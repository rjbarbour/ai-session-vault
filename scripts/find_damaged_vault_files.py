"""Find vault files damaged by the pre-fix fence-truncation bug.

Before the fix in PR #20, per-message truncation could cut inside a
fenced code block, leaving an opening ``` with no closing fence.
Obsidian then renders everything from the cut to EOF as preformatted
code, swallowing subsequent turns and headings.

This script scans the vault for .md files with an odd number of
line-start triple-backticks — the signature of the bug — and prints
affected filenames to stdout.

Usage:
    python3 scripts/find_damaged_vault_files.py [--vault PATH] [--verbose]

Exit codes:
    0  no damaged files found
    1  one or more damaged files found (useful for scripting)
    2  vault directory not found

Repair options:
    python3 scripts/export_all.py --full
        Regenerate the entire vault cleanly.

    rm <file>  # then:
    python3 scripts/export_sessions_to_obsidian.py
        Delete individual damaged files and let the delta export
        re-create them.
"""
import argparse
import re
import sys
from pathlib import Path

try:
    from utils import load_config
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from utils import load_config


# Line-start triple-backtick. Matches both bare ``` and language-tagged ```python.
FENCE_LINE = re.compile(r"(?m)^```")

# The truncation marker produced by export_session — its presence lifts
# confidence that an odd-fence file is bug-damaged rather than naturally
# unbalanced from session content.
TRUNCATION_MARKER = re.compile(r"\*\[(Response|Message) truncated —")


def scan_file(path):
    """Return (fence_count, has_truncation_marker) for one .md file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return len(FENCE_LINE.findall(text)), bool(TRUNCATION_MARKER.search(text))


def main():
    ap = argparse.ArgumentParser(
        description="Find vault files with unbalanced code fences "
                    "(likely damaged by the pre-fix truncation bug)."
    )
    ap.add_argument("--vault", type=Path,
                    help="Vault directory (default: vault_path from config.json)")
    ap.add_argument("--verbose", action="store_true",
                    help="Include fence count and marker status per match")
    args = ap.parse_args()

    if args.vault:
        vault = args.vault.expanduser()
    else:
        cfg = load_config()
        vault_path = cfg.get("vault_path", "")
        if not vault_path:
            print("Error: vault_path missing from config.json. "
                  "Pass --vault <path> or set vault_path in config.",
                  file=sys.stderr)
            return 2
        vault = Path(vault_path).expanduser()

    if not vault.is_dir():
        print(f"Error: vault not found: {vault}", file=sys.stderr)
        return 2

    damaged = []
    for md in sorted(vault.glob("*.md")):
        fences, has_marker = scan_file(md)
        if fences % 2 == 1:
            damaged.append((md, fences, has_marker))

    for path, fences, has_marker in damaged:
        if args.verbose:
            signal = "truncation marker + odd fences" if has_marker \
                     else "odd fences, no marker"
            print(f"{path.name}\t{fences}\t{signal}")
        else:
            print(path.name)

    return 0 if not damaged else 1


if __name__ == "__main__":
    sys.exit(main())
