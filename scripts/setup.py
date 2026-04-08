"""Pre-flight checks and guided setup for ai-session-vault.

Verifies all prerequisites, checks permissions, and creates configuration.
Run this before first use.

Usage:
    python3 scripts/setup.py
"""
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent


def check(label, ok, fix=None):
    """Print a check result. Returns True if passed."""
    if ok:
        print(f"  ✅ {label}")
        return True
    else:
        print(f"  ❌ {label}")
        if fix:
            for line in fix.split("\n"):
                print(f"     {line}")
        return False


def prompt_yn(question, default_yes=True):
    """Prompt for yes/no, return bool."""
    suffix = "[Y/n]" if default_yes else "[y/N]"
    answer = input(f"     {question} {suffix} ").strip().lower()
    if default_yes:
        return answer not in ("n", "no")
    return answer in ("y", "yes")


# -------------------------------------------------------------------------
# Step 1: Python
# -------------------------------------------------------------------------

def check_python():
    v = sys.version_info
    return check(
        f"Python {v.major}.{v.minor}.{v.micro}",
        v >= (3, 8),
        "Python 3.8+ required.\nInstall: https://www.python.org/downloads/ or brew install python3"
    )


def check_pytest():
    try:
        import pytest
        return check(f"pytest {pytest.__version__}", True)
    except ImportError:
        return check(
            "pytest (for running tests)",
            False,
            "Install: pip install pytest\n"
            "If blocked by PEP 668: python3 -m venv .venv && source .venv/bin/activate && pip install pytest"
        )


# -------------------------------------------------------------------------
# Step 2: Claude CLI
# -------------------------------------------------------------------------

def check_claude_cli():
    claude_path = shutil.which("claude")
    if not claude_path:
        check(
            "Claude CLI (optional — needed for AI-generated titles and summaries)",
            False,
            "Install: https://docs.anthropic.com/en/docs/claude-code\n"
            "Without it, sessions are exported but without enrichment."
        )
        return False, None

    try:
        result = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=10
        )
        version = result.stdout.strip() if result.returncode == 0 else "unknown"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        check("Claude CLI", False, "claude --version failed")
        return False, None

    # Check auth with a minimal call
    try:
        result = subprocess.run(
            ["claude", "--model", "haiku", "-p",
             "--system-prompt", "Reply with just OK", "test"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            check(f"Claude CLI {version} (authenticated, --system-prompt works)", True)
            return True, version
        else:
            stderr = result.stderr.strip()
            if "login" in stderr.lower() or "auth" in stderr.lower():
                check(f"Claude CLI {version} (not authenticated)", False,
                      "Run: claude /login")
            else:
                check(f"Claude CLI {version} (--system-prompt may not be supported)", False,
                      f"Update: claude update\nError: {stderr[:100]}")
            return False, version
    except subprocess.TimeoutExpired:
        check(f"Claude CLI {version} (auth check timed out)", False, "Run: claude /login")
        return False, version


# -------------------------------------------------------------------------
# Step 3: Obsidian
# -------------------------------------------------------------------------

def check_obsidian():
    system = platform.system()
    if system == "Darwin":
        app_path = Path("/Applications/Obsidian.app")
        if app_path.exists():
            return check("Obsidian installed", True)
        # Check user Applications
        user_app = Path.home() / "Applications" / "Obsidian.app"
        if user_app.exists():
            return check(f"Obsidian installed ({user_app})", True)
        return check(
            "Obsidian (optional — for browsing the vault)",
            False,
            "Download: https://obsidian.md/download\n"
            "The vault is plain Markdown — any text editor or grep works too.\n"
            "Recommended Obsidian plugins: Dataview (frontmatter queries), Smart Connections (semantic search)"
        )
    elif system == "Linux":
        if shutil.which("obsidian"):
            return check("Obsidian installed", True)
        return check("Obsidian (optional)", False,
                      "Download: https://obsidian.md/download")
    else:
        print("  ➖ Obsidian check not implemented for this OS")
        return True


# -------------------------------------------------------------------------
# Step 4: Configuration
# -------------------------------------------------------------------------

def check_config():
    config_path = PROJECT_DIR / "config.json"
    example_path = PROJECT_DIR / "config.example.json"

    if config_path.exists():
        with open(config_path) as f:
            cfg = json.load(f)
        check(f"config.json ({len(cfg)} settings)", True)
        return cfg

    print(f"  ⚠️  config.json not found")
    if example_path.exists() and prompt_yn("Create from config.example.json?"):
        shutil.copy(example_path, config_path)
        with open(config_path) as f:
            cfg = json.load(f)
        check("config.json created from example", True)
        print("     Edit config.json to set your vault path and other preferences.")
        return cfg

    return {
        "vault_path": str(Path.home() / "obsidian-session-vault"),
        "claude_projects": [str(Path.home() / ".claude" / "projects")],
        "codex_sessions": str(Path.home() / ".codex" / "sessions"),
        "accounts": [],
    }


# -------------------------------------------------------------------------
# Step 5: Vault
# -------------------------------------------------------------------------

def check_vault(cfg):
    vault = Path(cfg.get("vault_path", "~/obsidian-session-vault")).expanduser()
    if vault.exists():
        writable = os.access(str(vault), os.W_OK)
        return check(f"Vault: {vault}", writable,
                      f"Vault exists but not writable. Check permissions on {vault}")

    print(f"  ⚠️  Vault directory does not exist: {vault}")
    if prompt_yn("Create it?"):
        vault.mkdir(parents=True, exist_ok=True)
        return check(f"Vault created: {vault}", True)
    return check("Vault directory", False, f"mkdir -p {vault}")


# -------------------------------------------------------------------------
# Step 6: Session sources
# -------------------------------------------------------------------------

def check_session_sources(home):
    sources = [
        ("Claude CLI", Path(home) / ".claude" / "projects"),
        ("Codex", Path(home) / ".codex" / "sessions"),
        ("Desktop", Path(home) / "Library" / "Application Support" / "Claude" / "claude-code-sessions"),
        ("Co-work", Path(home) / "Library" / "Application Support" / "Claude" / "local-agent-mode-sessions"),
    ]

    found_any = False
    for name, path in sources:
        if path.exists() and path.is_dir():
            try:
                count = len(list(path.iterdir()))
                check(f"{name}: {count} items", True)
                found_any = True
            except PermissionError:
                check(f"{name}: permission denied", False, "Check ACLs")
        else:
            print(f"  ➖ {name}: not found (OK if you don't use it)")

    return found_any


# -------------------------------------------------------------------------
# Step 7: Cross-account access (only when configured)
# -------------------------------------------------------------------------

def check_cross_account(cfg):
    accounts = cfg.get("accounts", [])
    if not accounts:
        print("  ➖ Single-account mode (no other accounts in config.json)")
        print("     To add accounts: edit config.json, add \"accounts\": [\"otheraccount\"]")
        return True, False

    needs_acl = False
    needs_fda = False
    all_ok = True

    for account in accounts:
        account_home = f"/Users/{account}"
        if not os.path.isdir(account_home):
            check(f"{account}: home directory", False, f"/Users/{account} does not exist")
            all_ok = False
            continue

        # Check .claude access (the essential one)
        claude_dir = os.path.join(account_home, ".claude")
        if os.path.isdir(claude_dir):
            try:
                os.listdir(claude_dir)
                check(f"{account}: .claude readable", True)
            except PermissionError:
                check(f"{account}: .claude readable", False)
                needs_acl = True
                all_ok = False
        else:
            print(f"  ➖ {account}: no .claude directory (no Claude Code sessions)")

        # Check Application Support/Claude
        app_claude = os.path.join(account_home, "Library", "Application Support", "Claude")
        try:
            if os.path.isdir(app_claude):
                os.listdir(app_claude)
                check(f"{account}: Desktop/Co-work data readable", True)
        except PermissionError:
            check(f"{account}: Desktop/Co-work data", False, "Needs ACL + Library traverse")
            needs_acl = True
            all_ok = False

        # Check Documents (TCC-protected)
        documents = os.path.join(account_home, "Documents")
        try:
            if os.path.isdir(documents):
                os.listdir(documents)
                check(f"{account}: Documents readable", True)
        except PermissionError:
            check(f"{account}: Documents (for project audit)", False,
                  "Needs Full Disk Access for your terminal app")
            needs_fda = True
            # Not a blocker — sessions export without Documents access
            # all_ok stays True for this one

    # Offer to fix
    if needs_acl:
        print()
        print("  Some accounts need ACL permissions to read session data.")
        if prompt_yn("Run the ACL setup script now? (requires sudo password)"):
            result = subprocess.run(
                ["sudo", "bash", str(SCRIPT_DIR / "apply_cross_account_acls.sh")],
                timeout=60,
            )
            if result.returncode == 0:
                print("  ✅ ACLs applied. Re-run setup to verify.")
            else:
                print("  ❌ ACL script failed. Run manually:")
                print("     sudo bash scripts/apply_cross_account_acls.sh")

    if needs_fda:
        print()
        print("  To read other accounts' ~/Documents (for project directory auditing):")
        print("  1. Open System Settings > Privacy & Security > Full Disk Access")
        print("  2. Add your terminal app (Terminal, iTerm, or Claude Desktop)")
        print("  3. Restart the terminal")
        print("  Note: This is optional — session export works without it.")

    return all_ok, needs_acl or needs_fda


# -------------------------------------------------------------------------
# Main
# -------------------------------------------------------------------------

def main():
    print()
    print("ai-session-vault setup")
    print("=" * 50)
    print()

    home = os.path.expanduser("~")
    all_ok = True

    print("1. Python environment")
    all_ok &= check_python()
    check_pytest()  # Not a blocker
    print()

    print("2. Claude CLI")
    cli_ok, _ = check_claude_cli()
    print()

    print("3. Obsidian")
    check_obsidian()  # Not a blocker
    print()

    print("4. Configuration")
    cfg = check_config()
    print()

    print("5. Vault directory")
    all_ok &= check_vault(cfg)
    print()

    print("6. Session sources")
    sources_ok = check_session_sources(home)
    if not sources_ok:
        print("  ⚠️  No session sources found for current user.")
        print("     Have you used Claude Code, Codex, or Claude Desktop?")
    print()

    accounts = cfg.get("accounts", [])
    if accounts:
        print("7. Cross-account access")
        cross_ok, needs_fix = check_cross_account(cfg)
        print()
    else:
        cross_ok = True
        needs_fix = False

    # Summary
    print("=" * 50)
    print("Ready to go" if (all_ok and sources_ok) else "Setup needed")
    print("=" * 50)
    print()

    if all_ok and sources_ok:
        if cli_ok:
            print("  python3 scripts/export_all.py")
        else:
            print("  python3 scripts/export_all.py --skip-enrich")
            print()
            print("  Install Claude CLI for AI-generated titles and summaries.")
    else:
        print("  Fix the issues above, then re-run: python3 scripts/setup.py")

    print()


if __name__ == "__main__":
    main()
