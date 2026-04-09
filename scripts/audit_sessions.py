"""Audit session coverage across all sources and the Obsidian vault.

Scans project directories, Claude Code JSONL, Desktop metadata, Codex
sessions, Co-work sessions, and the vault to produce a comprehensive
markdown audit report.

Works for any macOS account — auto-discovers project roots, handles
iCloud path moves, and filters vault entries by account.

Usage:
    python3 scripts/audit_sessions.py                      # current user
    python3 scripts/audit_sessions.py --account robert     # other account
    python3 scripts/audit_sessions.py --account robert --output audit_robert.md
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from export_sessions_to_obsidian import _is_interactive_session, check_dir, load_config


# ---------------------------------------------------------------------------
# Project root discovery
# ---------------------------------------------------------------------------

def scan_project_roots(home, extra_roots=None):
    """Find all project directories for an account.

    Three-direction discovery:
    1. Search for .claude directories under home — any dir with .claude is a project
    2. Extract cwds from session JSONL — every cwd is a project directory
    3. Check extra_project_roots from config — user-specified directories

    The parent of each discovered project becomes a "root" for grouping
    in the report.
    """
    project_paths = set()  # full paths of project directories

    # Direction 1: Find all .claude directories (fast filesystem search)
    search_roots = [home]
    # Add extra roots from config (e.g. ~/DocsLocal on machines that use it)
    # Resolve ~ relative to the target account's home, not the current user
    if extra_roots:
        for root in extra_roots:
            if root.startswith("~/"):
                expanded = os.path.join(home, root[2:])
            else:
                expanded = root
            if os.path.isdir(expanded):
                search_roots.append(expanded)
    for root in search_roots:
        if not os.path.isdir(root):
            continue
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                # Check for .claude before filtering (it starts with .)
                if ".claude" in dirnames and dirpath != home:
                    project_paths.add(dirpath)
                # Skip hidden dirs, node_modules, .git internals for descent
                dirnames[:] = [d for d in dirnames
                               if not d.startswith(".") and d not in
                               ("node_modules", "__pycache__", "venv", ".venv")]
                # Don't descend too deep
                depth = dirpath.replace(root, "").count(os.sep)
                if depth >= 4:
                    dirnames.clear()
        except PermissionError:
            pass

    # Direction 2: Extract cwds from session JSONL
    projects_root = os.path.join(home, ".claude", "projects")
    if os.path.isdir(projects_root):
        try:
            for proj in os.listdir(projects_root):
                proj_path = os.path.join(projects_root, proj)
                if not os.path.isdir(proj_path):
                    continue
                for f in os.listdir(proj_path):
                    if not f.endswith(".jsonl"):
                        continue
                    full = os.path.join(proj_path, f)
                    try:
                        with open(full, errors="replace") as fh:
                            for line in fh:
                                try:
                                    data = json.loads(line)
                                    cwd = data.get("cwd", "")
                                    if cwd and os.path.isabs(cwd) and cwd != home:
                                        project_paths.add(cwd)
                                    break
                                except (json.JSONDecodeError, KeyError):
                                    pass
                    except (OSError, PermissionError):
                        pass
        except PermissionError:
            pass

    # Build (root_label, name, full_path) tuples grouped by parent directory
    skip_names = {"obsidian_shared_vault_session_index"}
    projects = []
    for path in sorted(project_paths):
        name = os.path.basename(path)
        if name in skip_names:
            continue
        parent = os.path.dirname(path)
        label = parent.replace(home, "~")
        projects.append((label, name, path))

    return projects


# ---------------------------------------------------------------------------
# Session scanners
# ---------------------------------------------------------------------------

def scan_cli_sessions(home):
    """Count interactive CLI sessions by cwd, including subagent files."""
    projects_root = os.path.join(home, ".claude", "projects")
    cwd_counts = defaultdict(int)
    if not check_dir(projects_root, f"CLI sessions ({projects_root})"):
        return cwd_counts

    for proj_dir in os.listdir(projects_root):
        proj_path = os.path.join(projects_root, proj_dir)
        if not os.path.isdir(proj_path):
            continue
        # Top-level JSONL
        top_jsonl = [f for f in os.listdir(proj_path) if f.endswith(".jsonl")]
        for f in top_jsonl:
            full = os.path.join(proj_path, f)
            if not _is_interactive_session(Path(full)):
                continue
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

        # Subagent files (for Desktop-only sessions with no top-level JSONL)
        if not top_jsonl:
            for session_dir in os.listdir(proj_path):
                sd_path = os.path.join(proj_path, session_dir)
                if not os.path.isdir(sd_path) or session_dir == "memory":
                    continue
                subagent_dir = os.path.join(sd_path, "subagents")
                if os.path.isdir(subagent_dir):
                    for f in os.listdir(subagent_dir):
                        if not f.endswith(".jsonl"):
                            continue
                        full = os.path.join(subagent_dir, f)
                        if _is_interactive_session(Path(full)):
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


def scan_desktop_sessions(home):
    """Count Desktop sessions by cwd. Track sessions with missing parent JSONL."""
    desktop_dir = os.path.join(
        home, "Library", "Application Support", "Claude", "claude-code-sessions"
    )
    cwd_counts = defaultdict(int)
    missing_parent = []
    if not check_dir(desktop_dir, f"Desktop sessions ({desktop_dir})"):
        return cwd_counts, missing_parent

    projects_root = os.path.join(home, ".claude", "projects")

    for root, dirs, files in os.walk(desktop_dir):
        for f in files:
            if not f.endswith(".json") or ".bak" in f:
                continue
            try:
                with open(os.path.join(root, f)) as fh:
                    data = json.load(fh)
                cwd = data.get("cwd", "")
                cli_id = data.get("cliSessionId", "")
                title = data.get("title", "")
                if cwd:
                    cwd_counts[cwd] += 1
                if cli_id and os.path.isdir(projects_root):
                    has_jsonl = False
                    for proj in os.listdir(projects_root):
                        proj_path = os.path.join(projects_root, proj)
                        if os.path.isfile(os.path.join(proj_path, cli_id + ".jsonl")):
                            has_jsonl = True
                            break
                    if not has_jsonl:
                        missing_parent.append((title, cwd, cli_id))
            except (json.JSONDecodeError, OSError):
                pass
    return cwd_counts, missing_parent


def scan_codex_sessions(home):
    """Count Codex sessions by cwd from session_meta."""
    sessions_dir = os.path.join(home, ".codex", "sessions")
    cwd_counts = defaultdict(int)
    if not check_dir(sessions_dir, f"Codex sessions ({sessions_dir})"):
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


def scan_cowork_sessions(home):
    """Count Co-work sessions (metadata-level) and JSONL files."""
    cowork_dir = os.path.join(
        home, "Library", "Application Support", "Claude", "local-agent-mode-sessions"
    )
    metadata_cwds = defaultdict(int)
    jsonl_count = 0
    if not check_dir(cowork_dir, f"Co-work sessions ({cowork_dir})"):
        return metadata_cwds, jsonl_count

    for root, dirs, files in os.walk(cowork_dir):
        for f in files:
            if f.startswith("local_") and f.endswith(".json") and ".bak" not in f:
                try:
                    with open(os.path.join(root, f)) as fh:
                        data = json.load(fh)
                    cwd = data.get("cwd", "")
                    if cwd:
                        metadata_cwds[cwd] += 1
                except (json.JSONDecodeError, OSError):
                    pass
            elif f.endswith(".jsonl") and f != "audit.jsonl":
                full = os.path.join(root, f)
                if _is_interactive_session(Path(full)):
                    jsonl_count += 1

    return metadata_cwds, jsonl_count


# ---------------------------------------------------------------------------
# Vault scanning
# ---------------------------------------------------------------------------

def scan_vault(vault_path, account_filter=None):
    """Count vault entries by project and source.

    If account_filter is set, only count entries matching that account.
    """
    by_project = defaultdict(int)
    by_source = defaultdict(int)
    total = 0
    if not os.path.isdir(vault_path):
        return by_project, by_source, total

    for f in os.listdir(vault_path):
        if not f.endswith(".md"):
            continue
        full = os.path.join(vault_path, f)
        project = source = account = ""
        with open(full) as fh:
            first_line = fh.readline()
            if first_line.strip() != "---":
                continue
            for line in fh:
                line = line.strip()
                if line == "---":
                    break
                if line.startswith("project:"):
                    project = line.partition(": ")[2].strip().strip('"')
                elif line.startswith("source:"):
                    source = line.partition(": ")[2].strip().strip('"')
                elif line.startswith("account:"):
                    account = line.partition(": ")[2].strip().strip('"')
        if account_filter and account != account_filter:
            continue
        if project or source:
            total += 1
            if project:
                by_project[project] += 1
            if source:
                by_source[source] += 1

    return by_project, by_source, total


# ---------------------------------------------------------------------------
# Path aliases and matching
# ---------------------------------------------------------------------------

def build_path_aliases(home):
    """Build a mapping of iCloud-moved paths to their TMD equivalents.

    Enumerates ALL subdirectories inside the TMD folder, not just
    Projects and GitHub.
    """
    docs = os.path.join(home, "Documents")
    aliases = {}

    tmd_name = None
    if os.path.isdir(docs):
        try:
            for item in os.listdir(docs):
                if "TMD" in item or "MacBook" in item:
                    full = os.path.join(docs, item)
                    if os.path.isdir(full):
                        tmd_name = item
                        break
        except PermissionError:
            pass

    if not tmd_name:
        return aliases

    tmd = os.path.join(docs, tmd_name)
    try:
        for subdir in os.listdir(tmd):
            sub_path = os.path.join(tmd, subdir)
            if not os.path.isdir(sub_path) or subdir.startswith("."):
                continue
            old_root = os.path.join(docs, subdir)
            # Map items inside: ~/Documents/<subdir>/X -> ~/Documents/TMD/<subdir>/X
            try:
                for item in os.listdir(sub_path):
                    old_path = os.path.join(old_root, item)
                    new_path = os.path.join(sub_path, item)
                    aliases[old_path] = new_path
            except PermissionError:
                pass
    except PermissionError:
        pass

    return aliases


def match_count(cwd_counts, project_path, aliases=None):
    """Count sessions matching a project path, including subdirectory and alias matches."""
    count = cwd_counts.get(project_path, 0)
    prefix = project_path + "/"
    for cwd, n in cwd_counts.items():
        if cwd.startswith(prefix):
            count += n

    if aliases:
        for old_path, new_path in aliases.items():
            if new_path == project_path or new_path.startswith(project_path + "/"):
                count += cwd_counts.get(old_path, 0)
                old_prefix = old_path + "/"
                for cwd, n in cwd_counts.items():
                    if cwd.startswith(old_prefix):
                        count += n

    return count


def find_orphan_cwds(all_cwds, known_paths):
    """Find session cwds that don't match any known project directory.

    Excludes Co-work /sessions/* paths (expected to be orphans).
    """
    orphans = defaultdict(int)
    known_set = set(known_paths)
    for cwd, count in all_cwds.items():
        # Co-work sessions use /sessions/<name> — not real project paths
        if cwd.startswith("/sessions/"):
            continue
        matched = False
        for known in known_set:
            if cwd == known or cwd.startswith(known + "/"):
                matched = True
                break
        if not matched:
            orphans[cwd] += count
    return orphans


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(home, vault_path, account, extra_roots=None):
    """Generate the full audit report as markdown."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    projects = scan_project_roots(home, extra_roots=extra_roots)
    cli = scan_cli_sessions(home)
    desktop, desktop_missing_parent = scan_desktop_sessions(home)
    codex = scan_codex_sessions(home)
    cowork_cwds, cowork_jsonl_total = scan_cowork_sessions(home)
    vault_projects, vault_sources, vault_total = scan_vault(vault_path, account_filter=account)

    known_paths = [full for _, _, full in projects]
    aliases = build_path_aliases(home)

    lines = []
    lines.append(f"# {account} Session Audit ({now})")
    lines.append("")
    lines.append("## Project Directories vs Sessions")
    lines.append("")
    lines.append("| Root | Project | .claude | CLI | Desktop | Codex | Co-work | Total | In Vault | Found | All In |")
    lines.append("|---|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")

    for label, name, full in projects:
        has_claude = os.path.isdir(os.path.join(full, ".claude"))
        n_cli = match_count(cli, full, aliases)
        n_desk = match_count(desktop, full, aliases)
        n_codex = match_count(codex, full, aliases)
        n_cowork = match_count(cowork_cwds, full, aliases)
        total = n_cli + n_desk + n_codex + n_cowork

        n_vault = match_count(vault_projects, full, aliases)

        has_lost_parent = False
        for title, cwd, cli_id in desktop_missing_parent:
            if cwd == full or cwd.startswith(full + "/"):
                has_lost_parent = True
                break
            if aliases:
                for old_path, new_path in aliases.items():
                    if (new_path == full or new_path.startswith(full + "/")) and \
                       (cwd == old_path or cwd.startswith(old_path + "/")):
                        has_lost_parent = True
                        break

        found = "✅" if total > 0 else "❌"
        if total == 0:
            all_in = "➖"
        elif has_lost_parent:
            all_in = "⚠️"
        elif n_vault >= total:
            all_in = "✅"
        else:
            all_in = "❌"

        claude_str = "yes" if has_claude else ""
        lines.append(f"| {label} | {name} | {claude_str} | {n_cli} | {n_desk} | {n_codex} | {n_cowork} | {total} | {n_vault} | {found} | {all_in} |")

    # Co-work summary row
    cowork_in_vault = vault_sources.get("claude-cowork", 0)
    if cowork_jsonl_total > 0 or len(cowork_cwds) > 0:
        lines.append(f"| Co-work | {len(cowork_cwds)} named sessions | | 0 | 0 | 0 | {cowork_jsonl_total} | {cowork_jsonl_total} | {cowork_in_vault} | ✅ | {'✅' if cowork_in_vault >= cowork_jsonl_total else '❌'} |")

    # Summary
    total_found = sum(cli.values()) + sum(desktop.values()) + sum(codex.values()) + cowork_jsonl_total
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **CLI sessions:** {sum(cli.values())}")
    lines.append(f"- **Desktop sessions:** {sum(desktop.values())}")
    lines.append(f"- **Codex sessions:** {sum(codex.values())}")
    lines.append(f"- **Co-work:** {len(cowork_cwds)} metadata sessions, {cowork_jsonl_total} JSONL files")
    lines.append(f"- **Total found:** {total_found}")
    lines.append(f"- **In vault:** {vault_total}")
    if vault_sources:
        lines.append("")
        lines.append("Vault breakdown by source:")
        for source, count in sorted(vault_sources.items()):
            lines.append(f"- {source}: {count}")

    # Gaps
    lines.append("")
    lines.append("## Gaps")
    lines.append("")

    gaps = []
    for label, name, full in projects:
        n_total = match_count(cli, full, aliases) + match_count(desktop, full, aliases) + \
                  match_count(codex, full, aliases) + match_count(cowork_cwds, full, aliases)
        n_vault = match_count(vault_projects, full, aliases)
        if n_total > 0 and n_vault < n_total:
            missing = n_total - n_vault
            gaps.append(f"- {label}/{name}: {missing} session(s) missing from vault")

    if cowork_jsonl_total > 0 and cowork_in_vault < cowork_jsonl_total:
        gaps.append(f"- Co-work: {cowork_jsonl_total - cowork_in_vault} session(s) missing from vault")

    if gaps:
        for g in gaps:
            lines.append(g)
    else:
        lines.append("No gaps — all found sessions are in the vault.")

    # Data losses
    if desktop_missing_parent:
        lines.append("")
        lines.append("## Data Losses (Desktop sessions with no parent JSONL)")
        lines.append("")
        lines.append("These sessions were visible in Claude Desktop but the parent conversation")
        lines.append("was never written to disk as JSONL. Only subagent work products may survive.")
        lines.append("Known bug: anthropics/claude-code#29373")
        lines.append("")
        lines.append("| Title | Project | Session ID |")
        lines.append("|-------|---------|------------|")
        for title, cwd, cli_id in sorted(desktop_missing_parent, key=lambda x: x[0]):
            display_cwd = cwd.replace(home, "~")
            lines.append(f"| {title} | {display_cwd} | {cli_id[:12]}... |")

    # Orphan sessions
    all_cwds = defaultdict(int)
    for d in (cli, desktop, codex, cowork_cwds):
        for cwd, count in d.items():
            all_cwds[cwd] += count

    orphans = find_orphan_cwds(all_cwds, known_paths)
    if orphans:
        lines.append("")
        lines.append("## Orphan Sessions (CWD not in any scanned root)")
        lines.append("")
        lines.append("| CWD | Sessions |")
        lines.append("|-----|:--------:|")
        for cwd, count in sorted(orphans.items()):
            display = cwd.replace(home, "~")
            lines.append(f"| {display} | {count} |")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    cfg = load_config()

    parser = argparse.ArgumentParser(description="Audit session coverage")
    parser.add_argument("--account", default=os.environ.get("USER", "unknown"))
    parser.add_argument("--output", type=Path, default=None,
                        help="Write report to file (default: stdout)")
    parser.add_argument("--vault", type=Path, default=None)
    args = parser.parse_args()

    if args.account == os.environ.get("USER", ""):
        home = os.path.expanduser("~")
    else:
        home = f"/Users/{args.account}"
    vault = str(args.vault or cfg["vault_path"])

    extra_roots = cfg.get("extra_project_roots", [])
    report = generate_report(home, vault, args.account, extra_roots=extra_roots)

    if args.output:
        args.output.write_text(report)
        print(f"Audit written to: {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
