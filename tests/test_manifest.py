"""Tests for manifest.py"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from manifest import (
    load_manifest, save_manifest, scan_sources, scan_vault,
    compute_delta, update_after_export, update_after_enrich,
    check_health, quick_check_sources, is_known_noninteractive,
    cache_interactive_status, _empty_manifest, _parse_float,
    MANIFEST_VERSION,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vault_md(vault_dir, session_id, enriched=False, source_mtime=None):
    """Create a minimal vault markdown file with frontmatter."""
    lines = ["---", f"session_id: {session_id}", "source: claude-cli"]
    if source_mtime:
        lines.append(f"source_mtime: {source_mtime}")
    if enriched:
        lines.append('summary_short: "A summary"')
        lines.append('title_source: generated')
    lines.extend(["---", "", f"# Session {session_id}"])
    (vault_dir / f"test_{session_id}.md").write_text("\n".join(lines))


def _make_jsonl(tmp_path, name, content="{}"):
    """Create a JSONL file."""
    f = tmp_path / f"{name}.jsonl"
    f.write_text(content + "\n")
    return f


# ---------------------------------------------------------------------------
# Load / Save
# ---------------------------------------------------------------------------

class TestLoadSaveManifest:
    def test_load_missing_file(self, tmp_path):
        m = load_manifest(str(tmp_path))
        assert m["version"] == MANIFEST_VERSION
        assert m["sessions"] == {}

    def test_load_corrupt_json(self, tmp_path):
        (tmp_path / ".export_manifest.json").write_text("not json{{{")
        m = load_manifest(str(tmp_path))
        assert m["version"] == MANIFEST_VERSION
        assert m["sessions"] == {}

    def test_load_wrong_version(self, tmp_path):
        (tmp_path / ".export_manifest.json").write_text(
            json.dumps({"version": 999, "sessions": {"a": {}}})
        )
        m = load_manifest(str(tmp_path))
        assert m["sessions"] == {}

    def test_save_and_reload(self, tmp_path):
        m = _empty_manifest()
        m["sessions"]["test"] = {"source": {"path": "/tmp/test.jsonl"}}
        save_manifest(str(tmp_path), m)

        loaded = load_manifest(str(tmp_path))
        assert loaded["sessions"]["test"]["source"]["path"] == "/tmp/test.jsonl"

    def test_save_is_atomic(self, tmp_path):
        """Save writes to temp file then renames — no partial writes."""
        m = _empty_manifest()
        m["sessions"]["x"] = {}
        save_manifest(str(tmp_path), m)
        # File should exist and be valid JSON
        manifest_path = tmp_path / ".export_manifest.json"
        assert manifest_path.exists()
        data = json.loads(manifest_path.read_text())
        assert data["version"] == MANIFEST_VERSION


# ---------------------------------------------------------------------------
# Scan Sources
# ---------------------------------------------------------------------------

class TestScanSources:
    def test_updates_manifest_from_files(self, tmp_path):
        f1 = _make_jsonl(tmp_path, "session-a")
        f2 = _make_jsonl(tmp_path, "session-b")
        m = _empty_manifest()

        seen = scan_sources(m, [("claude", f1), ("codex", f2)])
        assert "session-a" in seen
        assert "session-b" in seen
        assert m["sessions"]["session-a"]["source"]["source_tag"] == "claude"
        assert m["sessions"]["session-b"]["source"]["source_tag"] == "codex"
        assert m["sessions"]["session-a"]["source"]["mtime"] > 0
        assert m["sessions"]["session-a"]["source"]["size"] >= 0

    def test_updates_existing_entry(self, tmp_path):
        f = _make_jsonl(tmp_path, "session-x")
        m = _empty_manifest()
        m["sessions"]["session-x"] = {"source": {"mtime": 0, "size": 0}}

        scan_sources(m, [("claude", f)])
        assert m["sessions"]["session-x"]["source"]["mtime"] > 0


# ---------------------------------------------------------------------------
# Scan Vault
# ---------------------------------------------------------------------------

class TestScanVault:
    def test_reads_frontmatter(self, tmp_path):
        _make_vault_md(tmp_path, "abc123", enriched=True, source_mtime=1234.5)
        m = _empty_manifest()

        seen = scan_vault(m, str(tmp_path))
        assert "abc123" in seen
        vault = m["sessions"]["abc123"]["vault"]
        assert vault["enriched"] is True
        assert vault["source_mtime_at_export"] == 1234.5

    def test_marks_missing_files(self, tmp_path):
        m = _empty_manifest()
        m["sessions"]["gone"] = {"vault": {"filename": "gone.md"}}

        scan_vault(m, str(tmp_path))
        assert m["sessions"]["gone"]["vault"]["filename"] is None

    def test_unenriched_detected(self, tmp_path):
        _make_vault_md(tmp_path, "notsummarised", enriched=False)
        m = _empty_manifest()
        scan_vault(m, str(tmp_path))
        assert m["sessions"]["notsummarised"]["vault"]["enriched"] is False


# ---------------------------------------------------------------------------
# Compute Delta
# ---------------------------------------------------------------------------

class TestComputeDelta:
    def test_new_session(self):
        m = _empty_manifest()
        m["sessions"]["new"] = {
            "source": {"path": "/a.jsonl", "mtime": 100, "size": 50, "source_tag": "claude"},
        }
        delta = compute_delta(m)
        assert len(delta["to_export"]) == 1
        assert delta["to_export"][0][1] == Path("/a.jsonl")

    def test_unchanged_enriched_skipped(self):
        m = _empty_manifest()
        m["sessions"]["ok"] = {
            "source": {"path": "/a.jsonl", "mtime": 100, "size": 50, "source_tag": "claude"},
            "vault": {"filename": "ok.md", "source_mtime_at_export": 100, "enriched": True},
        }
        delta = compute_delta(m)
        assert "ok" in delta["skip"]
        assert len(delta["to_export"]) == 0

    def test_changed_session_reexported(self):
        m = _empty_manifest()
        m["sessions"]["changed"] = {
            "source": {"path": "/a.jsonl", "mtime": 200, "size": 100, "source_tag": "claude"},
            "vault": {"filename": "old.md", "source_mtime_at_export": 100, "enriched": True},
        }
        delta = compute_delta(m)
        assert len(delta["to_reexport"]) == 1
        assert delta["to_reexport"][0][2] == "old.md"

    def test_unenriched_flagged(self):
        m = _empty_manifest()
        m["sessions"]["raw"] = {
            "source": {"path": "/a.jsonl", "mtime": 100, "size": 50, "source_tag": "claude"},
            "vault": {"filename": "raw.md", "source_mtime_at_export": 100, "enriched": False},
        }
        delta = compute_delta(m)
        assert "raw.md" in delta["to_enrich"]

    def test_orphan_detected(self):
        m = _empty_manifest()
        m["sessions"]["orphan"] = {
            "vault": {"filename": "orphan.md", "enriched": True},
        }
        delta = compute_delta(m)
        assert len(delta["orphans"]) == 1

    def test_missing_vault_file_reexported(self):
        m = _empty_manifest()
        m["sessions"]["missing"] = {
            "source": {"path": "/a.jsonl", "mtime": 100, "size": 50, "source_tag": "claude"},
            "vault": {"filename": None, "source_mtime_at_export": 100, "enriched": True},
        }
        delta = compute_delta(m)
        assert len(delta["to_export"]) == 1


# ---------------------------------------------------------------------------
# Update functions
# ---------------------------------------------------------------------------

class TestUpdateFunctions:
    def test_update_after_export(self, tmp_path):
        f = _make_jsonl(tmp_path, "exported")
        m = _empty_manifest()
        update_after_export(m, "exported", "claude", f, "test_exported.md")
        entry = m["sessions"]["exported"]
        assert entry["vault"]["filename"] == "test_exported.md"
        assert entry["vault"]["enriched"] is False

    def test_update_after_enrich(self):
        m = _empty_manifest()
        m["sessions"]["enriched"] = {"vault": {}}
        update_after_enrich(m, "enriched", "new_name.md", "generated")
        vault = m["sessions"]["enriched"]["vault"]
        assert vault["filename"] == "new_name.md"
        assert vault["enriched"] is True
        assert vault["title_source"] == "generated"


# ---------------------------------------------------------------------------
# Check Health
# ---------------------------------------------------------------------------

class TestCheckHealth:
    def test_no_issues(self):
        m = _empty_manifest()
        m["sessions"]["ok"] = {
            "source": {"path": "/a.jsonl", "mtime": 100, "size": 50},
            "vault": {"filename": "ok.md", "source_mtime_at_export": 100, "enriched": True},
        }
        health = check_health(m)
        assert len(health["orphans"]) == 0
        assert len(health["unenriched"]) == 0
        assert len(health["stale"]) == 0

    def test_orphan_detected(self):
        m = _empty_manifest()
        m["sessions"]["orphan"] = {
            "vault": {"filename": "orphan.md", "enriched": True},
        }
        health = check_health(m)
        assert len(health["orphans"]) == 1

    def test_unenriched_detected(self):
        m = _empty_manifest()
        m["sessions"]["raw"] = {
            "source": {"path": "/a.jsonl", "mtime": 100},
            "vault": {"filename": "raw.md", "enriched": False},
        }
        health = check_health(m)
        assert len(health["unenriched"]) == 1

    def test_stale_detected(self):
        m = _empty_manifest()
        m["sessions"]["stale"] = {
            "source": {"path": "/a.jsonl", "mtime": 200},
            "vault": {"filename": "stale.md", "source_mtime_at_export": 100, "enriched": True},
        }
        health = check_health(m)
        assert len(health["stale"]) == 1


# ---------------------------------------------------------------------------
# Quick Check
# ---------------------------------------------------------------------------

class TestQuickCheckSources:
    def test_empty_manifest_returns_true(self):
        m = _empty_manifest()
        assert quick_check_sources(m) is True

    def test_unchanged_returns_false(self, tmp_path):
        f = _make_jsonl(tmp_path, "stable")
        stat = f.stat()
        m = _empty_manifest()
        m["sessions"]["stable"] = {
            "source": {"path": str(f), "mtime": stat.st_mtime, "size": stat.st_size},
        }
        assert quick_check_sources(m) is False

    def test_changed_mtime_returns_true(self, tmp_path):
        f = _make_jsonl(tmp_path, "changed")
        m = _empty_manifest()
        m["sessions"]["changed"] = {
            "source": {"path": str(f), "mtime": 0, "size": f.stat().st_size},
        }
        assert quick_check_sources(m) is True

    def test_missing_file_ignored(self, tmp_path):
        m = _empty_manifest()
        m["sessions"]["gone"] = {
            "source": {"path": "/nonexistent/file.jsonl", "mtime": 100, "size": 50},
        }
        assert quick_check_sources(m) is False


# ---------------------------------------------------------------------------
# Interactive caching
# ---------------------------------------------------------------------------

class TestInteractiveCaching:
    def test_unknown_session_returns_false(self):
        m = _empty_manifest()
        assert is_known_noninteractive(m, "unknown", 100, 50) is False

    def test_cached_noninteractive_with_matching_stat(self, tmp_path):
        m = _empty_manifest()
        m["sessions"]["cached"] = {
            "source": {"mtime": 100.0, "size": 50, "is_interactive": False},
        }
        assert is_known_noninteractive(m, "cached", 100.0, 50) is True

    def test_cached_noninteractive_with_changed_stat(self):
        m = _empty_manifest()
        m["sessions"]["cached"] = {
            "source": {"mtime": 100.0, "size": 50, "is_interactive": False},
        }
        assert is_known_noninteractive(m, "cached", 200.0, 50) is False

    def test_cache_stores_status(self):
        m = _empty_manifest()
        cache_interactive_status(m, "new-session", True)
        assert m["sessions"]["new-session"]["source"]["is_interactive"] is True

        cache_interactive_status(m, "non-interactive", False)
        assert m["sessions"]["non-interactive"]["source"]["is_interactive"] is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_parse_float_valid(self):
        assert _parse_float("1234.5") == 1234.5

    def test_parse_float_invalid(self):
        assert _parse_float("not a number") is None

    def test_parse_float_none(self):
        assert _parse_float(None) is None

    def test_empty_manifest_structure(self):
        m = _empty_manifest()
        assert m["version"] == MANIFEST_VERSION
        assert m["sessions"] == {}
