"""Export Claude Code and Codex session JSONL files to Obsidian-friendly Markdown.

Each session becomes one .md file with YAML frontmatter and
human/assistant messages as sections. Tool calls and results
are summarised, not dumped in full.

Supports two JSONL formats:
  - Claude Code: {"type": "user"|"assistant", "message": {"content": ...}}
  - Codex:       {"timestamp": ..., "type": "response_item"|"event_msg", "payload": {...}}

Format is auto-detected from the first JSONL line.

Usage:
    python3 scripts/export_sessions_to_obsidian.py [--vault PATH]

Configuration:
    Paths are loaded from config.json (see config.example.json).
    CLI flags override config values. Without config.json, generic defaults
    are used (~/.claude/projects, ~/.codex/sessions).
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path


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
# Format detection
# ---------------------------------------------------------------------------

def detect_format(jsonl_path):
    """Return 'claude' or 'codex' based on the first parseable line."""
    with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "payload" in data and data.get("type") in (
                "session_meta", "event_msg", "response_item", "turn_context",
            ):
                return "codex"
            if data.get("type") in ("user", "assistant", "system", "human", "file-history-snapshot"):
                return "claude"
            return "claude"
    return "claude"


# ---------------------------------------------------------------------------
# Claude Code format
# ---------------------------------------------------------------------------

def claude_extract_text(content):
    """Pull plain text from Claude message content (string or list of blocks).

    Skips tool_result and thinking blocks.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                btype = block.get("type", "")
                if btype == "text":
                    parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    name = block.get("name", "unknown_tool")
                    parts.append(f"[Tool call: {name}]")
        return "\n".join(parts)
    return str(content)


def summarise_tool_use(block):
    """Return a compact one-line summary of a tool_use block."""
    name = block.get("name", "unknown")
    inp = block.get("input", {})
    if name == "Read":
        return f"Read: {inp.get('file_path', '?')}"
    if name == "Write":
        return f"Write: {inp.get('file_path', '?')}"
    if name == "Edit":
        return f"Edit: {inp.get('file_path', '?')}"
    if name == "Bash":
        cmd = inp.get("command", "")
        return f"Bash: `{cmd[:80]}{'...' if len(cmd) > 80 else ''}`"
    if name == "Grep":
        return f"Grep: pattern={inp.get('pattern', '?')[:40]}"
    if name == "Glob":
        return f"Glob: {inp.get('pattern', '?')}"
    if name == "Agent":
        return f"Agent: {inp.get('description', '?')[:60]}"
    if name == "TodoWrite":
        return "[TodoWrite update]"
    if name == "WebSearch":
        return f"WebSearch: {inp.get('query', '?')[:60]}"
    if name == "WebFetch":
        return f"WebFetch: {inp.get('url', '?')[:60]}"
    return f"{name}: {json.dumps(inp)[:80]}"


def claude_process_message(msg_data):
    """Convert a Claude Code JSONL line to a (role, text) tuple or None."""
    msg_type = msg_data.get("type", "")

    if msg_type in ("user", "human"):
        message = msg_data.get("message", {})
        if isinstance(message, dict):
            content = message.get("content", "")
        elif isinstance(message, str):
            content = message
        else:
            return None
        text = claude_extract_text(content)
        text = re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.DOTALL)
        text = text.strip()
        if not text or len(text) < 3:
            return None
        return ("user", text)

    if msg_type == "assistant":
        message = msg_data.get("message", {})
        if isinstance(message, dict):
            content = message.get("content", "")
        elif isinstance(message, str):
            content = message
        else:
            return None

        parts = []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        t = block.get("text", "").strip()
                        if t:
                            parts.append(t)
                    elif block.get("type") == "tool_use":
                        parts.append(f"- {summarise_tool_use(block)}")
                elif isinstance(block, str):
                    parts.append(block)
        elif isinstance(content, str):
            parts.append(content)

        text = "\n".join(parts).strip()
        if not text:
            return None
        return ("assistant", text)

    return None


def parse_claude_session(jsonl_path):
    """Parse a Claude Code JSONL file into a list of (role, text) tuples."""
    messages = []
    with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            result = claude_process_message(data)
            if result:
                messages.append(result)
    return messages


# ---------------------------------------------------------------------------
# Codex format
# ---------------------------------------------------------------------------

def codex_summarise_function_call(payload):
    """Return a compact one-line summary of a Codex function_call payload."""
    name = payload.get("name", "unknown")
    args_raw = payload.get("arguments", "{}")
    try:
        args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
    except (json.JSONDecodeError, TypeError):
        args = {}

    if name == "exec_command":
        cmd = args.get("cmd", "")
        return f"exec: `{cmd[:80]}{'...' if len(cmd) > 80 else ''}`"
    if name.startswith("_search"):
        query = args.get("query", "")
        return f"{name}: {query[:60]}"
    if name.startswith("_list"):
        return f"{name}"
    return f"{name}: {json.dumps(args)[:80]}"


def codex_process_line(line_data):
    """Convert a Codex JSONL line to a (role, text) tuple or None.

    Returns meaningful conversational content only. Skips:
    - session_meta, turn_context (session plumbing)
    - event_msg (status messages, usually empty)
    - response_item/reasoning (internal chain-of-thought)
    - response_item/function_call_output (tool results)
    - response_item/web_search_call, tool_search_call, tool_search_output
    - developer and system-like user messages (XML-wrapped instructions)
    """
    msg_type = line_data.get("type", "")
    payload = line_data.get("payload", {})

    if msg_type != "response_item":
        return None

    item_type = payload.get("type", "")
    role = payload.get("role", "")

    if item_type == "message":
        content = payload.get("content", [])
        if not isinstance(content, list) or not content:
            return None

        first_block = content[0]
        if not isinstance(first_block, dict):
            return None

        content_type = first_block.get("type", "")
        text = first_block.get("text", "") or ""

        if role == "developer":
            return None
        if role == "user" and content_type == "input_text":
            if text.startswith("<") and ">" in text[:50]:
                return None
            text = text.strip()
            if not text or len(text) < 3:
                return None
            return ("user", text)
        if role == "assistant" and content_type == "output_text":
            text = text.strip()
            if not text:
                return None
            return ("assistant", text)
        return None

    if item_type == "function_call":
        summary = codex_summarise_function_call(payload)
        return ("assistant", f"- {summary}")

    return None


def parse_codex_session(jsonl_path):
    """Parse a Codex JSONL file into a list of (role, text) tuples."""
    messages = []
    with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            result = codex_process_line(data)
            if result:
                messages.append(result)
    return messages


# ---------------------------------------------------------------------------
# Shared export logic
# ---------------------------------------------------------------------------

# Keep old names as aliases so existing tests still import them
extract_text = claude_extract_text
process_message = claude_process_message


def slugify(text, max_len=50):
    """Convert text to a filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    if len(text) > max_len:
        text = text[:max_len].rstrip("-")
    return text or "untitled"


def extract_custom_title(jsonl_path):
    """Extract a custom-title from a Claude Code JSONL file, if present."""
    with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if data.get("type") == "custom-title":
                return data.get("customTitle", "")
    return ""


def load_desktop_titles(desktop_dir=None):
    """Load session titles from Claude Desktop metadata files.

    Returns a dict mapping cliSessionId -> title.
    """
    titles = {}
    if desktop_dir is None:  # pragma: no cover — called from main()
        desktop_dir = Path.home() / "Library" / "Application Support" / "Claude" / "claude-code-sessions"
    else:
        desktop_dir = Path(desktop_dir)
    if not desktop_dir.exists():
        return titles
    for json_file in desktop_dir.rglob("*.json"):
        if ".bak" in json_file.name:
            continue
        try:
            with open(json_file) as f:
                data = json.load(f)
            cli_id = data.get("cliSessionId", "")
            title = data.get("title", "")
            if cli_id and title:
                titles[cli_id] = title
        except (json.JSONDecodeError, OSError):
            continue
    return titles


def load_cowork_sessions(cowork_dir=None):
    """Find Co-work session JSONL files and their titles.

    Returns (titles_dict, jsonl_paths_list) where:
    - titles_dict maps cliSessionId -> title (same format as desktop_titles)
    - jsonl_paths_list is a list of Path objects to session JSONL files
    """
    titles = {}
    jsonl_files = []
    if cowork_dir is None:  # pragma: no cover — called from main()
        cowork_dir = Path.home() / "Library" / "Application Support" / "Claude" / "local-agent-mode-sessions"
    else:
        cowork_dir = Path(cowork_dir)
    if not cowork_dir.exists():
        return titles, jsonl_files

    for json_file in cowork_dir.rglob("local_*.json"):
        if ".bak" in json_file.name or not json_file.is_file():
            continue
        try:
            with open(json_file) as f:
                data = json.load(f)
            cli_id = data.get("cliSessionId", "")
            title = data.get("title", "")
            if cli_id and title:
                titles[cli_id] = title

            # Find the paired JSONL
            paired_dir = json_file.parent / json_file.stem
            if paired_dir.is_dir():
                for jf in paired_dir.rglob("*.jsonl"):
                    if jf.name == "audit.jsonl":
                        continue
                    if _is_interactive_session(jf):
                        jsonl_files.append(jf)
        except (json.JSONDecodeError, OSError):  # pragma: no cover — defensive guard
            continue

    return titles, jsonl_files


def load_codex_titles(codex_sessions_path=None):
    """Load session titles from Codex session_index.jsonl.

    Returns a dict mapping session UUID -> thread_name.
    Reads from ~/.codex/session_index.jsonl by default.
    """
    titles = {}
    if codex_sessions_path:
        index_path = Path(codex_sessions_path).parent / "session_index.jsonl"
    else:
        index_path = Path.home() / ".codex" / "session_index.jsonl"
    if not index_path.exists():
        return titles
    with open(index_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                sid = data.get("id", "")
                name = data.get("thread_name", "")
                if sid and name:
                    titles[sid] = name
            except json.JSONDecodeError:
                continue
    return titles


def extract_codex_meta(jsonl_path):
    """Extract session ID and cwd from a Codex JSONL session_meta line."""
    with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if data.get("type") == "session_meta":
                payload = data.get("payload", {})
                return payload.get("id", ""), payload.get("cwd", "")
    return "", ""


def session_date(jsonl_path):
    """Get the modification time of the JSONL file as a date string."""
    mtime = jsonl_path.stat().st_mtime
    return datetime.fromtimestamp(mtime)


def export_session(jsonl_path, vault_dir, source_tag=None, desktop_titles=None,
                   codex_titles=None, cowork_titles=None):
    """Convert one JSONL file to an Obsidian Markdown file.

    Auto-detects Claude Code vs Codex format.
    """
    fmt = detect_format(jsonl_path)
    if source_tag is None:
        source_tag = fmt

    if fmt == "codex":
        messages = parse_codex_session(jsonl_path)
    else:
        messages = parse_claude_session(jsonl_path)

    if not messages:
        return None

    session_id = jsonl_path.stem
    dt = session_date(jsonl_path)
    date_str = dt.strftime("%Y-%m-%d")
    time_str = dt.strftime("%H:%M")

    # Extract project working directory and entrypoint from JSONL metadata
    project = ""
    entrypoint = ""
    codex_session_id = ""
    if fmt == "codex":
        codex_session_id, project = extract_codex_meta(jsonl_path)
    else:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
            for raw_line in f:
                raw_line = raw_line.strip()
                if not raw_line:  # pragma: no cover — defensive guard
                    continue
                try:
                    line_data = json.loads(raw_line)
                except json.JSONDecodeError:  # pragma: no cover — defensive guard
                    continue
                if not project:
                    cwd = line_data.get("cwd", "")
                    if cwd:
                        project = cwd
                if not entrypoint:
                    ep = line_data.get("entrypoint", "")
                    if ep:
                        entrypoint = ep
                if project and entrypoint:
                    break
    if not project:
        project = jsonl_path.parent.name

    # Extract account from project path or JSONL file path
    account = ""
    if project.startswith("/Users/"):
        parts = project.split("/")
        if len(parts) >= 3:
            account = parts[2]
    if not account:
        # Fall back to JSONL file path (e.g. /Users/rob_dev/Library/.../<session>.jsonl)
        jsonl_str = str(jsonl_path)
        if "/Users/" in jsonl_str:
            parts = jsonl_str.split("/")
            users_idx = parts.index("Users")
            if users_idx + 1 < len(parts):
                account = parts[users_idx + 1]

    # Refine source tag based on entrypoint, Desktop/Co-work metadata
    if source_tag == "cowork":
        source_tag = "claude-cowork"
    elif fmt == "claude" and source_tag == "claude":
        is_desktop = desktop_titles and session_id in (desktop_titles or {})
        if entrypoint == "claude-desktop" or is_desktop:
            source_tag = "claude-desktop"
        else:
            source_tag = "claude-cli"

    user_count = sum(1 for r, _ in messages if r == "user")
    assistant_count = sum(1 for r, _ in messages if r == "assistant")

    # Title: prefer custom-title from JSONL, then Desktop title, then first message
    custom_title = ""
    if fmt == "claude":
        custom_title = extract_custom_title(jsonl_path)

    first_user_msg = next((t for r, t in messages if r == "user"), "")
    # Strip XML tags and command wrappers for cleaner fallback titles
    clean_first_msg = re.sub(r"<[^>]+>", "", first_user_msg).strip()
    clean_first_msg = re.sub(r"\s+", " ", clean_first_msg)

    # Check Desktop/Co-work metadata for a title (by cliSessionId)
    desktop_title = ""
    if desktop_titles and session_id in desktop_titles:
        desktop_title = desktop_titles[session_id]
    if not desktop_title and cowork_titles and session_id in cowork_titles:
        desktop_title = cowork_titles[session_id]

    # Check Codex session_index for a thread_name
    codex_title = ""
    if codex_titles and codex_session_id and codex_session_id in codex_titles:
        codex_title = codex_titles[codex_session_id]

    if custom_title:
        title_candidate = custom_title
        title_source = "custom"
    elif desktop_title:
        title_candidate = desktop_title
        title_source = "desktop"
    elif codex_title:
        title_candidate = codex_title
        title_source = "codex"
    else:
        title_candidate = clean_first_msg[:80]
        if len(title_candidate) > 60:
            title_candidate = title_candidate[:57] + "..."
        title_source = "first_message"

    # Build filename slug from title
    title_slug = slugify(title_candidate)

    # Preserve first message snippet for searchability
    first_msg_snippet = clean_first_msg[:120].replace("\n", " ") if clean_first_msg else ""

    lines = []
    lines.append("---")
    lines.append(f"session_id: {session_id}")
    lines.append(f"date: {date_str}")
    lines.append(f"time: {time_str}")
    lines.append(f"source: {source_tag}")
    lines.append(f"account: {account}")
    lines.append(f"project: {project}")
    lines.append(f"title: \"{title_candidate.replace(chr(34), "'")}\"")
    lines.append(f"title_source: {title_source}")
    if first_msg_snippet:
        lines.append(f"first_message: \"{first_msg_snippet.replace(chr(34), "'")}\"")

    lines.append(f"user_messages: {user_count}")
    lines.append(f"assistant_messages: {assistant_count}")
    lines.append(f"tags: [{source_tag}-session]")
    lines.append("---")
    lines.append("")
    lines.append(f"# {title_candidate}")
    lines.append("")

    turn = 0
    for role, text in messages:
        turn += 1
        if role == "user":
            lines.append(f"## User (turn {turn})")
            lines.append("")
            truncated = text[:3000]
            if len(text) > 3000:
                truncated += f"\n\n*[Message truncated — {len(text)} chars total]*"
            lines.append(truncated)
            lines.append("")
        else:
            lines.append(f"## Assistant (turn {turn})")
            lines.append("")
            truncated = text[:5000]
            if len(text) > 5000:
                truncated += f"\n\n*[Response truncated — {len(text)} chars total]*"
            lines.append(truncated)
            lines.append("")

    # Build a unique ID suffix for the filename
    if fmt == "codex" and codex_session_id:
        id_suffix = codex_session_id[:8]
    elif session_id.startswith("agent-"):
        # Subagent IDs need more chars to be unique (e.g. agent-aca1b2dc51c7ffd2d)
        id_suffix = session_id[:16]
    else:
        id_suffix = session_id[:8]
    filename = f"{date_str}_{source_tag}_{title_slug}_{id_suffix}.md"
    out_path = vault_dir / filename
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

MIN_USER_MESSAGES = 2


def _is_interactive_session(jsonl_path):
    """Check if a JSONL file is a real interactive session, not a claude -p call.

    Filters out non-interactive sessions that match BOTH:
    - Fewer than MIN_USER_MESSAGES user turns
    - Contains a queue-operation/enqueue record (signature of claude -p)
    """
    user_count = 0
    has_enqueue = False
    with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"type": "user"' in line or '"type":"user"' in line:
                user_count += 1
                if user_count >= MIN_USER_MESSAGES:
                    return True
            if '"queue-operation"' in line and '"enqueue"' in line:
                has_enqueue = True
    # Only filter out if BOTH conditions are true
    if user_count < MIN_USER_MESSAGES and has_enqueue:
        return False
    return True


def find_session_files(claude_project_dirs, codex_sessions, cowork_jsonl_files=None):
    """Find all JSONL session files from all configured sources."""
    found = []
    for claude_project in claude_project_dirs:
        if not claude_project.exists():
            continue
        jsonl_files = sorted(claude_project.glob("*.jsonl"))
        if jsonl_files:
            # Direct project dir — export its top-level JSONL files
            for f in jsonl_files:
                if _is_interactive_session(f):
                    found.append(("claude", f))
        else:
            # Parent dir containing project subdirs
            for project_dir in sorted(claude_project.iterdir()):
                if not project_dir.is_dir():
                    continue
                top_jsonl = sorted(project_dir.glob("*.jsonl"))
                if top_jsonl:
                    # Has top-level JSONL — export those
                    for f in top_jsonl:
                        if _is_interactive_session(f):
                            found.append(("claude", f))
                else:
                    # No top-level JSONL — check for session subdirs with
                    # subagent files (Desktop sessions that delegated to agents)
                    for session_dir in sorted(project_dir.iterdir()):
                        if not session_dir.is_dir() or session_dir.name == "memory":
                            continue
                        subagent_dir = session_dir / "subagents"
                        if subagent_dir.is_dir():
                            for f in sorted(subagent_dir.glob("*.jsonl")):
                                if _is_interactive_session(f):
                                    found.append(("claude", f))
    if codex_sessions.exists():
        for f in sorted(codex_sessions.rglob("rollout-*.jsonl")):
            found.append(("codex", f))
    for f in sorted(cowork_jsonl_files or []):
        found.append(("cowork", f))
    return found


def main():  # pragma: no cover
    cfg = load_config()

    parser = argparse.ArgumentParser(
        description="Export Claude Code and Codex sessions to Obsidian"
    )
    parser.add_argument("--vault", type=Path, default=None)
    parser.add_argument("--claude-project", type=Path, default=None,
                        help="Single Claude project dir (overrides config.json)")
    parser.add_argument("--codex-sessions", type=Path, default=None)
    args = parser.parse_args()

    vault = args.vault or Path(cfg["vault_path"])
    codex_sessions = args.codex_sessions or Path(cfg["codex_sessions"])

    if args.claude_project:
        claude_project_dirs = [args.claude_project]
    else:
        claude_project_dirs = [Path(p) for p in cfg["claude_projects"]]

    if not vault.exists():
        print(f"Vault directory not found: {vault}", file=sys.stderr)
        sys.exit(1)

    desktop_titles = load_desktop_titles()
    cowork_titles, cowork_jsonl = load_cowork_sessions()
    codex_titles = load_codex_titles(str(codex_sessions))

    session_files = find_session_files(claude_project_dirs, codex_sessions,
                                       cowork_jsonl_files=cowork_jsonl)
    if not session_files:
        print("No session files found.", file=sys.stderr)
        sys.exit(1)

    claude_count = sum(1 for s, _ in session_files if s == "claude")
    codex_count = sum(1 for s, _ in session_files if s == "codex")
    cowork_count = sum(1 for s, _ in session_files if s == "cowork")
    print(f"Found {len(session_files)} session files ({claude_count} Claude, {codex_count} Codex, {cowork_count} Co-work)")
    print(f"Exporting to: {vault}")
    print()

    exported = 0
    for source_tag, jsonl_path in session_files:
        result = export_session(jsonl_path, vault, source_tag=source_tag,
                                desktop_titles=desktop_titles,
                                codex_titles=codex_titles,
                                cowork_titles=cowork_titles)
        if result:
            size_kb = result.stat().st_size / 1024
            print(f"  [{source_tag:6s}] {result.name} ({size_kb:.1f} KB)")
            exported += 1
        else:
            print(f"  [{source_tag:6s}] {jsonl_path.stem[:20]}... (skipped — no messages)")

    print(f"\nExported {exported}/{len(session_files)} sessions")


if __name__ == "__main__":  # pragma: no cover
    main()
