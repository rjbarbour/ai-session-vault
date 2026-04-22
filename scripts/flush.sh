#!/bin/bash
# On-demand vault flush (current account only, no enrichment).
#
# Intended for situations where waiting for the next cron tick is too slow,
# most commonly "flush my current session to the vault before /compact".
#
# Enrichment (AI title/summary/keywords) is deliberately skipped — the next
# cron tick will pick up any unenriched files automatically via
# enrich_sessions.py --skip-enriched. This keeps flush latency in the
# single-second range for the common case.
#
# Exit codes: 0 = success (check stdout for delta), non-zero = failure.
# Output: verbatim stdout from export_sessions_to_obsidian.py.
#
# Contract: docs/superpowers/plans/2026-04-22-on-demand-flush.md

set -euo pipefail

# Invoke this script via its real path, not via a symlink. SCRIPT_DIR is
# derived from $0's directory, so a symlink at e.g. ~/bin/flush.sh would
# resolve PROJECT_DIR to ~, not the repo.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Homebrew-owned python3.13 lives in /opt/homebrew/bin — see the user's
# global CLAUDE.md Python standard. Ordering this first ensures we pick up
# the canonical interpreter even in stripped shells.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin:$PATH"

cd "$PROJECT_DIR"

# exec hands off to Python directly — no post-processing in the shell.
# Python's exit code and stdout/stderr reach the caller verbatim.
exec python3 scripts/export_sessions_to_obsidian.py
