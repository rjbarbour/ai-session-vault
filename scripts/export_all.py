"""Delta refresh pipeline: export, dedupe, enrich, and audit all sessions.

Orchestrates the full pipeline using the manifest for delta detection.
Only processes new or changed sessions on subsequent runs.

Usage:
    python3 scripts/export_all.py                # delta refresh
    python3 scripts/export_all.py --full         # ignore manifest, re-export everything
    python3 scripts/export_all.py --skip-enrich  # export and dedupe only
    python3 scripts/export_all.py --audit-only   # just run audits
"""
import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = str(Path(__file__).resolve().parent)
sys.path.insert(0, SCRIPT_DIR)

from utils import load_config, resolve_account_paths, check_claude_cli
from export_sessions_to_obsidian import (
    export_session, find_session_files,
    load_desktop_titles, load_cowork_sessions, load_codex_titles,
    archive_vault_file,
)
from manifest import (
    load_manifest, save_manifest, scan_sources, scan_vault,
    compute_delta, update_after_export, check_health, quick_check_sources,
)


def _archive_if_compacted(vault_dir, old_vault_filename, manifest_entry):
    """Archive the old vault file if the JSONL shrank since last export (compaction).

    Uses sizes already in the manifest entry — no file reads needed.
    Returns the archive Path if compaction was detected, or None.
    """
    current_size = (manifest_entry.get("source") or {}).get("size")
    export_size = (manifest_entry.get("vault") or {}).get("source_size_at_export")

    if current_size is None or export_size is None:
        return None

    if current_size < export_size:
        return archive_vault_file(vault_dir, old_vault_filename)
    return None


def discover_all_sessions(accounts, cfg, manifest=None):
    """Discover session files for all accounts. Returns combined list.

    If manifest is provided, uses cached is_interactive status to skip
    reading known non-interactive JSONL files (stat-only for unchanged files).
    """
    all_sessions = []
    for account in accounts:
        is_current = account == os.environ.get("USER", "")
        home, claude_dirs, codex_sessions, desktop_dir, cowork_dir = \
            resolve_account_paths(account if not is_current else None, cfg)

        desktop_titles = load_desktop_titles(str(desktop_dir))
        cowork_titles, cowork_jsonl = load_cowork_sessions(str(cowork_dir))
        codex_titles = load_codex_titles(str(codex_sessions))

        exclude = cfg.get("exclude_projects", [])
        sessions = find_session_files(
            claude_dirs, codex_sessions,
            cowork_jsonl_files=cowork_jsonl,
            exclude_projects=exclude,
            manifest=manifest,
        )

        # Store title lookups for use during export
        for source_tag, jsonl_path in sessions:
            all_sessions.append({
                "source_tag": source_tag,
                "jsonl_path": jsonl_path,
                "account": account,
                "desktop_titles": desktop_titles,
                "codex_titles": codex_titles,
                "cowork_titles": cowork_titles,
            })

    return all_sessions


def run_enrich(workers, skip_enriched=True):
    """Run the enrichment script via subprocess (uses claude CLI)."""
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, "enrich_sessions.py"),
           "--workers", str(workers)]
    if skip_enriched:
        cmd.append("--skip-enriched")
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600)


def run_dedupe(vault):
    """Run deduplication."""
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, "dedupe_vault.py"),
           "--vault", str(vault)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def run_audit(account):
    """Run audit for one account."""
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, "audit_sessions.py"),
           "--account", account]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def main():
    cfg = load_config()

    parser = argparse.ArgumentParser(
        description="Delta refresh pipeline: export, enrich, dedupe, audit"
    )
    parser.add_argument("--skip-enrich", action="store_true",
                        help="Skip Haiku enrichment")
    parser.add_argument("--audit-only", action="store_true",
                        help="Only run audits")
    parser.add_argument("--workers", type=int, default=10,
                        help="Parallel workers for enrichment (default: 10)")
    parser.add_argument("--full", action="store_true",
                        help="Ignore manifest and re-export all sessions")
    parser.add_argument("--save-audits", action="store_true",
                        help="Save audit reports to files")
    args = parser.parse_args()

    current_user = os.environ.get("USER", "unknown")
    accounts = [current_user]
    for account in cfg.get("accounts", []):
        if account != current_user and account not in accounts:
            accounts.append(account)

    vault = Path(cfg["vault_path"])
    if not vault.exists():
        print(f"Vault directory not found: {vault}")
        print("Run: python3 scripts/setup.py")
        sys.exit(1)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"ai-session-vault — {now}")
    print(f"Vault: {vault}")
    print(f"Accounts: {', '.join(accounts)}")
    print()

    if args.audit_only:
        # Skip to audit
        print("=" * 50)
        print("AUDIT")
        print("=" * 50)
        for account in accounts:
            result = run_audit(account)
            _print_audit_summary(account, result.stdout)
        total = len(list(vault.glob("*.md")))
        print(f"\n{'=' * 50}")
        print(f"DONE — {total} sessions in vault")
        print("=" * 50)
        return

    # ================================================================
    # Step 1-3: Discover, scan sources, scan vault
    # ================================================================
    manifest = load_manifest(str(vault))

    # Fast path: if no known sources changed AND no unenriched sessions, skip
    has_unenriched = any(
        entry.get("vault", {}).get("enriched") is False
        for entry in manifest.get("sessions", {}).values()
        if entry.get("vault", {}).get("filename")
    )
    if (not args.full and manifest.get("sessions")
            and not quick_check_sources(manifest)
            and not has_unenriched):
        total = len(list(vault.glob("*.md")))
        print(f"No changes detected — {total} sessions in vault")
        print()
        save_manifest(str(vault), manifest)
        return

    print("=" * 50)
    print("DISCOVER")
    print("=" * 50)
    all_sessions = discover_all_sessions(accounts, cfg, manifest=manifest)
    print(f"  Found {len(all_sessions)} session files across {len(accounts)} account(s)")

    source_session_files = [(s["source_tag"], s["jsonl_path"]) for s in all_sessions]
    scan_sources(manifest, source_session_files)
    scan_vault(manifest, str(vault))
    print()

    # ================================================================
    # Step 4-5: Compute delta and export
    # ================================================================
    print("=" * 50)
    print("EXPORT")
    print("=" * 50)

    if args.full:
        to_process = all_sessions
        print(f"  Full export: {len(to_process)} sessions")
    else:
        delta = compute_delta(manifest)
        # Build session info for export items
        session_lookup = {str(s["jsonl_path"]): s for s in all_sessions}
        to_process = []
        for source_tag, jsonl_path in delta["to_export"]:
            info = session_lookup.get(str(jsonl_path))
            if info:
                to_process.append(info)
        for source_tag, jsonl_path, old_vault in delta["to_reexport"]:
            info = session_lookup.get(str(jsonl_path))
            if info:
                info["old_vault_file"] = old_vault
                to_process.append(info)

        print(f"  Delta: {len(delta['to_export'])} new, "
              f"{len(delta['to_reexport'])} changed, "
              f"{len(delta['skip'])} unchanged, "
              f"{len(delta['to_enrich'])} need enrichment")

    exported = 0
    for session in to_process:
        # Delete old vault file if re-exporting
        old_vault = session.get("old_vault_file")
        if old_vault:
            session_id = session["jsonl_path"].stem
            entry = manifest["sessions"].get(session_id, {})
            archived = _archive_if_compacted(vault, old_vault, entry)
            if archived:
                print(f"  [archive] Pre-compaction preserved: {archived.name}")
            old_path = vault / old_vault
            if old_path.exists():
                old_path.unlink()

        result = export_session(
            session["jsonl_path"], vault,
            source_tag=session["source_tag"],
            desktop_titles=session.get("desktop_titles"),
            codex_titles=session.get("codex_titles"),
            cowork_titles=session.get("cowork_titles"),
        )
        if result:
            print(f"  [{session['source_tag']:6s}] {result.name}")
            update_after_export(
                manifest, session["jsonl_path"].stem,
                session["source_tag"], session["jsonl_path"], result.name,
            )
            exported += 1

    print(f"  Exported {exported} session(s)")
    print()

    # ================================================================
    # Step 6: Dedupe
    # ================================================================
    print("=" * 50)
    print("DEDUPE")
    print("=" * 50)
    result = run_dedupe(vault)
    output = result.stdout.strip()
    if "No duplicates" in output:
        print("  No duplicates found.")
    else:
        lines = output.split("\n")
        print(f"  {lines[0]}")
        print(f"  {lines[-1]}")
    print()

    # ================================================================
    # Step 7: Enrich
    # ================================================================
    if args.skip_enrich:
        print("(Enrichment skipped)")
    else:
        has_claude, reason = check_claude_cli()
        if has_claude:
            print("=" * 50)
            print(f"ENRICH (workers={args.workers})")
            print("=" * 50)
            result = run_enrich(args.workers)
            lines = result.stdout.strip().split("\n")
            if lines:
                print(f"  {lines[0]}")
            kept = sum(1 for l in lines if "KEPT:" in l)
            replaced = sum(1 for l in lines if "REPLACED:" in l)
            skipped = sum(1 for l in lines if "SKIP" in l)
            print(f"  Results: {kept} kept, {replaced} replaced, {skipped} skipped")
        else:
            print(f"(Enrichment skipped: {reason})")
    print()

    # ================================================================
    # Steps 8-9: Rescan vault and health check
    # ================================================================
    print("=" * 50)
    print("HEALTH")
    print("=" * 50)
    scan_vault(manifest, str(vault))
    health = check_health(manifest)

    issues = (health["duplicates"] + health["orphans"] +
              health["unenriched"] + health["stale"])
    if not issues:
        print("  ✅ No issues")
    else:
        if health["duplicates"]:
            print(f"  Duplicates: {len(health['duplicates'])}")
        if health["orphans"]:
            print(f"  Orphans: {len(health['orphans'])} (retained as historical records)")
        if health["unenriched"]:
            print(f"  Unenriched: {len(health['unenriched'])}")
        if health["stale"]:
            print(f"  Stale: {len(health['stale'])}")
    print()

    # ================================================================
    # Step 10: Save manifest
    # ================================================================
    save_manifest(str(vault), manifest)

    # ================================================================
    # Step 11: Audit
    # ================================================================
    print("=" * 50)
    print("AUDIT")
    print("=" * 50)
    for account in accounts:
        result = run_audit(account)
        _print_audit_summary(account, result.stdout)

    total = len(list(vault.glob("*.md")))
    print()
    print("=" * 50)
    print(f"DONE — {total} sessions in vault")
    print("=" * 50)


def _print_audit_summary(account, output):
    """Extract and print the summary section from an audit report."""
    summary_lines = []
    gap_lines = []
    in_summary = False
    in_gaps = False

    for line in output.split("\n"):
        if line.startswith("## Summary"):
            in_summary = True
            continue
        if line.startswith("## Gaps"):
            in_summary = False
            in_gaps = True
            continue
        if line.startswith("##"):
            in_summary = False
            in_gaps = False
            continue
        if in_summary and line.strip().startswith("- **"):
            summary_lines.append(line.strip())
        if in_gaps:
            if line.strip().startswith("- "):
                gap_lines.append(line.strip())
            elif "No gaps" in line:
                gap_lines.append("✅ No gaps")

    print(f"\n  {account}:")
    for sl in summary_lines:
        print(f"    {sl}")
    for gl in gap_lines:
        print(f"    {gl}")


if __name__ == "__main__":
    main()
