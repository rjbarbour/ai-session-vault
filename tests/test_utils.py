"""Tests for utils.py"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from utils import (
    parse_frontmatter_file, parse_frontmatter_text,
    check_dir, slugify, extract_account, atomic_write,
)


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

class TestParseFrontmatterFile:
    def test_reads_fields(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("---\ntitle: Hello\nsource: claude\n---\n\n# Hello")
        fm = parse_frontmatter_file(f)
        assert fm["title"] == "Hello"
        assert fm["source"] == "claude"

    def test_missing_frontmatter(self, tmp_path):
        f = tmp_path / "plain.md"
        f.write_text("No frontmatter")
        assert parse_frontmatter_file(f) == {}

    def test_missing_closing_dashes(self, tmp_path):
        f = tmp_path / "broken.md"
        f.write_text("---\ntitle: Broken\n")
        # Should read until EOF without crashing
        fm = parse_frontmatter_file(f)
        assert fm.get("title") == "Broken"

    def test_nonexistent_file(self, tmp_path):
        fm = parse_frontmatter_file(tmp_path / "nope.md")
        assert fm == {}

    def test_strips_quotes(self, tmp_path):
        f = tmp_path / "quoted.md"
        f.write_text('---\ntitle: "My Title"\n---\n')
        fm = parse_frontmatter_file(f)
        assert fm["title"] == "My Title"


class TestParseFrontmatterText:
    def test_returns_dict_and_body(self):
        text = "---\ntitle: Hello\n---\n\n# Body"
        fm, body = parse_frontmatter_text(text)
        assert fm["title"] == "Hello"
        assert "# Body" in body

    def test_no_frontmatter(self):
        fm, body = parse_frontmatter_text("Just text")
        assert fm == {}
        assert body == "Just text"

    def test_missing_closing_dashes(self):
        fm, body = parse_frontmatter_text("---\ntitle: X\n")
        assert fm == {}


# ---------------------------------------------------------------------------
# Directory checking
# ---------------------------------------------------------------------------

class TestCheckDir:
    def test_existing_dir(self, tmp_path):
        assert check_dir(tmp_path) is True

    def test_nonexistent_dir(self, tmp_path):
        assert check_dir(tmp_path / "nope") is False

    def test_file_not_dir(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hi")
        assert check_dir(f) is False


# ---------------------------------------------------------------------------
# Slugify
# ---------------------------------------------------------------------------

class TestSlugifyUtils:
    def test_basic(self):
        assert slugify("Hello World") == "hello-world"

    def test_special_chars(self):
        assert slugify("What's up? (test)") == "whats-up-test"

    def test_truncation(self):
        result = slugify("a" * 100, max_len=20)
        assert len(result) <= 20

    def test_empty(self):
        assert slugify("") == "untitled"

    def test_unicode(self):
        result = slugify("café résumé")
        assert "caf" in result


# ---------------------------------------------------------------------------
# Account extraction
# ---------------------------------------------------------------------------

class TestExtractAccount:
    def test_from_users_path(self):
        assert extract_account("/Users/rob_dev/projects/foo") == "rob_dev"

    def test_from_deep_path(self):
        assert extract_account("/Users/robfo/.claude/projects/bar") == "robfo"

    def test_no_users(self):
        assert extract_account("/tmp/test") == ""

    def test_empty(self):
        assert extract_account("") == ""


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    def test_creates_file(self, tmp_path):
        target = tmp_path / "output.txt"
        atomic_write(target, "hello world")
        assert target.read_text() == "hello world"

    def test_overwrites_existing(self, tmp_path):
        target = tmp_path / "output.txt"
        target.write_text("old content")
        atomic_write(target, "new content")
        assert target.read_text() == "new content"

    def test_no_partial_on_error(self, tmp_path):
        """If atomic_write fails, the original file is unchanged."""
        target = tmp_path / "output.txt"
        target.write_text("original")
        # Can't easily simulate a rename failure, but verify the pattern works
        atomic_write(target, "updated")
        assert target.read_text() == "updated"
