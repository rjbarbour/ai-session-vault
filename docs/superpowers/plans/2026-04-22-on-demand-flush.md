# Plan: On-demand vault flush (`scripts/flush.sh`)

## Intent

Provide a fast, synchronous, enrichment-free export script that a Claude Code skill (or slash command, or terminal user) can invoke to force the current account's changed sessions into the vault on demand.

Primary use case: flush before running `/compact`. The Claude Code user wants their current conversation preserved as Markdown in the vault before compaction rewrites the in-memory turns. The `/compact` operation is near-synchronous and the user expects to kick off the flush and run `/compact` moments later — so the flush must complete in seconds, not minutes.

Secondary use case: any situation where waiting up to 10 minutes for the next cron tick is too long (e.g. "I want to search for something I just did").

## Design decisions

1. **Export only, no enrichment.** The current cron pipeline already enriches any file missing `summary_short` on every 10-minute run. Anything written by flush will therefore be enriched automatically within 10 minutes of the next cron tick. Enrichment is 30–120s per session and inherently network-bound (Haiku call); blocking the flush on it would defeat the "before `/compact`" use case.

2. **Current account only.** Cross-account export requires TCC approval and interactive shell context. Flush is invoked from an interactive Claude Code session, so TCC is fine, but cross-account export is out of scope for the fast-path. If the user needs cross-account refresh, they run `export_all.py` explicitly — same split as cron already enforces.

3. **Delta behaviour, not full re-export.** The manifest-based delta in `export_sessions_to_obsidian.py` correctly identifies the current session's JSONL as "changed" (it grew since last export) and processes it alongside any other changed sessions. No need to identify "the current session" explicitly — the delta naturally picks it up.

4. **No dedupe, no health check, no audit.** Those belong in the full pipeline (`export_all.py`), not the fast-path.

5. **Separate script, not a flag on `cron_refresh.sh`.** `cron_refresh.sh` is purposefully named and scheduled — its role is the unattended periodic loop. `flush.sh` has a different intent (on-demand, fast, no enrichment). Two short scripts with clear names beats one flag-laden script.

## Assumptions (verified against current code, 2026-04-22)

1. **JSONL-flush race.** Flush captures whatever Claude Code has persisted to the session JSONL at invocation time. If the most recent turn has not yet been fsync'd, it will be captured on the next flush or cron tick. This is the single most likely cause of user-visible "I flushed but my last turn isn't there" confusion — the skill must communicate it explicitly, and the framework side cannot fix it.
2. **Missing `config.json`.** Flush inherits the defaults from `utils.load_config()` (generic paths: `~/.claude/projects`, `~/.codex/sessions`). Works on fresh clones; no hard requirement for config.
3. **Missing vault directory.** Flush exits 1 with the literal stderr message `Vault directory not found: <path>`. The skill may match this substring for user-facing messaging ("run `python3 scripts/setup.py` to create the vault"), but matching is not required — surfacing stderr verbatim is sufficient.
4. **SIGINT mid-export.** The manifest is saved at the end of the run via `atomic_write`, and per-file writes also use `atomic_write`. If Ctrl-C interrupts mid-run, any files written before the interrupt exist correctly; the manifest was not updated. The next run's vault scan reconciles: the existing files are detected in the vault scan, nothing is re-exported unnecessarily. Self-heals. No cleanup required.
5. **Multi-account / multi-user.** Each account has its own vault path (via `config.json` per-user), so concurrent flushes on a shared machine don't contend. Documented for completeness; not a design constraint.

## Script contract: `scripts/flush.sh`

### Purpose
Force an on-demand delta export of the current account's changed sessions, skipping enrichment.

### Inputs
No arguments. Reads configuration from `config.json` via `export_sessions_to_obsidian.py`.

Environment required:
- `HOME`, `USER` — standard shell variables. Always present in interactive shells and in cron.
- `PATH` — script extends `PATH` to find `python3`.

No optional flags in v1. If a future need arises (e.g. `--dry-run`), they can be added without breaking this contract.

### Exit codes
- `0` — export completed. Zero or more sessions may have been written; the caller checks stdout to see what (if anything) changed.
- Non-zero — export failed. stderr will contain the diagnostic. The caller should surface this to the user and not claim the flush succeeded.

### stdout
Verbatim passthrough of `export_sessions_to_obsidian.py`'s stdout, which already emits structured human-readable lines:

```
Found N session files (X Claude, Y Codex, Z Co-work)
Exporting to: /path/to/vault
Delta: N to export, M unchanged, K to enrich
  [claude-cli] 2026-04-22_claude-cli_some-session_abc12345.md (45.2 KB)
  [codex     ] 2026-04-22_codex_another-session_def67890.md (12.8 KB)

Exported 2 sessions
```

No machine-readable format (JSON, etc.) in v1. The final line `Exported N sessions` is the reliable summary and can be grep'd by the caller if needed.

### stderr
Diagnostic-only:
- "Vault directory not found: ..." → exit 1
- Permission errors
- Any unexpected traceback from the Python export

Normal "no changes" case is **not** a stderr event — it just means the Delta line shows `0 to export` and no per-session lines are emitted. stdout reports it clearly.

### Timing expectations
- Typical case (current session only, small delta): **typically under 3 seconds**. The manifest-based discovery scan cost grows with session count; on a machine with hundreds of tracked sessions it may approach 5–10s.
- Large delta (many sessions changed since last export): **under 30 seconds**.
- Worst case (full re-export due to deleted manifest): minutes — but this only happens if someone deleted `.manifest.json`, not during normal use.

### Side effects
- Writes/updates Markdown files in the configured vault directory.
- Updates `<vault>/.manifest.json`.
- **Does not** call `enrich_sessions.py`.
- **Does not** call `dedupe_vault.py`.
- **Does not** run health checks or audits.
- **Does not** touch the `cron_refresh.log`.

### Concurrency
- Safe to run concurrently with `cron_refresh.sh`: the manifest is written atomically (temp file + rename), and `export_sessions_to_obsidian.py` uses `atomic_write` for individual vault files. Worst case is one run observes the other's mid-state — both runs still produce correct output.
- If a user invokes flush twice in quick succession, the second run sees the first's manifest updates and correctly identifies zero changes.

## Implementation

```bash
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

set -euo pipefail

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
```

Ten lines of body. `set -e` covers the `cd` failure (no belt-and-braces `|| exit 1`). `exec` is intentional and terminates the shell wrapper; nothing after `exec` runs, which is fine because we don't need post-processing.

## Skill-facing contract

The skill (to be built later, separately) needs to know:

**Invocation.** From any working directory:
```
/Users/rob_dev/DocsLocal/chat_session_index/scripts/flush.sh
```

**Success path.** Exit 0. Capture stdout. Report to the user either:
- The final `Exported N sessions` line, optionally with the per-file lines if the user wants detail.
- If the delta was zero (stdout contains `Delta: 0 to export`), report "Vault already up to date".

**Failure path.** Non-zero exit. Capture stderr. Surface it to the user verbatim with a short prefix like "Flush failed:". Do not claim the flush succeeded.

**Timing.** Synchronous. Typical completion under 3 seconds. If the subprocess takes longer than ~60s, something is wrong and the skill may want to time out (up to its judgment).

**No post-conditions promised beyond stdout.** The skill should not assume a specific file was written — it should report whatever the script says was written. A flush that exports zero sessions is still a successful flush (the vault was already up to date).

**Race with Claude Code's own turn persistence.** If the most recent turn is missing from the exported Markdown, it was not yet persisted to the JSONL by Claude Code at the moment flush ran. The skill **must not** treat this as a flush failure — it's expected behaviour. A second flush a moment later will usually capture the missing turn.

**Enrichment note for the user-facing message.** The skill should make it clear that the session is preserved *without* summaries yet, and those will appear within 10 minutes via the cron enrichment pass. E.g. "Session flushed to vault. Summary and keywords will appear within 10 minutes as the next cron tick enriches it."

## Test plan

1. **Smoke test.** Run `./scripts/flush.sh` from the project directory. Expect exit 0 and a summary line.
2. **Zero-delta test.** Immediately re-run. Expect stdout contains `Delta: 0 to export` and exit 0. Confirms zero-sessions is success, not error.
3. **From-elsewhere test.** Run the absolute path from `~/` or `/tmp`. Expect identical behaviour (the `cd "$PROJECT_DIR"` line handles this).
4. **Stripped-env test.** Run with `env -i HOME="$HOME" USER="$USER" PATH="/usr/bin:/bin" /Users/rob_dev/DocsLocal/chat_session_index/scripts/flush.sh`. Expect success — proves the internal PATH extension covers stripped environments.
5. **Concurrent-run test.** Trigger `cron_refresh.sh` in the background, then run flush. Both should succeed; no corrupted manifest.
6. **Missing-vault test.** Temporarily rename the vault directory and run flush. Expect exit 1 with exactly `Vault directory not found: <path>` on stderr. Confirms the contract's quoted error string.
7. **No-config test.** Temporarily rename `config.json` aside and run flush. Expect success using the generic defaults from `utils.load_config()`. Documents that flush works on fresh clones.
8. **SIGINT test.** Run flush, Ctrl-C mid-export (if a large delta allows). Re-run immediately. Expect the second run to complete cleanly and the vault to contain all the right files — demonstrates the self-healing via vault scan.

## Out of scope

Explicit list of things this plan does NOT cover, by design:

- **The skill itself.** Trigger phrases, user-facing messaging, slash-command glue, PLAN.md/SKILL.md updates — all skill-side work, handled in a separate skill session against a different repo.
- **Cross-account flush.** Still manual via `export_all.py`.
- **Flushing enrichment.** Deliberately excluded; cron handles it.
- **Notification when enrichment arrives.** The skill could implement this with a follow-up query, but it's a nice-to-have, not part of this script's contract.
- **Machine-readable output.** v1 is human-readable only. JSON output can be added later without breaking the contract if a caller needs it.
- **Structured logging.** v1 writes to stdout/stderr directly; no logfile. If needed later, the caller can `tee` or `2>&1 | ...` as required.

## Files to create

| File | Change |
|------|--------|
| `scripts/flush.sh` | New, ~30 lines including comments. Body per the implementation sketch. |
| `README.md` | (a) Add a row in the "Scripts" table: `flush.sh | On-demand vault flush, current account, no enrichment`. (b) Add a 3-line note in the "Automated refresh" section pointing out that flush exists for "before `/compact`" and similar on-demand use. |
| `CLAUDE.md` | Add one bullet in the Commands section: `./scripts/flush.sh — on-demand vault flush (current account, no enrichment).` |

## Verification before the skill is written

After building `flush.sh` on this repo:
1. Run the six tests above manually.
2. Commit and open a PR.
3. Let PR review validate the contract before the skill is authored against it. This matters because the skill's PR will depend on the contract being stable.

Once merged, the skill session can be given a task that specifies: "invoke `/Users/rob_dev/DocsLocal/chat_session_index/scripts/flush.sh`, capture stdout+exit code, report per the contract in `docs/superpowers/plans/2026-04-22-on-demand-flush.md`."
