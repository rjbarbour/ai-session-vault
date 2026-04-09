"""Export, enrich, dedupe, and audit all sessions in one command.

Zero-config for single-account use. Reads config.json for multi-account
and custom paths. Gracefully skips enrichment if claude CLI is unavailable.

Usage:
    python3 scripts/export_all.py                    # default: current user
    python3 scripts/export_all.py --skip-enrich      # export only, no Haiku
    python3 scripts/export_all.py --workers 5        # control enrichment parallelism
    python3 scripts/export_all.py --audit-only       # just run audits, no export
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = str(Path(__file__).resolve().parent)
sys.path.insert(0, SCRIPT_DIR)
from export_sessions_to_obsidian import load_config


def check_claude_cli():
    """Check if the claude CLI is installed and authenticated."""
    if not shutil.which("claude"):
        return False, "claude CLI not found"
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return False, "claude CLI not working"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, "claude CLI not responding"
    return True, ""


def get_accounts(cfg):
    """Get list of accounts to process: current user + any configured extras."""
    current = os.environ.get("USER", "unknown")
    accounts = [current]
    for account in cfg.get("accounts", []):
        if account != current and account not in accounts:
            accounts.append(account)
    return accounts


def run_export(account, is_current_user):
    """Run the export script for one account."""
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, "export_sessions_to_obsidian.py")]
    if not is_current_user:
        cmd.extend(["--account", account])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return result


def run_enrich(workers, skip_enriched=True):
    """Run the enrichment script."""
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, "generate_titles.py"), "--workers", str(workers)]
    if skip_enriched:
        cmd.append("--skip-enriched")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return result


def run_dedupe():
    """Run the deduplication script."""
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPT_DIR, "dedupe_vault.py")],
        capture_output=True, text=True, timeout=120,
    )
    return result


def run_audit(account, is_current_user, output_dir=None):
    """Run the audit script for one account."""
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, "audit_sessions.py"), "--account", account]
    if output_dir:
        output_file = os.path.join(
            output_dir,
            f"audit_{account}_{datetime.now().strftime('%Y-%m-%d')}.md"
        )
        cmd.extend(["--output", output_file])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return result


def main():
    cfg = load_config()

    parser = argparse.ArgumentParser(
        description="Export, enrich, dedupe, and audit all sessions"
    )
    parser.add_argument("--skip-enrich", action="store_true",
                        help="Skip Haiku enrichment (export and dedupe only)")
    parser.add_argument("--audit-only", action="store_true",
                        help="Only run audits, no export or enrichment")
    parser.add_argument("--workers", type=int, default=10,
                        help="Parallel workers for enrichment (default: 10)")
    parser.add_argument("--save-audits", action="store_true",
                        help="Save audit reports to files (default: print summary)")
    args = parser.parse_args()

    current_user = os.environ.get("USER", "unknown")
    accounts = get_accounts(cfg)
    vault = cfg["vault_path"]

    print(f"ai-session-vault — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Vault: {vault}")
    print(f"Accounts: {', '.join(accounts)}")
    print()

    # Ensure vault exists
    vault_path = Path(vault)
    if not vault_path.exists():
        print(f"Vault directory not found: {vault}")
        print(f"Run: python3 scripts/setup.py")
        sys.exit(1)

    if not args.audit_only:
        # --- Export ---
        print("=" * 60)
        print("EXPORT")
        print("=" * 60)
        for account in accounts:
            is_current = account == current_user
            print(f"\n--- {account} ---")
            result = run_export(account, is_current)
            # Show first line (session count) and last line (exported count)
            lines = result.stdout.strip().split("\n")
            if lines:
                print(f"  {lines[0]}")
                if len(lines) > 1:
                    print(f"  {lines[-1]}")
            if result.stderr:
                for line in result.stderr.strip().split("\n"):
                    print(f"  WARNING: {line}")

        # --- Enrich ---
        if args.skip_enrich:
            print("\n(Enrichment skipped)")
        else:
            has_claude, reason = check_claude_cli()
            if has_claude:
                print()
                print("=" * 60)
                print(f"ENRICH (workers={args.workers})")
                print("=" * 60)
                result = run_enrich(args.workers)
                lines = result.stdout.strip().split("\n")
                if lines:
                    print(f"  {lines[0]}")
                # Count results
                kept = sum(1 for l in lines if "KEPT:" in l)
                replaced = sum(1 for l in lines if "REPLACED:" in l)
                skipped = sum(1 for l in lines if "SKIP" in l)
                print(f"  Results: {kept} kept, {replaced} replaced, {skipped} skipped")
                if result.stderr:
                    for line in result.stderr.strip().split("\n")[:5]:
                        print(f"  WARNING: {line}")
            else:
                print(f"\n(Enrichment skipped: {reason})")

        # --- Dedupe ---
        print()
        print("=" * 60)
        print("DEDUPE")
        print("=" * 60)
        result = run_dedupe()
        output = result.stdout.strip()
        if "No duplicates" in output:
            print("  No duplicates found.")
        else:
            lines = output.split("\n")
            if lines:
                print(f"  {lines[0]}")
                print(f"  {lines[-1]}")

    # --- Audit ---
    print()
    print("=" * 60)
    print("AUDIT")
    print("=" * 60)
    audit_dir = os.path.dirname(os.path.abspath(__file__)) + "/.."
    for account in accounts:
        is_current = account == current_user
        if args.save_audits:
            result = run_audit(account, is_current, output_dir=audit_dir)
            print(f"  {account}: saved to audit_{account}_{datetime.now().strftime('%Y-%m-%d')}.md")
        else:
            result = run_audit(account, is_current)
        # Extract summary from output
        output = result.stdout if not args.save_audits else result.stdout
        summary_lines = []
        in_summary = False
        for line in output.split("\n"):
            if line.startswith("## Summary"):
                in_summary = True
                continue
            if in_summary:
                if line.startswith("##") or line.startswith("Vault breakdown"):
                    break
                if line.strip().startswith("- **"):
                    summary_lines.append(line.strip())

        print(f"\n  {account}:")
        for sl in summary_lines:
            print(f"    {sl}")

        # Show gaps
        in_gaps = False
        gap_lines = []
        for line in output.split("\n"):
            if line.startswith("## Gaps"):
                in_gaps = True
                continue
            if in_gaps:
                if line.startswith("##"):
                    break
                if line.strip().startswith("- "):
                    gap_lines.append(line.strip())
                elif line.strip() == "No gaps — all found sessions are in the vault.":
                    gap_lines.append("✅ No gaps")

        if gap_lines:
            for gl in gap_lines:
                print(f"    {gl}")

        if result.stderr:
            for line in result.stderr.strip().split("\n")[:3]:
                print(f"    WARNING: {line}")

    # Final count
    total_files = len(list(Path(vault).glob("*.md")))
    print()
    print("=" * 60)
    print(f"DONE — {total_files} sessions in vault")
    print("=" * 60)


if __name__ == "__main__":
    main()
