"""Pre-flight checks and guided setup for ai-session-vault.

Verifies all prerequisites, checks permissions, and creates configuration.
Run this before first use.

Usage:
    python3 scripts/setup.py
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def check(label, ok, fix=None):
    """Print a check result. Returns True if passed."""
    if ok:
        print(f"  ✅ {label}")
        return True
    else:
        print(f"  ❌ {label}")
        if fix:
            print(f"     Fix: {fix}")
        return False


def check_python():
    """Verify Python version."""
    v = sys.version_info
    return check(
        f"Python {v.major}.{v.minor}.{v.micro}",
        v >= (3, 8),
        "Python 3.8+ required. Install from python.org or homebrew."
    )


def check_pytest():
    """Verify pytest is available."""
    try:
        import pytest
        return check(f"pytest {pytest.__version__}", True)
    except ImportError:
        return check(
            "pytest",
            False,
            "pip install pytest (or: python3 -m venv .venv && source .venv/bin/activate && pip install pytest)"
        )


def check_claude_cli():
    """Verify Claude CLI is installed, accessible, and authenticated."""
    claude_path = shutil.which("claude")
    if not claude_path:
        return check(
            "Claude CLI",
            False,
            "Install Claude Code CLI: https://docs.anthropic.com/en/docs/claude-code"
        ), None

    # Check version
    try:
        result = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=10
        )
        version = result.stdout.strip() if result.returncode == 0 else "unknown"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return check("Claude CLI", False, "claude --version failed"), None

    # Check auth
    try:
        result = subprocess.run(
            ["claude", "--model", "haiku", "-p", "--bare", "say ok"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0 and result.stdout.strip():
            check(f"Claude CLI {version} (authenticated)", True)
            return True, version
        else:
            # --bare might require API key; try without
            result = subprocess.run(
                ["claude", "--model", "haiku", "-p", "say ok"],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0 and result.stdout.strip():
                check(f"Claude CLI {version} (authenticated via OAuth)", True)
                return True, version

        check(
            f"Claude CLI {version} (not authenticated)",
            False,
            "Run: claude /login"
        )
        return False, version
    except (subprocess.TimeoutExpired, FileNotFoundError):
        check(f"Claude CLI {version} (auth check failed)", False, "Run: claude /login")
        return False, version


def check_system_prompt_flag(version):
    """Verify --system-prompt flag is available (needed for enrichment)."""
    try:
        result = subprocess.run(
            ["claude", "-p", "--system-prompt", "Reply with just OK", "test"],
            capture_output=True, text=True, timeout=15
        )
        return check(
            "Claude CLI --system-prompt flag",
            result.returncode == 0,
            f"Claude CLI version may be too old ({version}). Update: claude update"
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return check("Claude CLI --system-prompt flag", False, "Update Claude CLI")


def check_vault(cfg):
    """Check vault directory exists and is writable."""
    vault = Path(cfg.get("vault_path", "~/obsidian-session-vault")).expanduser()
    if vault.exists():
        writable = os.access(str(vault), os.W_OK)
        return check(
            f"Vault directory: {vault}",
            writable,
            f"Vault exists but not writable. Check permissions on {vault}"
        )
    else:
        print(f"  ⚠️  Vault directory does not exist: {vault}")
        create = input(f"     Create it? [Y/n] ").strip().lower()
        if create in ("", "y", "yes"):
            vault.mkdir(parents=True, exist_ok=True)
            return check(f"Vault directory created: {vault}", True)
        else:
            return check("Vault directory", False, f"mkdir -p {vault}")


def check_session_sources(home):
    """Check what session sources exist for the current user."""
    sources = {
        "Claude CLI": Path(home) / ".claude" / "projects",
        "Codex": Path(home) / ".codex" / "sessions",
        "Desktop": Path(home) / "Library" / "Application Support" / "Claude" / "claude-code-sessions",
        "Co-work": Path(home) / "Library" / "Application Support" / "Claude" / "local-agent-mode-sessions",
    }

    found_any = False
    for name, path in sources.items():
        if path.exists() and path.is_dir():
            try:
                items = list(path.iterdir())
                count = len(items)
                check(f"{name}: {path} ({count} items)", True)
                found_any = True
            except PermissionError:
                check(f"{name}: {path}", False, "Permission denied — check ACLs")
        else:
            print(f"  ➖ {name}: not found (OK if you don't use {name})")

    return found_any


def check_cross_account_access(cfg, home):
    """Check access to other accounts' session data."""
    accounts = cfg.get("accounts", [])
    if not accounts:
        print("  ➖ No other accounts configured (single-account mode)")
        return True

    all_ok = True
    for account in accounts:
        account_home = f"/Users/{account}"
        if not os.path.isdir(account_home):
            check(f"{account}: home directory", False, f"/Users/{account} does not exist")
            all_ok = False
            continue

        # Check traverse
        can_traverse = os.access(account_home, os.X_OK)
        if not can_traverse:
            check(
                f"{account}: home directory traverse",
                False,
                f"Run: sudo bash scripts/apply_cross_account_acls.sh"
            )
            all_ok = False
            continue

        # Check .claude access
        claude_dir = os.path.join(account_home, ".claude")
        if os.path.isdir(claude_dir):
            try:
                os.listdir(claude_dir)
                check(f"{account}: .claude readable", True)
            except PermissionError:
                check(
                    f"{account}: .claude readable",
                    False,
                    "Run: sudo bash scripts/apply_cross_account_acls.sh"
                )
                all_ok = False
        else:
            print(f"  ➖ {account}: no .claude directory")

        # Check Library/Application Support/Claude
        app_support = os.path.join(account_home, "Library", "Application Support", "Claude")
        if os.path.isdir(app_support):
            try:
                os.listdir(app_support)
                check(f"{account}: Application Support/Claude readable", True)
            except PermissionError:
                check(
                    f"{account}: Application Support/Claude readable",
                    False,
                    "Run: sudo bash scripts/apply_cross_account_acls.sh\n"
                    "     Also ensure ~/Library and ~/Library/Application Support are traversable"
                )
                all_ok = False

        # Check Documents (TCC-protected)
        documents = os.path.join(account_home, "Documents")
        if os.path.isdir(documents):
            try:
                os.listdir(documents)
                check(f"{account}: Documents readable", True)
            except PermissionError:
                check(
                    f"{account}: Documents readable",
                    False,
                    "Documents is TCC-protected. Grant Full Disk Access:\n"
                    "     System Settings > Privacy & Security > Full Disk Access > add Terminal\n"
                    "     Then restart Terminal"
                )
                all_ok = False

    return all_ok


def check_config():
    """Check for config.json, offer to create from example."""
    config_path = Path(__file__).resolve().parent.parent / "config.json"
    example_path = Path(__file__).resolve().parent.parent / "config.example.json"

    if config_path.exists():
        with open(config_path) as f:
            cfg = json.load(f)
        check(f"config.json found ({len(cfg)} keys)", True)
        return cfg

    print(f"  ⚠️  config.json not found")
    if example_path.exists():
        create = input("     Create from config.example.json? [Y/n] ").strip().lower()
        if create in ("", "y", "yes"):
            import shutil
            shutil.copy(example_path, config_path)
            with open(config_path) as f:
                cfg = json.load(f)
            check("config.json created from example", True)
            return cfg

    # Return defaults
    return {
        "vault_path": str(Path.home() / "obsidian-session-vault"),
        "claude_projects": [str(Path.home() / ".claude" / "projects")],
        "codex_sessions": str(Path.home() / ".codex" / "sessions"),
        "accounts": [],
    }


def main():
    print("ai-session-vault setup")
    print("=" * 50)
    print()

    home = os.path.expanduser("~")
    all_ok = True

    # 1. Python and dependencies
    print("1. Python environment")
    all_ok &= check_python()
    all_ok &= check_pytest()
    print()

    # 2. Claude CLI (optional but recommended)
    print("2. Claude CLI (required for enrichment)")
    cli_ok, cli_version = check_claude_cli()
    if cli_ok and cli_version:
        check_system_prompt_flag(cli_version)
    enrichment_available = cli_ok
    print()

    # 3. Configuration
    print("3. Configuration")
    cfg = check_config()
    print()

    # 4. Vault
    print("4. Vault directory")
    all_ok &= check_vault(cfg)
    print()

    # 5. Session sources
    print("5. Session sources (current user)")
    sources_ok = check_session_sources(home)
    if not sources_ok:
        print("  ⚠️  No session sources found. Have you used Claude Code or Codex?")
    print()

    # 6. Cross-account access
    print("6. Cross-account access")
    cross_ok = check_cross_account_access(cfg, home)
    print()

    # Summary
    print("=" * 50)
    print("Summary")
    print("=" * 50)
    print()

    if all_ok and sources_ok:
        print("Ready to export. Run:")
        print()
        if enrichment_available:
            print("  python3 scripts/export_all.py")
        else:
            print("  python3 scripts/export_all.py --skip-enrich")
            print()
            print("  (Enrichment unavailable — install and authenticate Claude CLI")
            print("   to get AI-generated titles, summaries, and keywords)")
    else:
        print("Some checks failed. Fix the issues above and re-run:")
        print()
        print("  python3 scripts/setup.py")

    if not cross_ok:
        print()
        print("Cross-account access issues detected. To fix:")
        print("  1. sudo bash scripts/apply_cross_account_acls.sh")
        print("  2. Grant Full Disk Access to Terminal (for ~/Documents)")
        print("  3. Restart Terminal")

    print()


if __name__ == "__main__":
    main()
