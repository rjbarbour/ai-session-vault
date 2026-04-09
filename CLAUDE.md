# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

Exports Claude Code, Codex, and Co-work session history to a single Obsidian vault as searchable Markdown. The goal is to search once across all sessions — regardless of which tool, account, or machine they came from — to find past work, pull context into a current session, or export for sharing.

Supports Claude Code CLI, Claude Desktop, Claude Co-work, and Codex JSONL. No dependencies beyond Python 3 stdlib + pytest.

## Commands

```bash
# Run all tests
python3 -m pytest tests/test_export_sessions.py -v

# Run a single test
python3 -m pytest tests/test_export_sessions.py -v -k "test_name"

# Full pipeline (delta — only new/changed sessions)
python3 scripts/export_all.py

# Full re-export ignoring manifest
python3 scripts/export_all.py --full

# Export without enrichment
python3 scripts/export_all.py --skip-enrich

# Setup/preflight checks
python3 scripts/setup.py

# Vault health check
python3 scripts/vault_health.py

# Audit a specific account
python3 scripts/audit_sessions.py --account rob_dev
```

## Configuration

Paths are configured in `config.json` (gitignored). See `config.example.json` for the schema. CLI flags override config values. Without `config.json`, generic defaults are used (`~/.claude/projects`, `~/.codex/sessions`).

## Architecture

### Package structure

`scripts/` is a Python package. Shared utilities live in `scripts/utils.py`:
- `load_config`, `check_dir`, `slugify`, `check_claude_cli`
- `parse_frontmatter_file`, `parse_frontmatter_text`
- `resolve_account_paths`, `extract_account`, `resolve_vault`
- `atomic_write`

### Core modules

- `export_sessions_to_obsidian.py` — JSONL parsing, format detection, session export, title extraction from multiple sources (custom, Desktop, Codex, Co-work, first message)
- `manifest.py` — delta state tracking: `load_manifest`, `save_manifest`, `scan_sources`, `scan_vault`, `compute_delta`, `check_health`
- `enrich_sessions.py` — Haiku enrichment: titles, summaries, keywords. Parallel workers. Content-based artefact filtering.
- `export_all.py` — pipeline orchestrator: discover → scan → export → dedupe → enrich → health → audit

### Key patterns

- Legacy aliases `extract_text` and `process_message` exist at module level for backward test compatibility
- `is_interactive_session()` filters out `claude -p` calls using dual check: single-turn + queue-operation/enqueue, plus content signature matching
- Manifest uses `mtime + size` for JSONL change detection (append-only files)
- Discovery caches `is_interactive` per session in manifest — known non-interactive files are stat-only on subsequent runs (no file read)
- Vault files are never deleted by the pipeline — duplicates go to `.deleted/`, orphans are flagged not removed

## Planned Work

See `PLAN.md` (local, not tracked in git) for operational notes, planned work, and phase status.

## Git Workflow

Always use feature branches and PRs. Never push directly to main.
