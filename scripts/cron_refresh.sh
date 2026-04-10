#!/bin/bash
# Cron job for periodic delta refresh.
# Install: crontab -e, add:
#   */10 * * * * /Users/rob_dev/DocsLocal/chat_session_index/scripts/cron_refresh.sh
#
# Runs the full pipeline including enrichment.
# Logs to cron_refresh.log in the project directory.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG="$PROJECT_DIR/cron_refresh.log"

# Cron has minimal PATH — add locations for python3 and claude CLI
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin:$PATH"

cd "$PROJECT_DIR" || exit 1

echo "$(date '+%Y-%m-%d %H:%M:%S') — cron refresh starting" >> "$LOG"
python3 scripts/export_all.py >> "$LOG" 2>&1
echo "$(date '+%Y-%m-%d %H:%M:%S') — cron refresh done" >> "$LOG"
echo "" >> "$LOG"
