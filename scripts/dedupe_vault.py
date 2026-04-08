"""Detect and remove duplicate sessions in the Obsidian vault.

When enrichment renames a file (new title → new slug → new filename),
the old file may remain. This script groups vault files by session_id,
scores each duplicate, keeps the best one, and removes the rest.

Usage:
    python3 scripts/dedupe_vault.py [--vault PATH] [--dry-run]
"""
import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from export_sessions_to_obsidian import load_config


def parse_frontmatter(path):
    """Parse YAML frontmatter from a vault markdown file."""
    fm = {}
    with open(path) as f:
        first_line = f.readline()
        if first_line.strip() != "---":
            return fm
        for line in f:
            line = line.strip()
            if line == "---":
                break
            key, _, val = line.partition(": ")
            fm[key.strip()] = val.strip().strip('"')
    return fm


def score_session(fm, file_size):
    """Score a session file for quality. Higher is better.

    Scoring criteria (in priority order):
    1. Has summary (enriched) — strongly preferred
    2. Title source quality: generated > desktop > codex > custom > first_message
    3. Has keywords
    4. Larger file size (more content retained)
    """
    score = 0

    # Enriched with summary (+100)
    if fm.get("summary_short"):
        score += 100

    # Title source quality (+10-50)
    title_source_scores = {
        "generated": 50,
        "desktop": 40,
        "codex": 40,
        "custom": 30,
        "first_message": 10,
    }
    score += title_source_scores.get(fm.get("title_source", ""), 0)

    # Has keywords (+10)
    if fm.get("keywords"):
        score += 10

    # File size tiebreaker (+0-5 based on size bucket)
    if file_size > 50000:
        score += 5
    elif file_size > 10000:
        score += 3
    elif file_size > 1000:
        score += 1

    return score


def find_duplicates(vault_path):
    """Group vault files by session_id and identify duplicates."""
    sessions = defaultdict(list)

    for f in sorted(Path(vault_path).glob("*.md")):
        fm = parse_frontmatter(f)
        sid = fm.get("session_id", "")
        if not sid:
            continue
        sessions[sid].append({
            "path": f,
            "fm": fm,
            "score": score_session(fm, f.stat().st_size),
            "size": f.stat().st_size,
        })

    duplicates = {}
    for sid, entries in sessions.items():
        if len(entries) > 1:
            # Sort by score descending — first is the keeper
            entries.sort(key=lambda e: e["score"], reverse=True)
            duplicates[sid] = entries

    return duplicates


def main():
    cfg = load_config()

    parser = argparse.ArgumentParser(
        description="Detect and remove duplicate sessions in the vault"
    )
    parser.add_argument("--vault", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be removed without deleting")
    args = parser.parse_args()

    vault = args.vault or Path(cfg["vault_path"])
    duplicates = find_duplicates(str(vault))

    if not duplicates:
        print("No duplicates found.")
        return

    total_files = sum(len(entries) for entries in duplicates.values())
    to_remove = total_files - len(duplicates)

    print(f"Found {len(duplicates)} session(s) with duplicates ({total_files} files, {to_remove} to remove)")
    print()

    # Move deleted files to .deleted/ for recovery, not permanent deletion
    deleted_dir = vault / ".deleted"
    if not args.dry_run:
        deleted_dir.mkdir(exist_ok=True)

    removed = 0
    for sid, entries in sorted(duplicates.items()):
        keeper = entries[0]
        removes = entries[1:]

        keeper_title = keeper["fm"].get("title", "?")[:50]
        print(f"  {sid[:12]}... — {keeper_title}")
        print(f"    KEEP:   {keeper['path'].name} (score={keeper['score']})")
        for r in removes:
            print(f"    REMOVE: {r['path'].name} (score={r['score']})")
            if not args.dry_run:
                dest = deleted_dir / r["path"].name
                r["path"].rename(dest)
                removed += 1
        print()

    if args.dry_run:
        print(f"Dry run: would remove {to_remove} files")
    else:
        print(f"Moved {removed} duplicate files to {deleted_dir}")


if __name__ == "__main__":
    main()
