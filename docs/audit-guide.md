# Session Audit Guide

## What the Audit Covers

The audit script scans five data sources and cross-references them against the Obsidian vault to produce a coverage report:

| Source | What it scans | How it counts |
|---|---|---|
| CLI | `~/.claude/projects/<encoded-path>/*.jsonl` | Interactive sessions only (filters out `claude -p` calls using dual check: <2 user messages + queue-operation enqueue) |
| Desktop | `~/Library/Application Support/Claude/claude-code-sessions/**/local_*.json` | Metadata JSON files — counts by `cwd` field |
| Codex | `~/.codex/sessions/**/rollout-*.jsonl` | `cwd` from `session_meta` payload |
| Co-work | `~/Library/Application Support/Claude/local-agent-mode-sessions/**/local_*.json` + `**/*.jsonl` | Metadata JSON for named session count, JSONL files (excluding `audit.jsonl`) for exportable session count |
| Vault | `<vault_path>/*.md` | Parses YAML frontmatter for `project` and `source` fields |

The audit also scans project root directories (`~/DocsLocal/`, `~/Documents/GitHub/`, `~/Documents/TMD/GitHub/`, `~/Documents/TMD/Projects/`) and checks each for a `.claude` directory.

### iCloud Path Aliases

The audit handles iCloud Optimize Storage path moves by building aliases between pre-move paths (`~/Documents/Projects/X`) and TMD paths (`~/Documents/Documents - TMD's MacBook Pro/Projects/X`). Sessions whose `cwd` references the old path are matched to the TMD project directory.

## How to Run

```bash
# Print to stdout
python3 scripts/audit_sessions.py

# Save to file
python3 scripts/audit_sessions.py --output audit_rob_dev_2026-04-08.md

# Specify a different vault
python3 scripts/audit_sessions.py --vault /path/to/vault

# Specify account name (defaults to current user)
python3 scripts/audit_sessions.py --account robfo
```

## How to Interpret the Report

### Main Table Columns

| Column | Meaning |
|---|---|
| Root | Parent directory containing the project |
| Project | Project directory name |
| .claude | Whether the project has a `.claude/` directory |
| CLI | Interactive CLI sessions found in `~/.claude/projects/` |
| Desktop | Desktop metadata sessions found in `claude-code-sessions/` |
| Codex | Codex JSONL sessions found in `~/.codex/sessions/` |
| Co-work | Co-work JSONL files found in `local-agent-mode-sessions/` |
| Total | Sum of all sources |
| In Vault | Markdown files in the Obsidian vault matching this project path |
| Found | ✅ if any sessions exist, ❌ if none |
| All In | ✅ if vault >= total, ❌ if vault < total, ➖ if no sessions |

### Known Counting Discrepancies

- **Vault > Total**: Normal when subagent files are exported (e.g. PromptKit shows 2 Desktop sessions but 10 in vault because 8 subagent JSONL files were also exported)
- **Desktop sessions showing as gaps**: Desktop and CLI often share the same underlying JSONL file. The audit counts Desktop metadata separately, so a session counted as both Desktop (1) and CLI (1) shows Total=2 but only 1 vault entry. This is a counting artefact, not missing data.

### Known Data Losses (rob_dev)

These sessions had their parent conversation visible in Claude Desktop but the JSONL was never written to disk (early Desktop version bug) and the Desktop UI state was wiped during a Claude Code update (known bug: anthropics/claude-code#29373). Only subagent work products survive where they exist.

- **TSC / "Create HubSpot landing page for Single Circle"** — 6-hour session, 11 git commits, 7 subagent files exported. Parent conversation lost.
- **genealogy / "Create Python scripts for local GEDCOM analysis"** — no JSONL or subagent files. Entirely lost.
- **PromptKit / "Check HeyPresto functionality status"** — no JSONL. Lost.
- **PromptKit / "Add Hey Presto MCP HTTP transport"** — no JSONL. Lost.

The audit script currently shows TSC and PromptKit as ✅ because subagent files are in the vault. This is misleading — the parent conversations are missing. These should be treated as partial coverage at best.

### Orphan Sessions

Sessions whose `cwd` doesn't match any scanned project root. Common causes:
- Pre-iCloud paths (`~/Documents/Projects/X`) — content is in vault under TMD alias
- Co-work session names (`/sessions/awesome-fervent-hypatia`) — content is in vault as `claude-cowork`

## Full Workflow: Export, Enrich, Dedupe, Audit

```bash
# 1. Export all sessions to vault
python3 scripts/export_sessions_to_obsidian.py

# 2. Enrich with Haiku (titles, summaries, keywords) — parallel
python3 scripts/generate_titles.py --skip-enriched --workers 10

# 3. Remove duplicates (enrichment renames files, leaving old copies)
python3 scripts/dedupe_vault.py              # remove duplicates
python3 scripts/dedupe_vault.py --dry-run    # preview what would be removed

# 4. Run audit to verify coverage
python3 scripts/audit_sessions.py --output audit_rob_dev_$(date +%Y-%m-%d).md
python3 scripts/audit_sessions.py --account robert   # audit other accounts

# 5. Render the report
cat audit_rob_dev_$(date +%Y-%m-%d).md
```

### Deduplication

`dedupe_vault.py` groups vault files by `session_id` from frontmatter. When multiple files share the same session_id, it scores each by:
1. Has summary (enriched) — +100
2. Title source quality (generated > desktop > codex > custom > first_message)
3. Has keywords — +10
4. File size tiebreaker

The highest-scoring file is kept; the rest are deleted.

## Rendering the Report Inline

When presenting to the user, output the full markdown content so tables render properly. Don't truncate or summarise — show the complete table, summary, gaps, and orphan sections.
