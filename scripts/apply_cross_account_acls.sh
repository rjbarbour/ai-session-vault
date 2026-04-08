#!/bin/bash
# Apply read-only ACLs so the current user can read Claude/Codex session data
# and project directories from other macOS accounts.
#
# Reads account list from config.json ("accounts" array).
# Must be run with sudo: sudo bash scripts/apply_cross_account_acls.sh
#
# NOTE: ~/Documents, ~/Desktop, ~/Downloads are protected by macOS TCC
# (Transparency, Consent and Control) in addition to Unix permissions.
# ACLs alone are not sufficient — Terminal must have Full Disk Access:
#   System Settings > Privacy & Security > Full Disk Access > add Terminal
#
# To remove: re-run with --remove

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../config.json"
LOG_FILE="$SCRIPT_DIR/../acl_apply.log"

log() {
    echo "$1"
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG_FILE"
}

if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "Error: config.json not found. Copy config.example.json and add an 'accounts' array." >&2
    exit 1
fi

ACL_USER=$(python3 -c "
import json, os
cfg = json.load(open('$CONFIG_FILE'))
print(cfg.get('acl_user', os.environ.get('SUDO_USER', '$(whoami)')))
")

ACCOUNTS=$(python3 -c "
import json
cfg = json.load(open('$CONFIG_FILE'))
print(' '.join(cfg.get('accounts', [])))
")

if [[ -z "$ACCOUNTS" ]]; then
    echo "No accounts configured in config.json. Add an 'accounts' array, e.g.:"
    echo '  "accounts": ["otheruser1", "otheruser2"]'
    exit 0
fi

if [[ "${1:-}" == "--remove" ]]; then
    ACTION="remove"
else
    ACTION="apply"
fi

if [[ $(id -u) -ne 0 ]]; then
    echo "Error: must run with sudo" >&2
    exit 1
fi

ACL_ENTRY="$ACL_USER allow read,execute,readattr,readextattr,readsecurity,list,search,file_inherit,directory_inherit"

log "=== ACL $ACTION started for accounts: $ACCOUNTS ==="
log "ACL user: $ACL_USER"
log ""

# Ensure home directories and intermediate paths are traversable.
# Without traverse on ~/Library and ~/Library/Application Support,
# the ACL on ~/Library/Application Support/Claude can't be reached.
for account in $ACCOUNTS; do
    for dir in \
        "/Users/$account" \
        "/Users/$account/Library" \
        "/Users/$account/Library/Application Support"; do
        if [[ -d "$dir" ]]; then
            if chmod +a "$ACL_USER allow execute,readattr,search" "$dir" 2>/dev/null; then
                log "  TRAVERSE OK: $dir"
            else
                log "  TRAVERSE FAILED: $dir"
            fi
        fi
    done
done
log ""

# Target session data and project directories per account.
for account in $ACCOUNTS; do
    log "--- $account ---"
    for dir in \
        "/Users/$account/.claude" \
        "/Users/$account/.codex/sessions" \
        "/Users/$account/Library/Application Support/Claude" \
        "/Users/$account/Documents"; do

        if [[ ! -e "$dir" ]]; then
            log "  SKIP (not found): $dir"
            continue
        fi

        if [[ "$ACTION" == "remove" ]]; then
            if chmod -R -a "$ACL_ENTRY" "$dir" 2>/dev/null; then
                log "  REMOVE OK: $dir"
            else
                log "  REMOVE PARTIAL: $dir (some entries may remain)"
            fi
        else
            # Apply to the directory itself first
            if chmod +a "$ACL_ENTRY" "$dir" 2>/dev/null; then
                log "  APPLY OK: $dir"
            else
                log "  APPLY FAILED: $dir (may need Full Disk Access for TCC-protected folders)"
            fi
            # Then recursively to contents (errors filtered for known non-issues)
            fail_count=$(chmod -R -h +a "$ACL_ENTRY" "$dir" 2>&1 | grep -cv "Operation not permitted\|No such file or directory\|^$" || true)
            if [[ "$fail_count" -gt 0 ]]; then
                log "    WARN: $fail_count unexpected errors during recursive apply"
            fi
        fi
    done
    log ""
done

# Verify access
log "=== Verification ==="
for account in $ACCOUNTS; do
    for dir in ".claude" "Documents"; do
        target="/Users/$account/$dir"
        if [[ -d "$target" ]] && ls "$target" >/dev/null 2>&1; then
            log "  READABLE: $target"
        elif [[ -d "$target" ]]; then
            log "  NOT READABLE: $target (TCC may be blocking — grant Terminal Full Disk Access)"
        fi
    done
done

log ""
log "Log written to: $LOG_FILE"
log "Done."
