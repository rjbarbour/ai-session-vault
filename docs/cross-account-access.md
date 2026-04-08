# Cross-Account Access on macOS

## The Problem

Session data is distributed across multiple macOS user accounts. To export and audit sessions from other accounts, `rob_dev` needs read access to their files. macOS has three layers of access control that all must be satisfied.

## Three Layers of Access Control

### 1. Unix Permissions (POSIX)

Home directories are `drwx------` (700) — owner only. Other users can't traverse the directory, so nothing inside is accessible regardless of file-level permissions.

**Fix:** ACL on the home directory granting execute (traverse) permission:
```bash
sudo chmod +a "rob_dev allow execute,readattr,search" /Users/<account>
```

### 2. ACLs (Access Control Lists)

macOS ACLs extend POSIX permissions. They support inheritance so new files/dirs created inside also grant access.

**Fix:** The `apply_cross_account_acls.sh` script applies:
```
rob_dev allow read,execute,readattr,readextattr,readsecurity,list,search,file_inherit,directory_inherit
```

To: `~/.claude`, `~/.codex/sessions`, `~/Library/Application Support/Claude`, `~/Documents`

**Gotchas:**
- `.codex` root contains SIP-protected binaries — target `.codex/sessions` instead
- `Application Support/Claude` may contain dangling symlinks (`latest`) — use `-h` flag to not follow symlinks
- ACLs must be applied AFTER the home directory traverse permission, or they can't be reached

### 3. TCC (Transparency, Consent and Control)

macOS protects `~/Documents`, `~/Desktop`, and `~/Downloads` with TCC, **independent of Unix permissions and ACLs**. Even `sudo chmod +a` on Documents won't help if the accessing process doesn't have TCC authorization.

**Fix:** Grant **Full Disk Access** to the terminal application:
- System Settings > Privacy & Security > Full Disk Access
- Add Terminal, iTerm, and/or Claude Desktop
- **Must restart the terminal process** for the grant to take effect (TCC is checked at process launch)

**What TCC does NOT protect:** `~/.claude`, `~/.codex`, `~/Library/Application Support/` — these are accessible with just POSIX + ACL permissions.

## What the ACL Script Covers

`scripts/apply_cross_account_acls.sh` handles all of this:

1. Reads account list from `config.json`
2. Applies traverse permission to home directories
3. Applies read ACLs to session data directories
4. Applies read ACLs to Documents (requires FDA)
5. Logs results to `acl_apply.log`
6. Verifies accessibility

```bash
sudo bash scripts/apply_cross_account_acls.sh        # apply
sudo bash scripts/apply_cross_account_acls.sh --remove  # remove
```

## Access Matrix

| Directory | POSIX | ACL | TCC/FDA | Script handles |
|---|:---:|:---:|:---:|:---:|
| `~/.claude/` | Need home traverse | Yes | No | ✅ |
| `~/.codex/sessions/` | Need home traverse | Yes | No | ✅ |
| `~/Library/Application Support/Claude/` | Need home traverse | Yes | No | ✅ |
| `~/Documents/` | Need home traverse | Yes | **Yes — needs FDA** | ✅ (ACL only; FDA is manual) |
| `~/Desktop/` | Need home traverse | Yes | **Yes — needs FDA** | Not targeted |
| `~/Downloads/` | Need home traverse | Yes | **Yes — needs FDA** | Not targeted |

## Persistence

- **ACLs** can be silently removed by macOS updates or permission resets. Re-run the script if access stops working.
- **Home directory traverse** permission persists unless the home directory permissions are reset.
- **Full Disk Access** persists across reboots but must be re-granted after major macOS updates.
- **Inheritance flags** ensure new files/dirs created inside ACL'd directories also grant access.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| "Permission denied" on `~/.claude` | Missing ACL or home traverse | Re-run ACL script with sudo |
| "Permission denied" on `~/Documents` | TCC blocking | Grant FDA to terminal app, restart terminal |
| "Operation not permitted" on `.codex` binaries | SIP protection | Expected — script targets `.codex/sessions` not `.codex` root |
| ACLs applied but still denied | Home dir not traversable | Script now handles this automatically |
| Access works in Terminal but not Claude Desktop | FDA granted to wrong app | Add Claude Desktop to FDA list |
