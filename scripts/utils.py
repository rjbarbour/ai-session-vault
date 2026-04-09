"""Shared utilities for ai-session-vault scripts.

Contains functions used across multiple scripts to avoid duplication.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

GENERIC_DEFAULTS = {
    "vault_path": str(Path.home() / "obsidian-session-vault"),
    "claude_projects": [str(Path.home() / ".claude" / "projects")],
    "codex_sessions": str(Path.home() / ".codex" / "sessions"),
}


def load_config():
    """Load config.json if present, else return generic defaults.

    Expands ~ in all path values.
    """
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
    else:
        cfg = dict(GENERIC_DEFAULTS)

    cfg["vault_path"] = str(Path(cfg["vault_path"]).expanduser())
    cfg["claude_projects"] = [
        str(Path(p).expanduser())
        for p in cfg.get("claude_projects", GENERIC_DEFAULTS["claude_projects"])
    ]
    cfg["codex_sessions"] = str(
        Path(cfg.get("codex_sessions", GENERIC_DEFAULTS["codex_sessions"])).expanduser()
    )
    return cfg


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

def parse_frontmatter_file(md_path):
    """Parse YAML frontmatter from a markdown file. Returns dict."""
    fm = {}
    try:
        with open(md_path) as f:
            first_line = f.readline()
            if first_line.strip() != "---":
                return fm
            for line in f:
                line = line.strip()
                if line == "---":
                    break
                key, _, val = line.partition(": ")
                fm[key.strip()] = val.strip().strip('"')
    except (OSError, UnicodeDecodeError):
        pass
    return fm


def parse_frontmatter_text(text):
    """Parse YAML frontmatter from a markdown string. Returns (dict, body)."""
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


# ---------------------------------------------------------------------------
# Directory and path utilities
# ---------------------------------------------------------------------------

def check_dir(path, label=""):
    """Check if a directory exists and is readable.

    Returns True if accessible, False if not found, and prints a warning
    if the path exists but is not readable (permission denied).
    """
    p = Path(path)
    try:
        if not p.is_dir():
            return False
        list(p.iterdir())
        return True
    except PermissionError:
        print(f"WARNING: {label or path} — permission denied. "
              "Run: sudo bash scripts/apply_cross_account_acls.sh", file=sys.stderr)
        return False
    except OSError:
        return False


def resolve_vault(args_vault, cfg):
    """Resolve vault path from CLI args or config. Returns Path."""
    return Path(args_vault) if args_vault else Path(cfg["vault_path"])


def resolve_account_paths(account=None, cfg=None):
    """Resolve all session-related paths for a given account.

    Returns (home, claude_project_dirs, codex_sessions, desktop_dir, cowork_dir).
    """
    if cfg is None:
        cfg = load_config()

    if account:
        home = Path(f"/Users/{account}")
    else:
        home = Path.home()

    if account:
        claude_project_dirs = [home / ".claude" / "projects"]
        codex_sessions = home / ".codex" / "sessions"
    else:
        claude_project_dirs = [Path(p) for p in cfg["claude_projects"]]
        codex_sessions = Path(cfg["codex_sessions"])

    desktop_dir = home / "Library" / "Application Support" / "Claude" / "claude-code-sessions"
    cowork_dir = home / "Library" / "Application Support" / "Claude" / "local-agent-mode-sessions"

    return home, claude_project_dirs, codex_sessions, desktop_dir, cowork_dir


def extract_account(path_str):
    """Extract macOS account name from a path like /Users/rob_dev/..."""
    if "/Users/" in str(path_str):
        parts = str(path_str).split("/")
        try:
            idx = parts.index("Users")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        except ValueError:
            pass
    return ""


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------

def slugify(text, max_len=50):
    """Convert text to a filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    if len(text) > max_len:
        text = text[:max_len].rstrip("-")
    return text or "untitled"


# ---------------------------------------------------------------------------
# Claude CLI
# ---------------------------------------------------------------------------

def check_claude_cli():
    """Check if Claude CLI is installed and can respond.

    Returns (ok, reason).
    """
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


# ---------------------------------------------------------------------------
# Atomic file operations
# ---------------------------------------------------------------------------

def atomic_write(path, content, encoding="utf-8"):
    """Write content to a file atomically via temp file + rename."""
    path = Path(path)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding=encoding) as f:
            f.write(content)
        Path(tmp_path).rename(path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise
