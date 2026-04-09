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
import os
import re
import subprocess
import sys
import tempfile
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
    end = text.find("---", 4)
    if end == -1:
        return {}, text
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


def check_claude_available():
    """Verify Claude CLI is installed and can respond. Returns (ok, reason)."""
    import shutil
    if not shutil.which("claude"):
        return False, "claude CLI not found in PATH"
    try:
        result = subprocess.run(
            ["claude", "--model", "haiku", "-p",
             "--system-prompt", "Reply with just OK"],
            input="test", capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "login" in stderr.lower() or "auth" in stderr.lower():
                return False, "claude CLI not authenticated — run: claude /login"
            return False, f"claude CLI error: {stderr[:100]}"
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "claude CLI timed out"
    except FileNotFoundError:
        return False, "claude CLI not found"


def enrich_session(md_content):
    """Call Claude CLI with session markdown to generate metadata.

    Truncates oversized sessions to fit Haiku's context window.
    Uses --system-prompt to avoid CLAUDE.md contamination from the
    current working directory.
    """
    if len(md_content) > MAX_ENRICHMENT_CHARS:
        parts = md_content.split("---", 2)
        if len(parts) >= 3:
            body = truncate_for_enrichment(parts[2])
            md_content = "---" + parts[1] + "---" + body
        # If still too large after turn-based truncation, hard-truncate
        if len(md_content) > MAX_ENRICHMENT_CHARS:
            md_content = md_content[:MAX_ENRICHMENT_CHARS] + "\n\n*[Content truncated]*"

    prompt = "Enrich this session:\n\n" + md_content
    try:
        result = subprocess.run(
            ["claude", "--model", "haiku", "-p",
             "--system-prompt", ENRICHMENT_SYSTEM_PROMPT],
            input=prompt, capture_output=True, text=True, timeout=90,
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
    except subprocess.TimeoutExpired:
        return None
    except (FileNotFoundError, json.JSONDecodeError):
        return None
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
    # Haiku sometimes returns keywords as a list instead of a string
    if isinstance(keywords, list):
        keywords = ", ".join(str(k) for k in keywords)
    if isinstance(summary_short, list):
        summary_short = " ".join(str(s) for s in summary_short)
    if isinstance(summary_long, list):
        summary_long = "\n".join(str(s) for s in summary_long)

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

    # Rebuild frontmatter line by line — old enrichment fields are
    # skipped and re-added with new values (no string.replace needed)
    lines = text.split("\n")
    new_lines = []
    in_frontmatter = False
    frontmatter_done = False
    tags_found = False

    for line in lines:
        if line.strip() == "---" and not in_frontmatter:
            in_frontmatter = True
            new_lines.append(line)
            continue
        if line.strip() == "---" and in_frontmatter:
            # Insert enrichment fields before closing ---
            if not tags_found:
                new_lines.append(f"tags: [session]")
            new_lines.append(f"original_title: \"{old_title_yaml}\"")
            new_lines.append(f"haiku_title: \"{haiku_title_yaml}\"")
            if summary_short:
                new_lines.append(f"summary_short: \"{summary_short_escaped}\"")
            if summary_long:
                new_lines.append(f"summary_long: \"{summary_long_escaped}\"")
            if keywords:
                new_lines.append(f"keywords: \"{keywords_escaped}\"")
            new_lines.append(line)
            in_frontmatter = False
            frontmatter_done = True
            continue

        if in_frontmatter:
            key = line.split(":")[0].strip() if ":" in line else ""
            # Skip old enrichment fields (will be re-added above)
            if key in ("summary_short", "summary_long", "keywords",
                       "original_title", "haiku_title"):
                continue
            # Update title
            if key == "title":
                new_lines.append(f"title: \"{new_title_yaml}\"")
                continue
            # Update title_source
            if key == "title_source":
                new_source = "generated" if replace else title_source
                new_lines.append(f"title_source: {new_source}")
                continue
            if key == "tags":
                tags_found = True
            new_lines.append(line)
        elif frontmatter_done and line.startswith("# ") and not line.startswith("## "):
            # Update the markdown heading
            new_lines.append(f"# {new_title}")
            frontmatter_done = False  # Only replace first heading
        else:
            new_lines.append(line)

    updated_text = "\n".join(new_lines)

    # Compute new filename
    if replace:
        old_slug = slugify(old_title)
        new_slug = slugify(new_title)
        new_name = md_path.name.replace(old_slug, new_slug)
    else:
        new_name = md_path.name
    new_path = md_path.parent / new_name

    # Atomic write: write to temp file then rename
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(md_path.parent), suffix=".md.tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp_f:
            tmp_f.write(updated_text)
        # Replace the original (or new name) atomically
        Path(tmp_path).rename(new_path)
        # Remove old file if renamed
        if new_name != md_path.name and md_path.exists():
            md_path.unlink()
    except Exception:
        # Clean up temp file on failure
        Path(tmp_path).unlink(missing_ok=True)
        raise

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

    # Pre-flight: verify Claude CLI before processing anything
    if not args.dry_run:
        ok, reason = check_claude_available()
        if not ok:
            print(f"Error: {reason}", file=sys.stderr)
            print("Enrichment requires Claude CLI. Run: python3 scripts/setup.py",
                  file=sys.stderr)
            sys.exit(1)

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
