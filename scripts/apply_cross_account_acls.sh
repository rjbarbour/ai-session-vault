#!/bin/bash
# Apply read-only ACLs so the current user can read Claude/Codex session data
# from other macOS accounts.
#
# Reads account list from config.json ("accounts" array).
# Must be run with sudo: sudo bash scripts/apply_cross_account_acls.sh
#
# To remove: re-run with --remove

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../config.json"

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

# Target session data directories per account.
# Uses .codex/sessions (not .codex root) to avoid SIP-protected binaries.
for account in $ACCOUNTS; do
    for dir in \
        "/Users/$account/.claude" \
        "/Users/$account/.codex/sessions" \
        "/Users/$account/Library/Application Support/Claude"; do

        if [[ ! -e "$dir" ]]; then
            echo "  SKIP (not found): $dir"
            continue
        fi
        if [[ "$ACTION" == "remove" ]]; then
            echo "  REMOVE ACL: $dir"
            chmod -R -a "$ACL_ENTRY" "$dir" 2>/dev/null || true
        else
            echo "  APPLY ACL:  $dir"
            chmod -R -h +a "$ACL_ENTRY" "$dir" 2>&1 | grep -v "Operation not permitted\|No such file or directory" || true
        fi
    done
done

echo ""
echo "Done. Verify with: ls -le /Users/<account>/.claude/"
