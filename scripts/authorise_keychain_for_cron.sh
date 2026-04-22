#!/bin/bash
# Authorise the Claude CLI to read its OAuth token from the login Keychain
# without a UI prompt, so cron/launchd can run `claude -p` non-interactively.
#
# This adds Anthropic's Developer ID team (Q6L2SF6YDW) to the partition list
# of the "Claude Code-credentials" Keychain item. After this, ANY binary
# signed by that team can read the token — so version bumps of the claude CLI
# (which happen multiple times a day) are trusted automatically, with no
# re-authorisation needed.
#
# Run once. You'll be prompted for your macOS login password on the TTY.
#
# IF IT STOPS WORKING AFTER A CLAUDE CLI UPDATE:
# The most likely cause is Anthropic changing their signing identity (team ID
# or bundle identifier). Check with:
#   codesign -dvv "$(readlink -f "$(which claude)")" 2>&1 | grep -E 'TeamIdentifier|Identifier'
# If the team ID is no longer Q6L2SF6YDW, update this script and re-run.

set -euo pipefail

TEAM_ID="Q6L2SF6YDW"          # Anthropic PBC (as of claude CLI 2.1.x)
SERVICE="Claude Code-credentials"
ACCOUNT="${USER}"
KEYCHAIN="${HOME}/Library/Keychains/login.keychain-db"

# Verify the specific (service, account) item exists before we try to
# authorise it. Avoids a cryptic "item could not be found" on fresh machines
# where `claude /login` has never been run. Metadata-only lookup, does not
# touch the password.
if ! security find-generic-password -s "${SERVICE}" -a "${ACCOUNT}" \
      "${KEYCHAIN}" >/dev/null 2>&1; then
  echo "Error: no '${SERVICE}' item for account '${ACCOUNT}' in ${KEYCHAIN}." >&2
  echo "Run 'claude /login' first to sign in and create the Keychain entry." >&2
  exit 1
fi

echo "Adding team ID ${TEAM_ID} to partition list for ${SERVICE} (${ACCOUNT})"
echo "You will be prompted for your macOS login password."
echo

security set-generic-password-partition-list \
  -S "apple-tool:,apple:,teamid:${TEAM_ID}" \
  -s "${SERVICE}" \
  -a "${ACCOUNT}" \
  "${KEYCHAIN}"

echo
echo "Done. Verify with a cron-like environment:"
echo "  env -i HOME=\"\$HOME\" USER=\"\$USER\" PATH=\"\$HOME/.local/bin:/usr/bin:/bin\" TMPDIR=\"\$TMPDIR\" claude -p --model haiku 'hello'"
echo
echo "(HOME and USER are required — claude uses them to locate ~/.claude/."
echo " Cron inherits both by default, so cron itself will work.)"
