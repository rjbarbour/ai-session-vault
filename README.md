# ai-session-vault

Export Claude Code, Codex, and Co-work session history as searchable Markdown in an Obsidian vault.

## Problem

AI coding sessions are scattered across multiple tools (Claude Code CLI, Claude Desktop, Co-work, Codex), each with its own storage format. If you use multiple accounts or machines, finding a past session means guessing which tool and account it was in, then searching each location separately.

## Solution

Export all sessions to a single Obsidian vault as Markdown with YAML frontmatter. Search once across everything — by keyword or semantically — then go to the original session, pull context into current work, or share it.

## Requirements

- **Python 3.8+** with pytest (`pip install pytest`)
- **Claude Code CLI** (optional, for AI-generated titles and summaries) — install and run `claude /login`
- **Obsidian** (optional, for browsing) — any text editor or grep works for search

## Quick Start

```bash
# 1. Clone
git clone https://github.com/rjbarbour/ai-session-vault.git
cd ai-session-vault

# 2. Run setup (checks all prerequisites, creates config and vault)
python3 scripts/setup.py

# 3. Export everything
python3 scripts/export_all.py

# 4. Open the vault directory in Obsidian
```

The setup script checks Python, Claude CLI (install + auth), Obsidian, config, vault directory, session sources, and cross-account permissions. It creates `config.json` from the example if needed.

## Pipeline

`export_all.py` runs the full delta refresh pipeline:

```
DISCOVER  → find session files across all accounts
SCAN      → stat JSONL files + read vault frontmatter → update manifest
EXPORT    → new/changed sessions only (delta)
DEDUPE    → remove duplicate vault files
ENRICH    → AI-generated titles, summaries, keywords via Haiku
HEALTH    → report orphans, stale entries, inconsistencies
AUDIT     → per-account coverage report
```

Subsequent runs are fast — only new or changed sessions are processed. The manifest tracks what's been exported and what state it's in.

```bash
python3 scripts/export_all.py                # delta refresh, all accounts (fast)
python3 scripts/export_all.py --full         # re-export everything
python3 scripts/export_all.py --skip-enrich  # no Haiku calls
python3 scripts/export_all.py --audit-only   # just run audits
```

### Automated refresh

A cron job runs every 10 minutes, exporting only the current account. Enrichment (AI titles/summaries) is **not** run by cron because the Claude CLI's OAuth tokens are unavailable in cron's non-interactive environment. Run enrichment manually from an interactive terminal.

```bash
# Cron does: export + dedupe (current account only, every 10 min)
# Install: crontab -e, add:
*/10 * * * * /path/to/scripts/cron_refresh.sh

# Manual enrichment (run periodically from a terminal)
python3 scripts/enrich_sessions.py --skip-enriched --workers 10

# Manual cross-account refresh (all accounts)
python3 scripts/export_all.py
```

## Session Data Sources

| Source | Format | Status |
|--------|--------|--------|
| Claude Code CLI | JSONL in `~/.claude/projects/` | Supported |
| Claude Code Desktop | JSONL + JSON metadata in `Application Support/Claude/` | Supported (titles from metadata) |
| Claude Co-work | JSONL in `Application Support/Claude/local-agent-mode-sessions/` | Supported |
| OpenAI Codex | JSONL in `~/.codex/sessions/` + titles from `session_index.jsonl` | Supported |
| Codex SQLite | `~/.codex/logs_1.sqlite` | Investigated — debug logs only, JSONL is sufficient |
| claude.ai | Server-side only | Not exportable |

## Exported File Format

Each session becomes one Markdown file with YAML frontmatter:

```yaml
---
session_id: b824361f-0d22-4f5c-b857-9947c9b02481
date: 2026-04-07
time: 19:40
source: claude-cli          # claude-cli, claude-desktop, claude-cowork, codex
account: rob_dev
project: "/Users/rob_dev/DocsLocal/chat_session_index"
title: "Session Naming and Cross-Account Indexing"
title_source: custom        # custom, desktop, codex, generated, first_message
source_mtime: 1712345678.123
summary_short: "Built session naming pipeline..."
summary_long: "**Context**\n- The AI session vault project..."
keywords: "session-indexing, cross-account-access, obsidian-vault"
---

# Session Naming and Cross-Account Indexing

## User (turn 1)
...
## Assistant (turn 2)
...
```

## Configuration

Copy `config.example.json` to `config.json` and edit:

```json
{
  "vault_path": "~/obsidian-session-vault",
  "claude_projects": ["~/.claude/projects"],
  "codex_sessions": "~/.codex/sessions",
  "accounts": [],
  "extra_project_roots": [],
  "exclude_projects": []
}
```

| Field | Description |
|-------|-------------|
| `vault_path` | Directory where Markdown files are written |
| `claude_projects` | Array of paths to Claude project directories. `~` is expanded. |
| `codex_sessions` | Path to Codex sessions directory |
| `accounts` | Other macOS accounts to export from (requires ACLs) |
| `extra_project_roots` | Additional directories to scan for projects (resolved per account) |
| `exclude_projects` | Project name patterns to skip during export |

`config.json` is gitignored. Without it, generic defaults are used.

## Multi-Account Setup (macOS)

To export sessions from other macOS accounts:

1. Add account names to `config.json`: `"accounts": ["otheraccount"]`
2. Run setup to check permissions: `python3 scripts/setup.py`
3. Apply ACLs when prompted (or manually): `sudo bash scripts/apply_cross_account_acls.sh`
4. For `~/Documents` access: grant Full Disk Access to Terminal in System Settings

See `docs/cross-account-access.md` for details on the three permission layers (POSIX, ACLs, TCC).

## Scripts

| Script | Purpose |
|--------|---------|
| `setup.py` | Pre-flight checks and guided setup |
| `export_all.py` | Full delta refresh pipeline |
| `export_sessions_to_obsidian.py` | Core export (single account) |
| `enrich_sessions.py` | AI enrichment via Claude Haiku |
| `dedupe_vault.py` | Remove duplicate vault files |
| `vault_health.py` | Report and fix vault inconsistencies |
| `audit_sessions.py` | Session coverage report per account |
| `apply_cross_account_acls.sh` | Cross-account read permissions |
| `manifest.py` | Delta state tracking (library, not CLI) |
| `utils.py` | Shared utilities (library, not CLI) |

## Documentation

| Doc | Contents |
|-----|----------|
| `docs/use-cases.md` | 5 setup scenarios + 5 day-to-day usage patterns |
| `docs/audit-guide.md` | How to run and interpret the audit report |
| `docs/cross-account-access.md` | POSIX/ACL/TCC permission layers on macOS |
| `docs/troubleshooting.md` | Common issues and solutions |

## Tests

```bash
python3 -m pytest tests/test_export_sessions.py -v
```

132 tests covering both formats, config loading, title extraction, source differentiation, filtering, YAML safety, and more. 100% coverage on the core export module.
