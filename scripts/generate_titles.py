"""Enrich exported sessions with AI-generated titles, summaries, and keywords.

Reads exported Markdown files from the vault, sends the full session
to Claude Haiku for enrichment. Always generates summaries and keywords.
Uses judgment to decide whether to keep the original title or substitute
the Haiku-generated one.

Usage:
    python3 scripts/generate_titles.py [--vault PATH] [--dry-run]

Requires the `claude` CLI to be installed and authenticated.
"""
import argparse
import json
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
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

- "title": 3-7 word descriptive title capturing the main activity of the whole session. \
The title should reflect the entire session, not just the beginning.
- "replace_title": true or false — whether your generated title is better than the \
original. The original title and its source are in the YAML frontmatter. Consider: \
a human-set custom title is usually intentional and should be kept unless it's clearly \
wrong or misleading. An auto-generated or first-message-derived title is often generic \
and should usually be replaced. Use your judgment.
- "summary_short": 2-5 sentence summary covering what was done and the key outcomes
- "summary_long": 10-15 sentence structured summary with markdown bullet points \
organized into logical sections (e.g. context, what was done, outcomes, open items). \
Use "\\n" for newlines and "\\n- " for bullets within the JSON string.
- "keywords": comma-separated list of 5-10 relevant keywords for search

Return ONLY valid JSON, no markdown fences, no explanation."""


MAX_ENRICHMENT_CHARS = 150000


def truncate_for_enrichment(body):
    """If body exceeds Haiku's context, keep first 20 + last 20 turns."""
    lines = body.split("\n")
    turn_indices = [i for i, l in enumerate(lines)
                    if l.startswith("## User (turn") or l.startswith("## Assistant (turn")]

    if len(turn_indices) <= 40:
        return body

    cut_start = turn_indices[20]
    cut_end = turn_indices[-20]
    kept = (lines[:cut_start]
            + [f"\n*[... {len(turn_indices) - 40} turns omitted for context limits ...]*\n"]
            + lines[cut_end:])
    return "\n".join(kept)


def enrich_session(md_content):
    """Call Claude CLI with session markdown to generate metadata.

    Truncates oversized sessions to fit Haiku's context window.
    """
    if len(md_content) > MAX_ENRICHMENT_CHARS:
        parts = md_content.split("---", 2)
        if len(parts) >= 3:
            body = truncate_for_enrichment(parts[2])
            md_content = "---" + parts[1] + "---" + body

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
    """Update the session file with enrichment data.

    Always adds summaries and keywords. Keeps original title in frontmatter.
    Uses judgment to decide whether to replace the display title.
    """
    text = md_path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)

    haiku_title = enrichment["title"].strip('"\'').rstrip(".")
    summary_short = enrichment.get("summary_short", "")
    summary_long = enrichment.get("summary_long", "")
    keywords = enrichment.get("keywords", "")

    old_title = fm.get("title", "")
    title_source = fm.get("title_source", "")
    replace = bool(enrichment.get("replace_title", True))
    new_title = haiku_title if replace else old_title

    # Escape for YAML
    new_title_yaml = new_title.replace('"', "'")
    haiku_title_yaml = haiku_title.replace('"', "'")
    old_title_yaml = old_title.replace('"', "'")
    summary_short_escaped = summary_short.replace("\n", "\\n").replace('"', "'")
    summary_long_escaped = summary_long.replace("\n", "\\n").replace('"', "'")
    keywords_escaped = keywords.replace('"', "'")

    if dry_run:
        action = "REPLACE" if replace else "KEEP"
        print(f"  {md_path.name}", flush=True)
        print(f"    original: {old_title}", flush=True)
        print(f"    haiku:    {haiku_title}", flush=True)
        print(f"    decision: {action}", flush=True)
        print(f"    summary:  {summary_short[:100]}...", flush=True)
        print(f"    keywords: {keywords}", flush=True)
        return

    # Build updated frontmatter lines
    lines = text.split("\n")

    # Remove old enrichment fields if re-enriching
    lines = [l for l in lines if not l.startswith((
        "summary_short:", "summary_long:", "keywords:",
        "original_title:", "haiku_title:",
    ))]
    text = "\n".join(lines)

    # Update title and title_source
    text = text.replace(f'title: "{old_title}"', f'title: "{new_title_yaml}"')
    if replace and title_source != "generated":
        text = text.replace(f"title_source: {title_source}", "title_source: generated")

    # Insert enrichment fields after tags line
    tags_line = next(l for l in text.split("\n") if l.startswith("tags:"))
    insertion = ""
    insertion += f"\noriginal_title: \"{old_title_yaml}\""
    insertion += f"\nhaiku_title: \"{haiku_title_yaml}\""
    if summary_short:
        insertion += f"\nsummary_short: \"{summary_short_escaped}\""
    if summary_long:
        insertion += f"\nsummary_long: \"{summary_long_escaped}\""
    if keywords:
        insertion += f"\nkeywords: \"{keywords_escaped}\""
    text = text.replace(tags_line, tags_line + insertion, 1)

    # Update the markdown heading if title changed
    if replace:
        old_slug = slugify(old_title)
        new_slug = slugify(new_title)
        text = text.replace(f"# {old_title}", f"# {new_title}", 1)
        new_name = md_path.name.replace(old_slug, new_slug)
    else:
        new_name = md_path.name

    new_path = md_path.parent / new_name
    md_path.write_text(text, encoding="utf-8")
    if new_name != md_path.name:
        md_path.rename(new_path)

    action = "REPLACED" if replace else "KEPT"
    print(f"  {action}: {new_name}", flush=True)
    print(f"    title: {new_title}", flush=True)
    print(f"    summary: {summary_short[:120]}...", flush=True)
    print(f"    keywords: {keywords}", flush=True)


def main():
    cfg = load_config()

    parser = argparse.ArgumentParser(
        description="Enrich exported sessions with AI-generated titles, summaries, and keywords"
    )
    parser.add_argument("--vault", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without modifying files")
    parser.add_argument("--skip-enriched", action="store_true",
                        help="Skip sessions that already have summaries")
    parser.add_argument("--workers", type=int, default=10,
                        help="Number of parallel workers (default: 10)")
    args = parser.parse_args()

    vault = args.vault or Path(cfg["vault_path"])

    candidates = []
    for md_file in sorted(vault.glob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(text)
        if not fm.get("source"):
            continue  # not an exported session
        if args.skip_enriched and fm.get("summary_short"):
            continue
        candidates.append(md_file)

    if not candidates:
        print("No sessions found to enrich.")
        return

    total = len(candidates)
    print(f"Found {total} session(s) to enrich (workers={args.workers}):")
    print(flush=True)

    counter_lock = threading.Lock()
    completed_count = [0]

    def process_one(md_file):
        """Process a single file: enrich and return result for printing."""
        text = md_file.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(text)
        turns = extract_turns(body)

        if not turns:
            return md_file, "skip_no_turns", None

        enrichment = enrich_session(text)
        if not enrichment:
            return md_file, "skip_failed", len(turns)

        return md_file, enrichment, len(turns)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process_one, f): f for f in candidates}

        for future in as_completed(futures):
            md_file, result, turn_count = future.result()

            with counter_lock:
                completed_count[0] += 1
                n = completed_count[0]

            progress = f"[{n}/{total}]"

            if result == "skip_no_turns":
                print(f"  {progress} SKIP (no turns): {md_file.name}", flush=True)
            elif result == "skip_failed":
                print(f"  {progress} SKIP (failed): {md_file.name} ({turn_count} turns)", flush=True)
            else:
                update_file(md_file, result, dry_run=args.dry_run)
                # Print progress after update_file's own output
            print(flush=True)


if __name__ == "__main__":
    main()
