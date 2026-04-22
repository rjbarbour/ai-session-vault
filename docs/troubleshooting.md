# Troubleshooting

Common issues encountered when setting up and running ai-session-vault.

## Installation Issues

### Python version
Requires Python 3.8+. macOS ships with Python 3 but `pip install pytest` may fail with PEP 668 ("externally-managed-environment"). Use a venv:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pytest
```

### No sessions found
If `export_all.py` reports 0 sessions:
- Check that `~/.claude/projects/` exists and contains JSONL files
- If using Codex, check `~/.codex/sessions/` exists
- If using `--account`, verify the account name matches a macOS username in `/Users/`

### Vault directory not found
The default vault path is `~/obsidian-session-vault`. Create it first:
```bash
mkdir -p ~/obsidian-session-vault
```
Or set a custom path in `config.json`.

## Cross-Account Issues (macOS)

### "Permission denied" on other accounts' .claude directories
Run the ACL script with sudo:
```bash
sudo bash scripts/apply_cross_account_acls.sh
```
This requires accounts to be listed in `config.json`:
```json
"accounts": ["otheraccount"]
```

### "Permission denied" on ~/Documents
macOS protects Documents, Desktop, and Downloads with TCC (Transparency, Consent and Control). ACLs alone are not sufficient.

**Fix:** Grant Full Disk Access to your terminal app:
1. System Settings > Privacy & Security > Full Disk Access
2. Add Terminal, iTerm, or Claude Desktop
3. **Restart the terminal** — TCC permissions are checked at process launch

### "Permission denied" on ~/Library/Application Support
The ACL script needs to add traverse permissions to `~/Library` and `~/Library/Application Support`, not just the Claude subdirectory. This is handled automatically — re-run the ACL script if you see this error.

### ACLs were applied but access still denied
macOS can silently remove ACLs after system updates. Re-run:
```bash
sudo bash scripts/apply_cross_account_acls.sh
```

## Enrichment Issues

### "Enrichment skipped: claude CLI not found"
The enrichment step calls `claude --model haiku -p` to generate titles and summaries. Install and authenticate Claude Code CLI:
```bash
claude --version
claude /login
```
If you don't want enrichment, use `--skip-enrich`.

### Cron enrichment stopped working after a Claude CLI update
The cron job reads the Claude CLI's OAuth token from the login Keychain via a partition-list entry trusting Anthropic's Developer ID team. If Anthropic ever changes their signing identity, the partition-list entry no longer matches and enrichment fails with "Not logged in" errors in `cron_refresh.log`.

**First thing to check:**
```bash
codesign -dvv "$(readlink -f "$(which claude)")" 2>&1 | grep TeamIdentifier
```
If `TeamIdentifier` is no longer `Q6L2SF6YDW`, update the team ID in `scripts/authorise_keychain_for_cron.sh` and re-run it. See the "Keychain setup" section in the README for the full explanation of the trust model.

### Enrichment fails on large sessions
Sessions exceeding Haiku's context window (~200K tokens) are automatically truncated to first 20 + last 20 turns. If it still fails, the session is skipped — the exported Markdown is still in the vault, just without the AI-generated title/summary.

### Keywords returned as list instead of string
Haiku occasionally returns keywords as a JSON array instead of a comma-separated string. The script handles this automatically.

## Vault Issues

### Duplicate files in vault
After enrichment renames files (new title → new slug → new filename), old copies may remain. Run:
```bash
python3 scripts/dedupe_vault.py
```
Duplicates are moved to `.deleted/` inside the vault (not permanently deleted). Remove `.deleted/` manually when confident.

### Enrichment artefacts in vault
`claude -p` enrichment calls create their own session JSONL files, which can be re-exported as vault entries. These are filtered by:
1. Single-turn + queue-operation/enqueue signature
2. Content starting with "Enrich this session:" or "Generate a title"

If you see vault entries that look like enrichment prompts rather than real sessions, re-export with the latest code.

### Wrong account attribution
Co-work sessions use `/sessions/<name>` as their cwd instead of a real path. The account is extracted from the JSONL file path instead. If you see blank `account:` fields, re-export with the latest code.

## iCloud Issues (macOS)

### Projects moved by iCloud Optimize Storage
When macOS moves `~/Documents` contents into an iCloud subfolder (`Documents - TMD's MacBook Pro/`), session cwds reference the old paths. The audit script handles this via path aliases, but the vault `project:` field will show the path from when the session was created.

### Desktop sessions disappear after Claude Code update
Known bug (anthropics/claude-code#29373). Claude Desktop may wipe session UI state during updates, losing parent conversations that were never written to JSONL. Subagent files and git commits survive. No recovery possible without Time Machine.

## Performance

### Export is slow
Export itself is fast (seconds). Enrichment is the slow part — each session makes a Haiku API call. Use `--workers 10` (default) for parallelism, or `--skip-enrich` to skip entirely.

### Audit is slow on accounts with many JSONL files
The interactive session filter reads the first few lines of every JSONL file to check for the queue-operation/enqueue signature. For accounts with hundreds of files (like robfo with 275), this takes a few seconds.
