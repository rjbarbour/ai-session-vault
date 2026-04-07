# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

Exports Claude Code and Codex JSONL session history to a single Obsidian vault as searchable Markdown. The goal is to search once across all sessions — regardless of which tool, account, or machine they came from — to find past work, pull context into a current session, or export for sharing.

Currently supports Claude Code JSONL and Codex JSONL. Co-work sessions, Codex SQLite, and Claude Desktop metadata are not yet supported. No dependencies beyond Python 3 stdlib + pytest.

## Commands

```bash
# Run all tests
python3 -m pytest tests/test_export_sessions.py -v

# Run a single test
python3 -m pytest tests/test_export_sessions.py -v -k "test_name"

# Export sessions
python3 scripts/export_sessions_to_obsidian.py
python3 scripts/export_sessions_to_obsidian.py --vault /path --claude-project ~/.claude/projects/ENCODED-PATH
```

## Configuration

Paths are configured in `config.json` (gitignored). See `config.example.json` for the schema. CLI flags override config values. Without `config.json`, generic defaults are used (`~/.claude/projects`, `~/.codex/sessions`).

## Architecture

`scripts/export_sessions_to_obsidian.py` is a single-file script that:
1. Loads config from `config.json` (or uses generic defaults)
2. Auto-detects JSONL format from the first parseable line (Claude Code vs Codex)
3. Parses messages, filtering out thinking blocks, tool results, system reminders, developer messages, and reasoning
4. Summarises tool calls as one-liners (e.g. `Bash: \`ls -la\``)
5. Writes one Markdown file per session with YAML frontmatter

Legacy aliases `extract_text` and `process_message` exist at module level for backward test compatibility — they map to `claude_extract_text` and `claude_process_message`.

Tests use `tmp_path` fixtures with a `_make_jsonl` helper to create test JSONL files in-memory.

## Planned Work

See `AGENTS.md` (local, not tracked in git) for operational notes and planned work.
