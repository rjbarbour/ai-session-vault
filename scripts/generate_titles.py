"""Enrich exported sessions with AI-generated titles, summaries, and keywords.

Reads exported Markdown files from the vault, finds sessions with
title_source: first_message (or all sessions with --all), samples
turns from across the whole conversation, and asks Claude (via CLI)
for a title, short summary, long summary, and keywords.

Usage:
    python3 scripts/generate_titles.py [--vault PATH] [--dry-run] [--all]

Requires the `claude` CLI to be installed and authenticated.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    from export_sessions_to_obsidian import load_config, slugify
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from export_sessions_to_obsidian import load_config, slugify


def parse_frontmatter(text):
    """Extract frontmatter as a dict and return (frontmatter_dict, body)."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.index("---", 4)
    fm_text = text[4:end]
    body = text[end + 4:]
    fm = {}
    for line in fm_text.strip().split("\n"):
        key, _, val = line.partition(": ")
        fm[key.strip()] = val.strip().strip('"')
    return fm, body


def extract_turns(body):
    """Extract all user/assistant turns from the markdown body."""
    turns = []
    current_role = None
    current_text = []

    for line in body.split("\n"):
        if line.startswith("## User (turn") or line.startswith("## Assistant (turn"):
            if current_role and current_text:
                text = "\n".join(current_text).strip()
                if text:
                    turns.append((current_role, text))
            current_role = "User" if "User" in line else "Assistant"
            current_text = []
        elif current_role:
            current_text.append(line)

    if current_role and current_text:
        text = "\n".join(current_text).strip()
        if text:
            turns.append((current_role, text))

    return turns


ENRICHMENT_SYSTEM_PROMPT = """\
You enrich AI coding session exports with metadata. Given the full markdown \
of a session conversation, return a JSON object with exactly these keys:

- "title": 3-7 word descriptive title capturing the main activity of the whole session
- "summary_short": 2-5 sentence summary covering what was done and the key outcomes
- "summary_long": 10-15 sentence structured summary with markdown bullet points \
organized into logical sections (e.g. context, what was done, outcomes, open items). \
Use "\\n" for newlines and "\\n- " for bullets within the JSON string.
- "keywords": comma-separated list of 5-10 relevant keywords for search

The title should reflect the entire session, not just the beginning. \
Return ONLY valid JSON, no markdown fences, no explanation."""


def enrich_session(md_content):
    """Call Claude CLI with the full session markdown to generate metadata."""
    prompt = "Enrich this session:\n\n" + md_content
    try:
        result = subprocess.run(
            ["claude", "--model", "haiku", "-p",
             "--system-prompt", ENRICHMENT_SYSTEM_PROMPT, prompt],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return None
        output = result.stdout.strip()
        # Strip markdown fences if present
        output = re.sub(r"^```json\s*", "", output)
        output = re.sub(r"\s*```$", "", output)
        data = json.loads(output)
        if "title" in data and "summary_short" in data:
            return data
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    return None


def update_file(md_path, enrichment, dry_run=False):
    """Update the session file with enrichment data and rename if needed."""
    text = md_path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)

    new_title = enrichment["title"].strip('"\'').rstrip(".")
    summary_short = enrichment.get("summary_short", "")
    summary_long = enrichment.get("summary_long", "")
    keywords = enrichment.get("keywords", "")

    old_title = fm.get("title", "")
    old_slug = slugify(old_title)
    new_slug = slugify(new_title)

    if dry_run:
        print(f"  Would update: {md_path.name}")
        print(f"    title: {old_title} -> {new_title}")
        print(f"    summary: {summary_short}")
        print(f"    keywords: {keywords}")
        return

    # Update frontmatter fields
    text = text.replace(f'title: "{old_title}"', f'title: "{new_title}"')
    text = text.replace("title_source: first_message", "title_source: generated")

    # Escape summaries for YAML: replace real newlines with literal \n
    summary_short_escaped = summary_short.replace("\n", "\\n")
    summary_long_escaped = summary_long.replace("\n", "\\n")

    # Insert/update summary and keywords after the tags line in frontmatter
    tags_line = next(l for l in text.split("\n") if l.startswith("tags:"))

    # Remove old summary/keywords lines if re-enriching
    lines = text.split("\n")
    lines = [l for l in lines if not l.startswith(("summary_short:", "summary_long:", "keywords:"))]
    text = "\n".join(lines)

    # Re-find tags line after cleanup
    tags_line = next(l for l in text.split("\n") if l.startswith("tags:"))
    insertion = ""
    if summary_short:
        insertion += f"\nsummary_short: \"{summary_short_escaped}\""
    if summary_long:
        insertion += f"\nsummary_long: \"{summary_long_escaped}\""
    if keywords:
        insertion += f"\nkeywords: \"{keywords}\""
    text = text.replace(tags_line, tags_line + insertion, 1)

    # Update the markdown heading
    text = text.replace(f"# {old_title}", f"# {new_title}", 1)

    # Compute new filename
    new_name = md_path.name.replace(old_slug, new_slug)
    new_path = md_path.parent / new_name

    md_path.write_text(text, encoding="utf-8")
    if new_name != md_path.name:
        md_path.rename(new_path)

    print(f"  Updated: {new_name}")
    print(f"    title: {new_title}")
    print(f"    summary: {summary_short}")
    print(f"    keywords: {keywords}")


def main():
    cfg = load_config()

    parser = argparse.ArgumentParser(
        description="Enrich exported sessions with AI-generated titles, summaries, and keywords"
    )
    parser.add_argument("--vault", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without modifying files")
    parser.add_argument("--all", action="store_true",
                        help="Enrich all sessions, not just those with first_message titles")
    args = parser.parse_args()

    vault = args.vault or Path(cfg["vault_path"])

    candidates = []
    for md_file in sorted(vault.glob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(text)
        if args.all:
            if fm.get("title_source") and fm.get("source"):
                candidates.append(md_file)
        else:
            if fm.get("title_source") == "first_message":
                candidates.append(md_file)

    if not candidates:
        print("No sessions found to enrich.")
        return

    print(f"Found {len(candidates)} session(s) to enrich:")
    print()

    for md_file in candidates:
        text = md_file.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)
        turns = extract_turns(body)

        if not turns:
            print(f"  SKIP (no turns): {md_file.name}")
            continue

        print(f"  Processing: {md_file.name} ({len(turns)} turns)")
        print(f"    Current title: {fm.get('title', '?')}")

        enrichment = enrich_session(text)
        if enrichment:
            update_file(md_file, enrichment, dry_run=args.dry_run)
        else:
            print(f"    SKIP (enrichment failed)")
        print()


if __name__ == "__main__":
    main()
