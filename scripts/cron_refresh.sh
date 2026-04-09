#!/bin/bash
# Cron job for periodic delta refresh.
# Install: crontab -e, add:
#   */10 * * * * /Users/rob_dev/DocsLocal/chat_session_index/scripts/cron_refresh.sh
#
# Runs export_all.py with --skip-enrich (enrichment needs interactive claude CLI).
# Logs to cron_refresh.log in the project directory.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG="$PROJECT_DIR/cron_refresh.log"

cd "$PROJECT_DIR" || exit 1

echo "$(date '+%Y-%m-%d %H:%M:%S') — cron refresh starting" >> "$LOG"
python3 scripts/export_all.py --skip-enrich >> "$LOG" 2>&1
echo "$(date '+%Y-%m-%d %H:%M:%S') — cron refresh done" >> "$LOG"
echo "" >> "$LOG"
