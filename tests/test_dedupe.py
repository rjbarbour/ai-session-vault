"""Tests for dedupe_vault.py"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from dedupe_vault import parse_frontmatter, score_session, find_duplicates


def _make_vault_file(vault, name, session_id, enriched=False, title_source="first_message", size_pad=""):
    """Create a vault markdown file with frontmatter."""
    lines = [
        "---",
        f"session_id: {session_id}",
        f"title_source: {title_source}",
    ]
    if enriched:
        lines.append('summary_short: "A summary of the session"')
        lines.append('keywords: "test, session"')
    lines.extend(["---", "", f"# Test {name}", size_pad])
    (vault / name).write_text("\n".join(lines))


class TestParseFrontmatter:
    def test_reads_fields(self, tmp_path):
        _make_vault_file(tmp_path, "test.md", "abc123", enriched=True)
        fm = parse_frontmatter(tmp_path / "test.md")
        assert fm["session_id"] == "abc123"
        assert fm["summary_short"] == "A summary of the session"

    def test_missing_frontmatter(self, tmp_path):
        (tmp_path / "plain.md").write_text("No frontmatter here")
        fm = parse_frontmatter(tmp_path / "plain.md")
        assert fm == {}

    def test_empty_file(self, tmp_path):
        (tmp_path / "empty.md").write_text("")
        fm = parse_frontmatter(tmp_path / "empty.md")
        assert fm == {}


class TestScoreSession:
    def test_enriched_scores_higher(self):
        enriched = {"summary_short": "yes", "title_source": "generated", "keywords": "a,b"}
        unenriched = {"title_source": "first_message"}
        assert score_session(enriched, 1000) > score_session(unenriched, 1000)

    def test_generated_title_scores_higher_than_first_message(self):
        gen = {"title_source": "generated"}
        first = {"title_source": "first_message"}
        assert score_session(gen, 1000) > score_session(first, 1000)

    def test_larger_file_tiebreaker(self):
        fm = {"title_source": "first_message"}
        assert score_session(fm, 100000) > score_session(fm, 500)


class TestFindDuplicates:
    def test_no_duplicates(self, tmp_path):
        _make_vault_file(tmp_path, "a.md", "session-a")
        _make_vault_file(tmp_path, "b.md", "session-b")
        dupes = find_duplicates(str(tmp_path))
        assert len(dupes) == 0

    def test_finds_duplicates(self, tmp_path):
        _make_vault_file(tmp_path, "old.md", "session-x", enriched=False)
        _make_vault_file(tmp_path, "new.md", "session-x", enriched=True)
        dupes = find_duplicates(str(tmp_path))
        assert len(dupes) == 1
        assert "session-x" in dupes

    def test_keeper_has_higher_score(self, tmp_path):
        _make_vault_file(tmp_path, "unenriched.md", "dup", enriched=False)
        _make_vault_file(tmp_path, "enriched.md", "dup", enriched=True, title_source="generated")
        dupes = find_duplicates(str(tmp_path))
        keeper = dupes["dup"][0]
        loser = dupes["dup"][1]
        assert keeper["score"] > loser["score"]
        assert "enriched" in keeper["path"].name
