# ai-session-vault

Export Claude Code and Codex session history as searchable Markdown in an Obsidian vault.

## Problem

AI coding tools store conversation history in opaque formats (JSONL, SQLite) scattered across tool-specific directories. If you use multiple tools or multiple machines/accounts, finding a past session means guessing where it is and searching each location separately.

## Solution

Export all sessions to a single Obsidian vault as Markdown with YAML frontmatter. Search once across everything — by keyword or semantically — then go to the original session, pull context into a current conversation, or share it.

## Quick Start

```bash
# 1. Clone
git clone https://github.com/youruser/ai-session-vault.git
cd ai-session-vault

# 2. Configure
cp config.example.json config.json
# Edit config.json with your vault path and session sources

# 3. Create the vault directory
mkdir -p ~/obsidian-session-vault

# 4. Export
python3 scripts/export_sessions_to_obsidian.py

# 5. Open the vault directory in Obsidian
```

## Configuration

Copy `config.example.json` to `config.json` and edit:

```json
{
  "vault_path": "~/obsidian-session-vault",
  "claude_projects": [
    "~/.claude/projects"
  ],
  "codex_sessions": "~/.codex/sessions",
  "accounts": []
}
```

| Field | Description |
|-------|-------------|
| `vault_path` | Directory where Markdown files are written. Open this in Obsidian. |
| `claude_projects` | Array of paths. Each can be a single project dir (contains `*.jsonl`) or a parent dir (contains project subdirs). `~` is expanded. |
| `codex_sessions` | Path to Codex sessions directory. |
| `accounts` | Other macOS accounts to read sessions from (for `apply_cross_account_acls.sh`). Leave empty for single-account use. |

`config.json` is gitignored. CLI flags override config values:

```bash
python3 scripts/export_sessions_to_obsidian.py --vault /other/vault --claude-project ~/.claude/projects/specific-project
```

Without `config.json`, generic defaults are used (`~/.claude/projects`, `~/.codex/sessions`).

## How It Works

`scripts/export_sessions_to_obsidian.py` reads JSONL session files, auto-detects the format (Claude Code vs Codex), and writes one Markdown file per session into the vault.

Each exported file contains:
- YAML frontmatter: `session_id`, `date`, `time`, `source`, message counts, `tags`
- User and assistant messages as `## User (turn N)` / `## Assistant (turn N)` sections
- Tool calls summarised as one-liners (e.g. `Bash: \`ls -la\``, `Read: /foo/bar.md`)
- Thinking blocks, tool results, system reminders, and developer messages filtered out
- Long messages truncated (user: 3000 chars, assistant: 5000 chars)

### Output file naming

```
{date}_{source}_{session-id-first-8-chars}.md
```

Example: `2026-03-31_claude_7ee2430b.md`

## Session Data Sources

| Source | Path | Format | Status |
|--------|------|--------|--------|
| Claude Code CLI | `~/.claude/projects/<encoded-path>/*.jsonl` | JSONL | Supported |
| Codex (JSONL) | `~/.codex/sessions/**/*.jsonl` | JSONL | Supported |
| Codex (SQLite) | `~/.codex/logs_1.sqlite` | SQLite | Not yet supported |
| Co-work | `~/Library/Application Support/Claude/local-agent-mode-sessions/` | Unknown | Not yet supported |
| Claude Desktop | `~/Library/Application Support/Claude/claude-code-sessions/` | JSON metadata (links to CLI JSONL via `cliSessionId`) | Not yet supported |
| claude.ai | Server-side only | N/A | Not exportable |

### Supported JSONL formats

**Claude Code** (`~/.claude/projects/<encoded-path>/*.jsonl`):
```json
{"type": "user", "message": {"content": "..."}}
{"type": "assistant", "message": {"content": [{"type": "text", "text": "..."}]}}
```

**Codex** (`~/.codex/sessions/**/*.jsonl`):
```json
{"timestamp": "...", "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "..."}]}}
```

## Multi-Account Setup (Optional)

If you have multiple macOS accounts with session data, you can grant read access so the export runs from one account:

1. Add account names to `config.json`:
   ```json
   "accounts": ["otheraccount1", "otheraccount2"]
   ```

2. Apply read-only ACLs:
   ```bash
   sudo bash scripts/apply_cross_account_acls.sh
   ```

3. Add their project paths to `claude_projects`:
   ```json
   "claude_projects": [
     "~/.claude/projects",
     "/Users/otheraccount1/.claude/projects"
   ]
   ```

To remove ACLs later: `sudo bash scripts/apply_cross_account_acls.sh --remove`

## Obsidian Tips

- **Dataview** plugin — query YAML frontmatter fields (e.g. list all sessions by date, filter by source)
- **Smart Connections** plugin — semantic/vector search across session content
- **Concurrent access warning:** if multiple macOS accounts open the same vault via fast user switching, Obsidian's SQLite index can corrupt. Quit Obsidian before switching accounts.

## Tests

```bash
python3 -m pytest tests/test_export_sessions.py -v
```

63 tests covering both formats. No dependencies beyond Python 3 stdlib + pytest.
