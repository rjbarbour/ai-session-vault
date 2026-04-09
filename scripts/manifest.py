"""Manifest for tracking export state between runs.

The manifest reconciles two independent sources:
1. JSONL session files on disk (stat only — mtime + size)
2. Markdown files in the Obsidian vault (frontmatter only)

This enables delta export: only new or changed sessions are processed.

The manifest file lives at {vault_path}/.export_manifest.json.
"""
import json
import os
import tempfile
from pathlib import Path


MANIFEST_VERSION = 1
MANIFEST_FILENAME = ".export_manifest.json"


def load_manifest(vault_dir):
    """Load the manifest from the vault directory.

    Returns an empty manifest structure if the file is missing or corrupt.
    """
    manifest_path = Path(vault_dir) / MANIFEST_FILENAME
    try:
        with open(manifest_path) as f:
            data = json.load(f)
        if data.get("version") != MANIFEST_VERSION:
            return _empty_manifest()
        return data
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return _empty_manifest()


def save_manifest(vault_dir, manifest):
    """Save the manifest atomically (write to temp file, then rename)."""
    manifest_path = Path(vault_dir) / MANIFEST_FILENAME
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(vault_dir), suffix=".manifest.tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(manifest, f, indent=2)
        Path(tmp_path).rename(manifest_path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def scan_sources(manifest, session_files):
    """Update manifest source entries from discovered JSONL files.

    Only uses stat() — no JSONL content is read.
    session_files is a list of (source_tag, Path) tuples from find_session_files.
    """
    seen_ids = set()
    for source_tag, jsonl_path in session_files:
        session_id = jsonl_path.stem
        seen_ids.add(session_id)
        stat = jsonl_path.stat()

        entry = manifest["sessions"].setdefault(session_id, {})
        source = entry.setdefault("source", {})
        source["path"] = str(jsonl_path)
        source["mtime"] = stat.st_mtime
        source["size"] = stat.st_size
        source["source_tag"] = source_tag

    return seen_ids


def scan_vault(manifest, vault_dir):
    """Update manifest vault entries from vault frontmatter.

    Reads only the first ~20 lines of each file (frontmatter).
    """
    vault_path = Path(vault_dir)
    seen_ids = set()

    for md_file in vault_path.glob("*.md"):
        fm = _read_frontmatter(md_file)
        session_id = fm.get("session_id", "")
        if not session_id:
            continue

        seen_ids.add(session_id)
        entry = manifest["sessions"].setdefault(session_id, {})
        vault = entry.setdefault("vault", {})
        vault["filename"] = md_file.name
        vault["enriched"] = bool(fm.get("summary_short"))
        vault["title_source"] = fm.get("title_source", "")
        vault["source_mtime_at_export"] = _parse_float(fm.get("source_mtime", ""))

    # Mark vault entries as missing for sessions no longer in vault
    for session_id, entry in manifest["sessions"].items():
        if "vault" in entry and session_id not in seen_ids:
            entry["vault"]["filename"] = None

    return seen_ids


def compute_delta(manifest):
    """Compute what needs to be done based on manifest state.

    Returns a dict with action lists:
    - to_export: [(source_tag, Path)] — new sessions or changed JSONL
    - to_reexport: [(source_tag, Path, old_vault_filename)] — changed sessions with existing vault file
    - to_enrich: [vault_filename] — exported but not enriched
    - orphans: [vault_filename] — in vault but no JSONL source
    - skip: [session_id] — unchanged and enriched
    """
    to_export = []
    to_reexport = []
    to_enrich = []
    orphans = []
    skip = []

    for session_id, entry in manifest["sessions"].items():
        source = entry.get("source")
        vault = entry.get("vault")

        has_source = source and source.get("path")
        has_vault = vault and vault.get("filename")

        if has_source and not has_vault:
            # New session — not in vault yet
            to_export.append((
                source.get("source_tag", "claude"),
                Path(source["path"]),
            ))

        elif has_source and has_vault:
            # Both exist — check if source changed
            source_changed = (
                vault.get("source_mtime_at_export") is None or
                abs(source["mtime"] - (vault.get("source_mtime_at_export") or 0)) > 0.01
            )

            if source_changed:
                to_reexport.append((
                    source.get("source_tag", "claude"),
                    Path(source["path"]),
                    vault["filename"],
                ))
            elif not vault.get("enriched"):
                to_enrich.append(vault["filename"])
            else:
                skip.append(session_id)

        elif not has_source and has_vault:
            # In vault but no JSONL — orphan
            orphans.append(vault["filename"])

    return {
        "to_export": to_export,
        "to_reexport": to_reexport,
        "to_enrich": to_enrich,
        "orphans": orphans,
        "skip": skip,
    }


def update_after_export(manifest, session_id, source_tag, jsonl_path, vault_filename):
    """Update manifest after successfully exporting a session."""
    stat = jsonl_path.stat()
    entry = manifest["sessions"].setdefault(session_id, {})
    entry["source"] = {
        "path": str(jsonl_path),
        "mtime": stat.st_mtime,
        "size": stat.st_size,
        "source_tag": source_tag,
    }
    entry["vault"] = {
        "filename": vault_filename,
        "source_mtime_at_export": stat.st_mtime,
        "enriched": False,
        "title_source": "",
    }


def check_health(manifest):
    """Check vault health and return a report dict.

    Returns:
        {
            "duplicates": [(session_id, [filenames])],
            "orphans": [(session_id, vault_filename)],
            "unenriched": [(session_id, vault_filename)],
            "stale": [(session_id, vault_filename, source_mtime, vault_mtime)],
            "missing": [(session_id, expected_filename)],
        }
    """
    from collections import defaultdict

    # Find duplicates: multiple vault files with same session_id
    vault_by_id = defaultdict(list)
    for session_id, entry in manifest["sessions"].items():
        vault = entry.get("vault")
        if vault and vault.get("filename"):
            vault_by_id[session_id].append(vault["filename"])

    duplicates = [(sid, fnames) for sid, fnames in vault_by_id.items()
                  if len(fnames) > 1]

    orphans = []
    unenriched = []
    stale = []
    missing = []

    for session_id, entry in manifest["sessions"].items():
        source = entry.get("source")
        vault = entry.get("vault")

        has_source = source and source.get("path")
        has_vault = vault and vault.get("filename")

        if has_vault and not has_source:
            orphans.append((session_id, vault["filename"]))

        if has_vault and has_source and not vault.get("enriched"):
            unenriched.append((session_id, vault["filename"]))

        if has_vault and has_source:
            vault_mtime = vault.get("source_mtime_at_export")
            source_mtime = source.get("mtime")
            if vault_mtime and source_mtime and abs(source_mtime - vault_mtime) > 0.01:
                stale.append((session_id, vault["filename"], source_mtime, vault_mtime))

        if has_source and has_vault and vault.get("filename"):
            # Check if the vault file actually exists on disk
            # (caller should verify — we just flag from manifest state)
            pass

    return {
        "duplicates": duplicates,
        "orphans": orphans,
        "unenriched": unenriched,
        "stale": stale,
        "missing": missing,
    }


def update_after_enrich(manifest, session_id, vault_filename, title_source):
    """Update manifest after successfully enriching a session."""
    entry = manifest["sessions"].get(session_id, {})
    vault = entry.setdefault("vault", {})
    vault["filename"] = vault_filename
    vault["enriched"] = True
    vault["title_source"] = title_source


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _empty_manifest():
    return {"version": MANIFEST_VERSION, "sessions": {}}


def _read_frontmatter(md_path):
    """Read YAML frontmatter from the first lines of a markdown file."""
    fm = {}
    try:
        with open(md_path) as f:
            first_line = f.readline()
            if first_line.strip() != "---":
                return fm
            for line in f:
                line = line.strip()
                if line == "---":
                    break
                key, _, val = line.partition(": ")
                fm[key.strip()] = val.strip().strip('"')
    except (OSError, UnicodeDecodeError):
        pass
    return fm


def _parse_float(s):
    """Parse a string as float, return None if not parseable."""
    try:
        return float(s)
    except (ValueError, TypeError):
        return None
