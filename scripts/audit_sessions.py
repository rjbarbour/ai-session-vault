"""Audit session coverage across all sources and the Obsidian vault.

Scans project directories, Claude Code JSONL, Desktop metadata, Codex
sessions, Co-work sessions, and the vault to produce a comprehensive
markdown audit report.

Usage:
    python3 scripts/audit_sessions.py [--account ACCOUNT] [--output PATH]

Defaults to the current user. Output defaults to stdout.
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from export_sessions_to_obsidian import (
    load_config, load_desktop_titles, load_cowork_sessions, load_codex_titles,
    _is_interactive_session,
)


def scan_project_roots(home):
    """Find all project root directories and their children."""
    docs = os.path.join(home, "Documents")

    # Find TMD subfolder
    tmd_name = None
    if os.path.isdir(docs):
        for item in os.listdir(docs):
            if "TMD" in item or "MacBook" in item:
                full = os.path.join(docs, item)
                if os.path.isdir(full):
                    tmd_name = item
                    break

    roots = [
        ("~/DocsLocal", os.path.join(home, "DocsLocal")),
        ("~/Documents/GitHub", os.path.join(docs, "GitHub")),
    ]
    if tmd_name:
        tmd = os.path.join(docs, tmd_name)
        roots.append(("~/Documents/TMD/GitHub", os.path.join(tmd, "GitHub")))
        roots.append(("~/Documents/TMD/Projects", os.path.join(tmd, "Projects")))

    projects = []  # (root_label, name, full_path)
    for label, path in roots:
        if not os.path.isdir(path):
            continue
        for item in sorted(os.listdir(path)):
            full = os.path.join(path, item)
            if os.path.isdir(full) and not item.startswith("."):
                projects.append((label, item, full))

    return projects


def scan_cli_sessions(home):
    """Count CLI sessions by cwd from ~/.claude/projects/."""
    projects_root = os.path.join(home, ".claude", "projects")
    cwd_counts = defaultdict(int)
    if not os.path.isdir(projects_root):
        return cwd_counts

    for proj_dir in os.listdir(projects_root):
        proj_path = os.path.join(projects_root, proj_dir)
        if not os.path.isdir(proj_path):
            continue
        for f in os.listdir(proj_path):
            if not f.endswith(".jsonl"):
                continue
            full = os.path.join(proj_path, f)
            if not _is_interactive_session(Path(full)):
                continue
            # Get cwd
            with open(full, errors="replace") as fh:
                for line in fh:
                    try:
                        data = json.loads(line)
                        cwd = data.get("cwd", "")
                        if cwd:
                            cwd_counts[cwd] += 1
                            break
                    except (json.JSONDecodeError, KeyError):
                        pass
    return cwd_counts


def scan_desktop_sessions(home, desktop_dir=None):
    """Count Desktop sessions by cwd."""
    if desktop_dir is None:
        desktop_dir = os.path.join(
            home, "Library", "Application Support", "Claude", "claude-code-sessions"
        )
    cwd_counts = defaultdict(int)
    if not os.path.isdir(desktop_dir):
        return cwd_counts

    for root, dirs, files in os.walk(desktop_dir):
        for f in files:
            if not f.endswith(".json") or ".bak" in f:
                continue
            try:
                with open(os.path.join(root, f)) as fh:
                    data = json.load(fh)
                cwd = data.get("cwd", "")
                if cwd:
                    cwd_counts[cwd] += 1
            except (json.JSONDecodeError, OSError):
                pass
    return cwd_counts


def scan_codex_sessions(home):
    """Count Codex sessions by cwd from session_meta."""
    sessions_dir = os.path.join(home, ".codex", "sessions")
    cwd_counts = defaultdict(int)
    if not os.path.isdir(sessions_dir):
        return cwd_counts

    for root, dirs, files in os.walk(sessions_dir):
        for f in files:
            if not f.startswith("rollout-") or not f.endswith(".jsonl"):
                continue
            with open(os.path.join(root, f), errors="replace") as fh:
                for line in fh:
                    try:
                        data = json.loads(line)
                        if data.get("type") == "session_meta":
                            cwd = data.get("payload", {}).get("cwd", "")
                            if cwd:
                                cwd_counts[cwd] += 1
                            break
                    except (json.JSONDecodeError, KeyError):
                        pass
    return cwd_counts


def scan_cowork_sessions(home, cowork_dir=None):
    """Count Co-work sessions by cwd."""
    if cowork_dir is None:
        cowork_dir = os.path.join(
            home, "Library", "Application Support", "Claude", "local-agent-mode-sessions"
        )
    cwd_counts = defaultdict(int)
    total_jsonl = 0
    if not os.path.isdir(cowork_dir):
        return cwd_counts, total_jsonl

    for root, dirs, files in os.walk(cowork_dir):
        for f in files:
            if not f.startswith("local_") or not f.endswith(".json") or ".bak" in f:
                continue
            try:
                with open(os.path.join(root, f)) as fh:
                    data = json.load(fh)
                cwd = data.get("cwd", "")
                if cwd:
                    cwd_counts[cwd] += 1
            except (json.JSONDecodeError, OSError):
                pass

    # Count JSONL files (excluding audit.jsonl)
    for root, dirs, files in os.walk(cowork_dir):
        for f in files:
            if f.endswith(".jsonl") and f != "audit.jsonl":
                full = os.path.join(root, f)
                if _is_interactive_session(Path(full)):
                    total_jsonl += 1

    return cwd_counts, total_jsonl


def scan_vault(vault_path):
    """Count vault entries by project path."""
    project_counts = defaultdict(int)
    total = 0
    if not os.path.isdir(vault_path):
        return project_counts, total

    for f in os.listdir(vault_path):
        if not f.endswith(".md"):
            continue
        full = os.path.join(vault_path, f)
        with open(full) as fh:
            text = fh.read()
        if not text.startswith("---\n"):
            continue
        try:
            end = text.index("---", 4)
        except ValueError:
            continue
        for line in text[4:end].strip().split("\n"):
            if line.startswith("project:"):
                proj = line.partition(": ")[2].strip().strip('"')
                project_counts[proj] += 1
                total += 1
                break
    return project_counts, total


def generate_report(home, vault_path, account):
    """Generate the full audit report as markdown."""
    now = datetime.now().strftime("%Y-%m-%d")
    projects = scan_project_roots(home)
    cli = scan_cli_sessions(home)
    desktop = scan_desktop_sessions(home)
    codex = scan_codex_sessions(home)
    cowork_cwds, cowork_jsonl_total = scan_cowork_sessions(home)
    vault_projects, vault_total = scan_vault(vault_path)

    lines = []
    lines.append(f"# {account} Session Audit ({now})")
    lines.append("")
    lines.append("## Project Directories vs Sessions")
    lines.append("")
    lines.append("| Root | Project | .claude | CLI | Desktop | Codex | Co-work | Total | In Vault | Found | All In |")
    lines.append("|---|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")

    skip_names = {"obsidian_shared_vault_session_index"}

    for label, name, full in projects:
        if name in skip_names:
            continue

        has_claude = os.path.isdir(os.path.join(full, ".claude"))
        n_cli = cli.get(full, 0)
        n_desk = desktop.get(full, 0)
        n_codex = codex.get(full, 0)
        # Codex may reference subdirs
        for cwd, count in codex.items():
            if cwd.startswith(full + "/") and cwd != full:
                n_codex += count
        n_cowork = cowork_cwds.get(full, 0)
        total = n_cli + n_desk + n_codex + n_cowork

        n_vault = vault_projects.get(full, 0)
        for vp, count in vault_projects.items():
            if vp.startswith(full + "/") and vp != full:
                n_vault += count

        found = "✅" if total > 0 else "❌"
        if total == 0:
            all_in = "➖"
        elif n_vault >= total:
            all_in = "✅"
        else:
            all_in = "❌"

        claude_str = "yes" if has_claude else ""
        lines.append(f"| {label} | {name} | {claude_str} | {n_cli} | {n_desk} | {n_codex} | {n_cowork} | {total} | {n_vault} | {found} | {all_in} |")

    # Co-work summary
    lines.append(f"| Co-work | {len(cowork_cwds)} named sessions | | 0 | 0 | 0 | {cowork_jsonl_total} | {cowork_jsonl_total} | {vault_projects.get('/sessions', 0)} | ✅ | {'✅' if cowork_jsonl_total > 0 else '❌'} |")

    # Summary
    total_sessions = sum(cli.values()) + sum(desktop.values()) + sum(codex.values()) + cowork_jsonl_total
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **CLI sessions:** {sum(cli.values())}")
    lines.append(f"- **Desktop sessions:** {sum(desktop.values())}")
    lines.append(f"- **Codex sessions:** {sum(codex.values())}")
    lines.append(f"- **Co-work sessions:** {cowork_jsonl_total} JSONL files across {len(cowork_cwds)} named sessions")
    lines.append(f"- **Total sessions found:** {total_sessions}")
    lines.append(f"- **In vault:** {vault_total}")

    # Gap analysis
    lines.append("")
    lines.append("## Gaps")
    lines.append("")

    gaps = []
    for label, name, full in projects:
        if name in skip_names:
            continue
        n_desk = desktop.get(full, 0)
        n_vault = vault_projects.get(full, 0)
        for vp, count in vault_projects.items():
            if vp.startswith(full + "/") and vp != full:
                n_vault += count
        n_total = cli.get(full, 0) + n_desk + codex.get(full, 0) + cowork_cwds.get(full, 0)
        if n_total > 0 and n_vault < n_total:
            missing = n_total - n_vault
            gaps.append(f"- {label}/{name}: {missing} session(s) missing from vault")

    if gaps:
        for g in gaps:
            lines.append(g)
    else:
        lines.append("No gaps — all found sessions are in the vault.")

    return "\n".join(lines) + "\n"


def main():
    cfg = load_config()

    parser = argparse.ArgumentParser(description="Audit session coverage")
    parser.add_argument("--account", default=os.environ.get("USER", "unknown"))
    parser.add_argument("--output", type=Path, default=None,
                        help="Write report to file (default: stdout)")
    parser.add_argument("--vault", type=Path, default=None)
    args = parser.parse_args()

    home = os.path.expanduser("~")
    vault = str(args.vault or cfg["vault_path"])

    report = generate_report(home, vault, args.account)

    if args.output:
        args.output.write_text(report)
        print(f"Audit written to: {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
