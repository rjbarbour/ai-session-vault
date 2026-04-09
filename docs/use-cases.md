# Use Cases

## Setup Scenarios

### 1. Solo developer, single machine, CLI only

The simplest case. Uses Claude Code CLI, wants to find past sessions.

**What they have:** `~/.claude/projects/` with JSONL session files.

**What they do:**
```bash
git clone https://github.com/rjbarbour/ai-session-vault.git
cd ai-session-vault
python3 scripts/export_all.py
```

**What they get:** A vault directory with one Markdown file per session, searchable in Obsidian or any text editor.

**Config needed:** None. The tool auto-discovers `~/.claude/projects` and creates a default vault at `~/obsidian-session-vault`.

### 2. Solo developer, CLI + Desktop + Co-work

Same person but uses Claude Desktop app and Co-work (background agent) too. Sessions are in three locations.

**What they have:**
- `~/.claude/projects/` — CLI sessions
- `~/Library/Application Support/Claude/claude-code-sessions/` — Desktop metadata + JSONL
- `~/Library/Application Support/Claude/local-agent-mode-sessions/` — Co-work sessions

**What they do:** Same single command. The tool auto-discovers all three sources.

**Config needed:** None. All sources are in standard locations under the current user's home.

### 3. Solo developer, Claude + Codex

Uses both Anthropic Claude Code and OpenAI Codex. Sessions are in different formats.

**What they have:**
- `~/.claude/projects/` — Claude Code JSONL
- `~/.codex/sessions/` — Codex JSONL (different format, auto-detected)
- `~/.codex/session_index.jsonl` — Codex session titles

**What they do:** Same single command. The tool auto-detects which format each file uses.

**Config needed:** None.

### 4. Multi-account on one machine

Multiple macOS user accounts on the same machine, each with their own Claude/Codex sessions. Wants one searchable vault across all accounts.

**What they have:** Session data under `/Users/<account>/.claude/` for each account.

**What they do:**
1. Copy `config.example.json` to `config.json`
2. Add account names: `"accounts": ["otheraccount1", "otheraccount2"]`
3. Run ACL script: `sudo bash scripts/apply_cross_account_acls.sh`
4. Grant Terminal Full Disk Access (System Settings > Privacy & Security) if they want to audit project directories under `~/Documents`
5. Run: `python3 scripts/export_all.py`

**Config needed:** Account list. Optionally `extra_project_roots` for non-standard project locations.

**Docs needed:** `docs/cross-account-access.md` covers the three permission layers (POSIX, ACLs, TCC).

### 5. Multi-machine

Sessions spread across multiple machines. Wants to search across all of them.

**What they do:**
- Run the export on each machine to produce a local vault
- Sync the vault via git, Dropbox, iCloud, or rsync to a shared location
- Open the combined vault in Obsidian

**Config needed:** Each machine has its own `config.json` pointing to the same shared vault (or separate vaults that are merged).

**Not yet implemented.** The tool works per-machine. Cross-machine aggregation is Phase 6c.

---

## Day-to-Day Usage

### 6. Find a past session

**Scenario:** "I fixed a permissions issue a few weeks ago — which session was that?"

**What they do:**
1. Open the vault in Obsidian
2. Search for "permissions" or "ACL" — full-text search across all sessions
3. Results show session titles, summaries, and keywords in frontmatter
4. Click into the matching session to read the summary and confirm it's the right one

**What makes this work:** The enrichment step generates searchable summaries and keywords, so searches match on meaning not just exact strings. The Dataview plugin enables frontmatter queries (e.g. "show all sessions from March 2026 tagged codex").

### 7. Pull context into current work

**Scenario:** Found the right session. Need to bring that context into a current Claude Code session.

**What they do:**
1. Read the session summary in Obsidian
2. Copy relevant sections (the summary, key decisions, code snippets)
3. Paste into the current Claude Code session as context

**Future:** Could build a skill or MCP tool that searches the vault and injects context automatically.

### 8. Export for sharing

**Scenario:** Did significant work in a session, want to share it with a colleague or post on GitHub.

**What they do:**
1. Find the session in Obsidian
2. The Markdown file is already in a shareable format with frontmatter, structured headings, and summaries
3. Copy to a GitHub repo, Notion page, or email

**What makes this work:** The exported format is clean Markdown, not raw JSONL. Summaries provide a TL;DR without reading the full transcript.

### 9. Incremental re-index

**Scenario:** Has been working for a week, new sessions need indexing.

**What they do:**
```bash
python3 scripts/export_all.py
```

The export overwrites existing files (same session = same filename) and only enriches sessions that don't already have summaries (`--skip-enriched`). Dedupe runs automatically.

**Performance:** Export is fast (seconds). Enrichment is the slow part — parallel Haiku calls, ~10 seconds per session with 10 workers.

### 10. Automated re-index

**Scenario:** Doesn't want to remember to run the export.

**What they do:**
```bash
# Add to crontab — run daily at midnight
0 0 * * * cd /path/to/ai-session-vault && python3 scripts/export_all.py >> export.log 2>&1
```

Or as a Claude Code hook that triggers after each session ends (not yet implemented).

**Requirement:** The `claude` CLI must be installed and authenticated for the enrichment step. If it's not available, the export runs but enrichment is skipped.

---

## What the Tool Does NOT Cover

- **claude.ai (web)** — conversations are server-side only, no local session files
- **ChatGPT / other AI tools** — different formats, not supported (could be added)
- **Real-time sync** — the vault is a point-in-time export, not a live mirror
- **Session replay** — you can read what happened but can't resume a session from the vault
- **Privacy/access control** — all sessions in the vault are visible to anyone who opens it. Use separate vaults for per-account privacy.
