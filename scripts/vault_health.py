"""Check vault health and report inconsistencies.

Uses the manifest to compare JSONL sources against vault contents.
Reports duplicates, orphans, unenriched sessions, and stale exports.

Usage:
    python3 scripts/vault_health.py              # report only
    python3 scripts/vault_health.py --fix        # report and fix issues
"""
import argparse
import sys
from pathlib import Path

try:
    from utils import load_config
    from manifest import load_manifest, save_manifest, scan_vault, check_health
    from dedupe_vault import find_duplicates
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from utils import load_config
    from manifest import load_manifest, save_manifest, scan_vault, check_health
    from dedupe_vault import find_duplicates


def fix_duplicates(vault_dir, duplicates):
    """Move duplicate vault files to .deleted/, keeping the best."""
    deleted_dir = Path(vault_dir) / ".deleted"
    deleted_dir.mkdir(exist_ok=True)
    removed = 0

    # Use dedupe's scoring to pick keepers
    all_dupes = find_duplicates(str(vault_dir))
    for sid, entries in sorted(all_dupes.items()):
        keeper = entries[0]
        for r in entries[1:]:
            dest = deleted_dir / r["path"].name
            r["path"].rename(dest)
            removed += 1

    return removed


def flag_orphans(vault_dir, orphans):
    """Add source_status: orphan to frontmatter of orphaned vault files."""
    flagged = 0
    for session_id, filename in orphans:
        filepath = Path(vault_dir) / filename
        if not filepath.exists():
            continue
        text = filepath.read_text(encoding="utf-8")
        if "source_status: orphan" in text:
            continue  # already flagged
        # Insert after the tags line in frontmatter
        if "\ntags:" in text:
            text = text.replace("\ntags:", "\nsource_status: orphan\ntags:", 1)
            filepath.write_text(text, encoding="utf-8")
            flagged += 1
    return flagged


def main():
    cfg = load_config()

    parser = argparse.ArgumentParser(
        description="Check vault health and report inconsistencies"
    )
    parser.add_argument("--vault", type=Path, default=None)
    parser.add_argument("--fix", action="store_true",
                        help="Fix issues (move duplicates, flag orphans)")
    args = parser.parse_args()

    vault = str(args.vault or cfg["vault_path"])

    # Load manifest and scan vault
    manifest = load_manifest(vault)
    scan_vault(manifest, vault)

    # Check health
    health = check_health(manifest)

    # Also check for vault files that are on disk but not in manifest
    vault_path = Path(vault)
    vault_files = set(f.name for f in vault_path.glob("*.md"))
    manifest_files = set()
    for entry in manifest["sessions"].values():
        v = entry.get("vault", {})
        if v.get("filename"):
            manifest_files.add(v["filename"])

    # Check for files missing from disk
    missing_from_disk = []
    for session_id, entry in manifest["sessions"].items():
        v = entry.get("vault", {})
        if v.get("filename") and v["filename"] not in vault_files:
            missing_from_disk.append((session_id, v["filename"]))

    # Report
    total_files = len(vault_files)
    total_sessions = len(manifest["sessions"])
    dupes = health["duplicates"]
    orphans = health["orphans"]
    unenriched = health["unenriched"]
    stale = health["stale"]

    print(f"Vault Health Report")
    print(f"{'=' * 50}")
    print(f"Vault files: {total_files}")
    print(f"Manifest sessions: {total_sessions}")
    print()

    if not dupes and not orphans and not unenriched and not stale and not missing_from_disk:
        print("  ✅ No issues found")
    else:
        if dupes:
            print(f"  Duplicates: {len(dupes)} session(s) with multiple vault files")
            for sid, fnames in dupes[:5]:
                print(f"    {sid[:12]}...: {len(fnames)} files")
            if len(dupes) > 5:
                print(f"    ... and {len(dupes) - 5} more")
            print()

        if orphans:
            print(f"  Orphans: {len(orphans)} vault file(s) with no JSONL source")
            print(f"    (These are retained as historical records)")
            for sid, fname in orphans[:5]:
                print(f"    {fname}")
            if len(orphans) > 5:
                print(f"    ... and {len(orphans) - 5} more")
            print()

        if unenriched:
            print(f"  Unenriched: {len(unenriched)} session(s) without summaries")
            for sid, fname in unenriched[:5]:
                print(f"    {fname}")
            if len(unenriched) > 5:
                print(f"    ... and {len(unenriched) - 5} more")
            print()

        if stale:
            print(f"  Stale: {len(stale)} session(s) where source changed since export")
            for sid, fname, src_mt, vault_mt in stale[:5]:
                print(f"    {fname}")
            if len(stale) > 5:
                print(f"    ... and {len(stale) - 5} more")
            print()

        if missing_from_disk:
            print(f"  Missing from disk: {len(missing_from_disk)} manifest entries with no vault file")
            for sid, fname in missing_from_disk[:5]:
                print(f"    {fname}")
            if len(missing_from_disk) > 5:
                print(f"    ... and {len(missing_from_disk) - 5} more")
            print()

    # Fix
    if args.fix:
        print()
        print("Fixing issues...")

        if dupes:
            removed = fix_duplicates(vault, dupes)
            print(f"  Moved {removed} duplicate files to .deleted/")

        if orphans:
            flagged = flag_orphans(vault, orphans)
            print(f"  Flagged {flagged} orphan files with source_status: orphan")

        if stale:
            print(f"  {len(stale)} stale sessions will be re-exported on next run")

        # Rescan vault and save manifest
        scan_vault(manifest, vault)
        save_manifest(vault, manifest)
        print("  Manifest updated")


if __name__ == "__main__":
    main()
