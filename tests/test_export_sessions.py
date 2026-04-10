"""Tests for export_sessions_to_obsidian.py and utils.py"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from utils import load_config, slugify
from export_sessions_to_obsidian import (
    extract_text,
    summarise_tool_use,
    process_message,
    export_session,
    detect_format,
    codex_process_line,
    codex_summarise_function_call,
    parse_codex_session,
    extract_custom_title,
    load_codex_titles,
    extract_codex_meta,
    load_desktop_titles,
    load_cowork_sessions,
    is_interactive_session,
    find_session_files,
    archive_vault_file,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_jsonl(tmp_path, lines, name="test-session.jsonl"):
    """Write a list of dicts as a JSONL file, return the path."""
    fpath = tmp_path / name
    with open(fpath, "w") as f:
        for item in lines:
            f.write(json.dumps(item) + "\n")
    return fpath


# ---------------------------------------------------------------------------
# Claude Code: extract_text
# ---------------------------------------------------------------------------

class TestExtractText:
    def test_string_passthrough(self):
        assert extract_text("hello world") == "hello world"

    def test_empty_string(self):
        assert extract_text("") == ""

    def test_list_with_text_block(self):
        content = [{"type": "text", "text": "hello"}]
        assert extract_text(content) == "hello"

    def test_list_with_multiple_text_blocks(self):
        content = [
            {"type": "text", "text": "line one"},
            {"type": "text", "text": "line two"},
        ]
        assert extract_text(content) == "line one\nline two"

    def test_list_with_tool_use_block(self):
        content = [{"type": "tool_use", "name": "Read", "input": {}}]
        result = extract_text(content)
        assert "[Tool call: Read]" in result

    def test_list_with_tool_result_block_skipped(self):
        """tool_result blocks are protocol plumbing and should produce no text."""
        content = [{"type": "tool_result", "content": "big result here"}]
        result = extract_text(content)
        assert result == ""

    def test_list_with_mixed_blocks(self):
        content = [
            {"type": "text", "text": "before"},
            {"type": "tool_use", "name": "Bash", "input": {}},
            {"type": "text", "text": "after"},
        ]
        result = extract_text(content)
        assert "before" in result
        assert "after" in result

    def test_list_with_plain_strings(self):
        content = ["hello", "world"]
        assert extract_text(content) == "hello\nworld"

    def test_thinking_block_excluded(self):
        content = [{"type": "thinking", "thinking": "internal reasoning"}]
        result = extract_text(content)
        assert "internal reasoning" not in result

    def test_non_string_non_list_fallback(self):
        assert extract_text(42) == "42"


# ---------------------------------------------------------------------------
# Claude Code: summarise_tool_use
# ---------------------------------------------------------------------------

class TestSummariseToolUse:
    def test_read(self):
        block = {"name": "Read", "input": {"file_path": "/foo/bar.md"}}
        assert summarise_tool_use(block) == "Read: /foo/bar.md"

    def test_write(self):
        block = {"name": "Write", "input": {"file_path": "/foo/bar.md"}}
        assert summarise_tool_use(block) == "Write: /foo/bar.md"

    def test_bash_short(self):
        block = {"name": "Bash", "input": {"command": "ls -la"}}
        assert summarise_tool_use(block) == "Bash: `ls -la`"

    def test_bash_long_truncated(self):
        long_cmd = "x" * 100
        block = {"name": "Bash", "input": {"command": long_cmd}}
        result = summarise_tool_use(block)
        assert result.endswith("...`")
        assert len(result) < 100

    def test_grep(self):
        block = {"name": "Grep", "input": {"pattern": "foo.*bar"}}
        assert "foo.*bar" in summarise_tool_use(block)

    def test_unknown_tool(self):
        block = {"name": "SomeMCPTool", "input": {"key": "val"}}
        result = summarise_tool_use(block)
        assert "SomeMCPTool" in result

    def test_missing_input(self):
        block = {"name": "Read", "input": {}}
        result = summarise_tool_use(block)
        assert "?" in result


# ---------------------------------------------------------------------------
# Claude Code: process_message
# ---------------------------------------------------------------------------

    def test_edit(self):
        block = {"name": "Edit", "input": {"file_path": "/foo/bar.py"}}
        assert summarise_tool_use(block) == "Edit: /foo/bar.py"

    def test_glob(self):
        block = {"name": "Glob", "input": {"pattern": "**/*.ts"}}
        assert summarise_tool_use(block) == "Glob: **/*.ts"

    def test_agent(self):
        block = {"name": "Agent", "input": {"description": "search for config files"}}
        assert "search for config files" in summarise_tool_use(block)

    def test_todowrite(self):
        block = {"name": "TodoWrite", "input": {}}
        assert summarise_tool_use(block) == "[TodoWrite update]"

    def test_websearch(self):
        block = {"name": "WebSearch", "input": {"query": "python async patterns"}}
        assert "python async patterns" in summarise_tool_use(block)

    def test_webfetch(self):
        block = {"name": "WebFetch", "input": {"url": "https://example.com/api"}}
        result = summarise_tool_use(block)
        assert result.startswith("WebFetch: ")
        assert "/api" in result


class TestProcessMessage:
    def test_user_simple_string(self):
        msg = {"type": "user", "message": {"content": "hello world"}}
        result = process_message(msg)
        assert result == ("user", "hello world")

    def test_user_strips_system_reminders(self):
        msg = {
            "type": "user",
            "message": {"content": "before <system-reminder>noise</system-reminder> after"},
        }
        result = process_message(msg)
        assert result is not None
        assert "noise" not in result[1]
        assert "before" in result[1]
        assert "after" in result[1]

    def test_user_too_short_filtered(self):
        msg = {"type": "user", "message": {"content": "ok"}}
        assert process_message(msg) is None

    def test_user_empty_filtered(self):
        msg = {"type": "user", "message": {"content": ""}}
        assert process_message(msg) is None

    def test_user_tool_result_only_should_be_filtered(self):
        msg = {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "x", "content": "big output..."}
                ]
            },
        }
        result = process_message(msg)
        if result is not None:
            assert result[1] != "[Tool result]"

    def test_assistant_text_block(self):
        msg = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "I will help."}]},
        }
        assert process_message(msg) == ("assistant", "I will help.")

    def test_assistant_tool_use_summarised(self):
        msg = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "/a/b.md"}}
                ]
            },
        }
        result = process_message(msg)
        assert result is not None
        assert "Read: /a/b.md" in result[1]

    def test_assistant_thinking_only_filtered(self):
        msg = {
            "type": "assistant",
            "message": {
                "content": [{"type": "thinking", "thinking": "let me consider..."}]
            },
        }
        assert process_message(msg) is None

    def test_assistant_mixed_text_and_thinking(self):
        msg = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "secret reasoning"},
                    {"type": "text", "text": "Here is my answer."},
                ]
            },
        }
        result = process_message(msg)
        assert result is not None
        assert "secret reasoning" not in result[1]
        assert "Here is my answer." in result[1]

    def test_file_history_snapshot_ignored(self):
        assert process_message({"type": "file-history-snapshot", "snapshot": {}}) is None

    def test_system_message_ignored(self):
        assert process_message({"type": "system", "message": {"content": "system prompt"}}) is None


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

    def test_user_message_as_string(self):
        """User message where message field is a string, not a dict."""
        msg = {"type": "user", "message": "hello from string message"}
        result = process_message(msg)
        assert result == ("user", "hello from string message")

    def test_assistant_message_as_string_content(self):
        """Assistant message where content is a plain string."""
        msg = {"type": "assistant", "message": {"content": "plain string response"}}
        result = process_message(msg)
        assert result == ("assistant", "plain string response")

    def test_assistant_message_field_as_string(self):
        """Assistant message where message field is a string."""
        msg = {"type": "assistant", "message": "string message field"}
        result = process_message(msg)
        assert result == ("assistant", "string message field")

    def test_user_message_non_dict_non_string(self):
        """User message where message field is neither dict nor string."""
        msg = {"type": "user", "message": 42}
        assert process_message(msg) is None

    def test_assistant_message_non_dict_non_string(self):
        msg = {"type": "assistant", "message": 42}
        assert process_message(msg) is None

    def test_assistant_content_list_with_string_block(self):
        """Assistant content list containing plain strings."""
        msg = {"type": "assistant", "message": {"content": ["plain string block"]}}
        result = process_message(msg)
        assert result is not None
        assert "plain string block" in result[1]


class TestDetectFormat:
    def test_claude_format(self, tmp_path):
        f = _make_jsonl(tmp_path, [
            {"type": "user", "message": {"content": "hello"}},
        ])
        assert detect_format(f) == "claude"

    def test_codex_format(self, tmp_path):
        f = _make_jsonl(tmp_path, [
            {"timestamp": "2026-03-31T01:00:00Z", "type": "session_meta", "payload": {"id": "abc"}},
        ])
        assert detect_format(f) == "codex"

    def test_codex_response_item(self, tmp_path):
        f = _make_jsonl(tmp_path, [
            {"timestamp": "2026-03-31T01:00:00Z", "type": "response_item", "payload": {"type": "message"}},
        ])
        assert detect_format(f) == "codex"

    def test_empty_file_defaults_claude(self, tmp_path):
        f = tmp_path / "empty.jsonl"
        f.write_text("")
        assert detect_format(f) == "claude"


# ---------------------------------------------------------------------------
# Codex: codex_process_line
# ---------------------------------------------------------------------------

    def test_empty_lines_skipped(self, tmp_path):
        f = tmp_path / "test.jsonl"
        f.write_text("\n\n" + json.dumps({"type": "user", "message": {}}) + "\n")
        assert detect_format(f) == "claude"

    def test_malformed_json_skipped(self, tmp_path):
        f = tmp_path / "test.jsonl"
        f.write_text("not json\n" + json.dumps({"type": "user", "message": {}}) + "\n")
        assert detect_format(f) == "claude"

    def test_unknown_type_defaults_claude(self, tmp_path):
        f = tmp_path / "test.jsonl"
        f.write_text(json.dumps({"type": "unknown_thing", "data": 123}) + "\n")
        assert detect_format(f) == "claude"


class TestCodexProcessLine:
    def test_user_message(self):
        line = {
            "timestamp": "2026-03-31T01:00:00Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Find my yeet skill"}],
            },
        }
        result = codex_process_line(line)
        assert result == ("user", "Find my yeet skill")

    def test_assistant_message(self):
        line = {
            "timestamp": "2026-03-31T01:00:00Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "I found it here."}],
            },
        }
        result = codex_process_line(line)
        assert result == ("assistant", "I found it here.")

    def test_developer_message_skipped(self):
        line = {
            "timestamp": "2026-03-31T01:00:00Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": "<permissions>sandbox rules</permissions>"}],
            },
        }
        assert codex_process_line(line) is None

    def test_system_like_user_message_skipped(self):
        """User messages that start with XML tags are system plumbing."""
        line = {
            "timestamp": "2026-03-31T01:00:00Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "<environment_context>\n<cwd>/foo</cwd>"}],
            },
        }
        assert codex_process_line(line) is None

    def test_function_call_summarised(self):
        line = {
            "timestamp": "2026-03-31T01:00:00Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": '{"cmd": "ls -la", "workdir": "/tmp"}',
            },
        }
        result = codex_process_line(line)
        assert result is not None
        assert result[0] == "assistant"
        assert "exec" in result[1]
        assert "ls -la" in result[1]

    def test_function_call_output_skipped(self):
        line = {
            "timestamp": "2026-03-31T01:00:00Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "output": "big command output here",
            },
        }
        assert codex_process_line(line) is None

    def test_reasoning_skipped(self):
        line = {
            "timestamp": "2026-03-31T01:00:00Z",
            "type": "response_item",
            "payload": {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "thinking..."}],
            },
        }
        assert codex_process_line(line) is None

    def test_event_msg_skipped(self):
        line = {
            "timestamp": "2026-03-31T01:00:00Z",
            "type": "event_msg",
            "payload": {"role": "", "content": ""},
        }
        assert codex_process_line(line) is None

    def test_session_meta_skipped(self):
        line = {
            "timestamp": "2026-03-31T01:00:00Z",
            "type": "session_meta",
            "payload": {"id": "abc"},
        }
        assert codex_process_line(line) is None

    def test_empty_assistant_message_skipped(self):
        line = {
            "timestamp": "2026-03-31T01:00:00Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": ""}],
            },
        }
        assert codex_process_line(line) is None

    def test_short_user_message_skipped(self):
        line = {
            "timestamp": "2026-03-31T01:00:00Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "ok"}],
            },
        }
        assert codex_process_line(line) is None


# ---------------------------------------------------------------------------
# Codex: codex_summarise_function_call
# ---------------------------------------------------------------------------

class TestCodexSummariseFunctionCall:
    def test_exec_command(self):
        payload = {"name": "exec_command", "arguments": '{"cmd": "pwd", "workdir": "/tmp"}'}
        result = codex_summarise_function_call(payload)
        assert "exec" in result
        assert "pwd" in result

    def test_exec_command_long_truncated(self):
        long_cmd = "x" * 100
        payload = {"name": "exec_command", "arguments": json.dumps({"cmd": long_cmd})}
        result = codex_summarise_function_call(payload)
        assert "..." in result

    def test_search_tool(self):
        payload = {"name": "_search_repositories", "arguments": '{"query": "my project"}'}
        result = codex_summarise_function_call(payload)
        assert "_search_repositories" in result
        assert "my project" in result

    def test_list_tool(self):
        payload = {"name": "_list_repositories", "arguments": '{"page_size": 100}'}
        result = codex_summarise_function_call(payload)
        assert "_list_repositories" in result

    def test_unknown_tool(self):
        payload = {"name": "some_tool", "arguments": '{"key": "val"}'}
        result = codex_summarise_function_call(payload)
        assert "some_tool" in result

    def test_malformed_arguments(self):
        payload = {"name": "exec_command", "arguments": "not json"}
        result = codex_summarise_function_call(payload)
        assert "exec" in result


# ---------------------------------------------------------------------------
# Codex: full session export
# ---------------------------------------------------------------------------

class TestCodexExportSession:
    def test_basic_codex_export(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        jsonl = _make_jsonl(tmp_path, [
            {"timestamp": "2026-03-31T01:00:00Z", "type": "session_meta", "payload": {"id": "abc123"}},
            {"timestamp": "2026-03-31T01:00:01Z", "type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Find my yeet skill"}],
            }},
            {"timestamp": "2026-03-31T01:00:02Z", "type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "I found it at ~/.codex/skills/yeet/"}],
            }},
        ])
        result = export_session(jsonl, vault, source_tag="codex")
        assert result is not None
        text = result.read_text()
        assert "source: codex" in text
        assert "tags: [codex-session]" in text
        assert "Find my yeet skill" in text
        assert "I found it at" in text

    def test_codex_filters_developer_and_system(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        jsonl = _make_jsonl(tmp_path, [
            {"timestamp": "t", "type": "session_meta", "payload": {"id": "x"}},
            {"timestamp": "t", "type": "response_item", "payload": {
                "type": "message", "role": "developer",
                "content": [{"type": "input_text", "text": "<permissions>sandboxing</permissions>"}],
            }},
            {"timestamp": "t", "type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "<environment_context><cwd>/foo</cwd>"}],
            }},
            {"timestamp": "t", "type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Real user question here"}],
            }},
            {"timestamp": "t", "type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "Real answer here"}],
            }},
        ])
        result = export_session(jsonl, vault, source_tag="codex")
        text = result.read_text()
        assert "permissions" not in text.lower() or "permissions" not in text.split("---")[2]
        assert "environment_context" not in text
        assert "Real user question here" in text
        assert "Real answer here" in text
        user_turns = [l for l in text.split("\n") if l.startswith("## User")]
        assert len(user_turns) == 1

    def test_codex_function_calls_in_output(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        jsonl = _make_jsonl(tmp_path, [
            {"timestamp": "t", "type": "session_meta", "payload": {"id": "x"}},
            {"timestamp": "t", "type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "List files please"}],
            }},
            {"timestamp": "t", "type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "Let me check."}],
            }},
            {"timestamp": "t", "type": "response_item", "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": '{"cmd": "ls -la /tmp"}',
            }},
            {"timestamp": "t", "type": "response_item", "payload": {
                "type": "function_call_output",
                "output": "total 0\ndrwxr-xr-x 2 user staff 64 Mar 31 file.txt",
            }},
            {"timestamp": "t", "type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "Found file.txt in /tmp."}],
            }},
        ])
        result = export_session(jsonl, vault, source_tag="codex")
        text = result.read_text()
        assert "ls -la /tmp" in text
        assert "total 0" not in text  # function_call_output should be filtered
        assert "Found file.txt" in text

    def test_codex_reasoning_not_leaked(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        jsonl = _make_jsonl(tmp_path, [
            {"timestamp": "t", "type": "session_meta", "payload": {"id": "x"}},
            {"timestamp": "t", "type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Help me find something"}],
            }},
            {"timestamp": "t", "type": "response_item", "payload": {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "SECRET CHAIN OF THOUGHT"}],
                "content": [{"type": "reasoning_content", "text": "internal reasoning"}],
            }},
            {"timestamp": "t", "type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "Here is what I found."}],
            }},
        ])
        result = export_session(jsonl, vault, source_tag="codex")
        text = result.read_text()
        assert "SECRET CHAIN OF THOUGHT" not in text
        assert "internal reasoning" not in text
        assert "Here is what I found." in text

    def test_codex_filename_includes_source_tag(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        jsonl = _make_jsonl(tmp_path, [
            {"timestamp": "t", "type": "session_meta", "payload": {"id": "x"}},
            {"timestamp": "t", "type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "test message here"}],
            }},
            {"timestamp": "t", "type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "reply"}],
            }},
        ], name="rollout-2026-03-31T01-46-32-abc123.jsonl")
        result = export_session(jsonl, vault, source_tag="codex")
        assert "_codex_" in result.name


# ---------------------------------------------------------------------------
# Claude Code: export_session (integration)
# ---------------------------------------------------------------------------

class TestClaudeExportSession:
    def test_basic_export(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        jsonl = _make_jsonl(tmp_path, [
            {"type": "user", "message": {"content": "What is the CRS?"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "The CRS is the booking system."}]}},
        ])
        result = export_session(jsonl, vault)
        assert result is not None
        text = result.read_text()
        assert "session_id: test-session" in text
        assert "user_messages: 1" in text
        assert "assistant_messages: 1" in text
        assert "What is the CRS?" in text
        assert "The CRS is the booking system." in text

    def test_empty_session_returns_none(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        jsonl = _make_jsonl(tmp_path, [
            {"type": "file-history-snapshot", "snapshot": {}},
            {"type": "system", "message": {"content": "system prompt"}},
        ])
        assert export_session(jsonl, vault) is None

    def test_frontmatter_is_valid_yaml(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        jsonl = _make_jsonl(tmp_path, [
            {"type": "user", "message": {"content": "hello there assistant"}},
            {"type": "assistant", "message": {"content": "hi"}},
        ])
        result = export_session(jsonl, vault)
        text = result.read_text()
        assert text.startswith("---\n")
        end_idx = text.index("---", 4)
        frontmatter = text[4:end_idx].strip()
        for line in frontmatter.split("\n"):
            assert ": " in line, f"Invalid frontmatter line: {line}"

    def test_tool_result_user_turns_not_exported(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        jsonl = _make_jsonl(tmp_path, [
            {"type": "user", "message": {"content": "Do something"}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": "/test.md"}}
            ]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "abc", "content": "file contents here"}
            ]}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "I read the file."}
            ]}},
        ])
        result = export_session(jsonl, vault)
        text = result.read_text()
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if line.strip() == "[Tool result]":
                for j in range(i - 1, max(i - 4, 0), -1):
                    if lines[j].startswith("## User"):
                        pytest.fail(f"Found '[Tool result]' as a User turn at line {i+1}")

    def test_thinking_not_leaked(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        jsonl = _make_jsonl(tmp_path, [
            {"type": "user", "message": {"content": "Help me with this"}},
            {"type": "assistant", "message": {"content": [
                {"type": "thinking", "thinking": "SECRET INTERNAL REASONING"},
                {"type": "text", "text": "Here is my visible answer."},
            ]}},
        ])
        result = export_session(jsonl, vault)
        text = result.read_text()
        assert "SECRET INTERNAL REASONING" not in text
        assert "Here is my visible answer." in text

    def test_long_message_truncated(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        jsonl = _make_jsonl(tmp_path, [
            {"type": "user", "message": {"content": "x" * 10000}},
            {"type": "assistant", "message": {"content": "short reply"}},
        ])
        result = export_session(jsonl, vault)
        text = result.read_text()
        assert "[Message truncated" in text
        assert len(text) < 5000

    def test_turn_numbers_skip_tool_results(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        jsonl = _make_jsonl(tmp_path, [
            {"type": "user", "message": {"content": "First question from user"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Let me check."}]}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": "/x.md"}}
            ]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "result"}
            ]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Here is what I found."}]}},
            {"type": "user", "message": {"content": "Thanks, now do something else"}},
        ])
        result = export_session(jsonl, vault)
        text = result.read_text()
        user_turns = [l for l in text.split("\n") if l.startswith("## User")]
        assert len(user_turns) == 2, f"Expected 2 user turns, got {len(user_turns)}: {user_turns}"


# ---------------------------------------------------------------------------
# Obsidian compatibility
# ---------------------------------------------------------------------------

class TestObsidianCompat:
    def test_claude_filename_format(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        jsonl_path = tmp_path / "abcdef12-3456-7890-abcd-ef1234567890.jsonl"
        with open(jsonl_path, "w") as f:
            f.write(json.dumps({"type": "user", "message": {"content": "test message here"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": "reply"}}) + "\n")
        result = export_session(jsonl_path, vault)
        assert "_claude-cli_test-message-here_abcdef12.md" in result.name
        assert result.suffix == ".md"

    def test_no_bare_yaml_special_chars(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        jsonl_path = tmp_path / "test-session.jsonl"
        with open(jsonl_path, "w") as f:
            f.write(json.dumps({
                "type": "user",
                "message": {"content": "What about: colons and 'quotes' in messages?"}
            }) + "\n")
            f.write(json.dumps({
                "type": "assistant",
                "message": {"content": "Fine."}
            }) + "\n")
        result = export_session(jsonl_path, vault)
        text = result.read_text()
        fm_end = text.index("---", 4)
        frontmatter_lines = text[4:fm_end].strip().split("\n")
        for line in frontmatter_lines:
            key, _, val = line.partition(": ")
            assert key.strip(), f"Empty key in frontmatter: {line}"


# ---------------------------------------------------------------------------
# Slugify
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Custom title extraction
# ---------------------------------------------------------------------------

class TestExtractCustomTitle:
    def test_finds_custom_title(self, tmp_path):
        f = _make_jsonl(tmp_path, [
            {"type": "permission-mode", "permissionMode": "default"},
            {"type": "user", "message": {"content": "hello world"}},
            {"type": "custom-title", "customTitle": "my-session-name", "sessionId": "abc"},
        ])
        assert extract_custom_title(f) == "my-session-name"

    def test_no_custom_title(self, tmp_path):
        f = _make_jsonl(tmp_path, [
            {"type": "user", "message": {"content": "hello world"}},
            {"type": "assistant", "message": {"content": "hi"}},
        ])
        assert extract_custom_title(f) == ""


# ---------------------------------------------------------------------------
# Codex title loading
# ---------------------------------------------------------------------------

class TestLoadCodexTitles:
    def test_loads_titles(self, tmp_path):
        index = tmp_path / "session_index.jsonl"
        index.write_text(
            json.dumps({"id": "abc-123", "thread_name": "Build a widget", "updated_at": "2026-01-01"}) + "\n"
            + json.dumps({"id": "def-456", "thread_name": "Fix the bug", "updated_at": "2026-01-02"}) + "\n"
        )
        # load_codex_titles looks for session_index.jsonl in parent of the sessions path
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        titles = load_codex_titles(str(sessions_dir))
        assert titles["abc-123"] == "Build a widget"
        assert titles["def-456"] == "Fix the bug"

    def test_missing_file(self, tmp_path):
        titles = load_codex_titles(str(tmp_path / "nonexistent"))
        assert titles == {}


# ---------------------------------------------------------------------------
# Codex meta extraction
# ---------------------------------------------------------------------------

class TestExtractCodexMeta:
    def test_extracts_id_and_cwd(self, tmp_path):
        f = _make_jsonl(tmp_path, [
            {"timestamp": "t", "type": "session_meta", "payload": {
                "id": "019cbf55-1c4a", "cwd": "/Users/rob_dev/projects/foo"
            }},
            {"timestamp": "t", "type": "response_item", "payload": {"type": "message"}},
        ])
        sid, cwd = extract_codex_meta(f)
        assert sid == "019cbf55-1c4a"
        assert cwd == "/Users/rob_dev/projects/foo"

    def test_no_session_meta(self, tmp_path):
        f = _make_jsonl(tmp_path, [
            {"timestamp": "t", "type": "response_item", "payload": {"type": "message"}},
        ])
        sid, cwd = extract_codex_meta(f)
        assert sid == ""
        assert cwd == ""


# ---------------------------------------------------------------------------
# Interactive session filter
# ---------------------------------------------------------------------------

class TestIsInteractiveSession:
    def test_multi_turn_is_interactive(self, tmp_path):
        f = _make_jsonl(tmp_path, [
            {"type": "user", "message": {"content": "first question"}},
            {"type": "assistant", "message": {"content": "answer"}},
            {"type": "user", "message": {"content": "follow up"}},
            {"type": "assistant", "message": {"content": "more"}},
        ])
        assert is_interactive_session(f) is True

    def test_single_turn_with_enqueue_filtered(self, tmp_path):
        f = _make_jsonl(tmp_path, [
            {"type": "queue-operation", "operation": "enqueue", "content": "prompt"},
            {"type": "queue-operation", "operation": "dequeue"},
            {"type": "user", "message": {"content": "Generate a title for this session"}},
            {"type": "assistant", "message": {"content": "Some Title"}},
        ])
        assert is_interactive_session(f) is False

    def test_single_turn_without_enqueue_kept(self, tmp_path):
        f = _make_jsonl(tmp_path, [
            {"type": "user", "message": {"content": "a real one-turn session"}},
            {"type": "assistant", "message": {"content": "done"}},
        ])
        assert is_interactive_session(f) is True

    def test_multi_turn_with_enqueue_kept(self, tmp_path):
        """Multi-turn sessions are kept even if they have queue-operation."""
        f = _make_jsonl(tmp_path, [
            {"type": "queue-operation", "operation": "enqueue", "content": "prompt"},
            {"type": "user", "message": {"content": "first"}},
            {"type": "assistant", "message": {"content": "reply"}},
            {"type": "user", "message": {"content": "second"}},
            {"type": "assistant", "message": {"content": "reply2"}},
        ])
        assert is_interactive_session(f) is True


# ---------------------------------------------------------------------------
# Export: source tag differentiation
# ---------------------------------------------------------------------------

class TestSourceTagDifferentiation:
    def test_cli_session_gets_claude_cli_tag(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        f = _make_jsonl(tmp_path, [
            {"type": "user", "message": {"content": "hello there friend"},
             "entrypoint": "cli", "cwd": "/Users/rob_dev/projects/test"},
            {"type": "assistant", "message": {"content": "hi back"}},
        ])
        result = export_session(f, vault, source_tag="claude")
        text = result.read_text()
        assert "source: claude-cli" in text

    def test_desktop_session_gets_claude_desktop_tag(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        f = _make_jsonl(tmp_path, [
            {"type": "user", "message": {"content": "hello there friend"},
             "entrypoint": "claude-desktop", "cwd": "/Users/rob_dev/projects/test"},
            {"type": "assistant", "message": {"content": "hi back"}},
        ])
        result = export_session(f, vault, source_tag="claude")
        text = result.read_text()
        assert "source: claude-desktop" in text

    def test_desktop_detected_from_titles_lookup(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        f = _make_jsonl(tmp_path, [
            {"type": "user", "message": {"content": "hello there friend"},
             "entrypoint": "cli", "cwd": "/Users/rob_dev/projects/test"},
            {"type": "assistant", "message": {"content": "hi back"}},
        ], name="abc12345.jsonl")
        desktop_titles = {"abc12345": "My Desktop Session"}
        result = export_session(f, vault, source_tag="claude", desktop_titles=desktop_titles)
        text = result.read_text()
        assert "source: claude-desktop" in text
        assert "My Desktop Session" in text


# ---------------------------------------------------------------------------
# Export: account and project extraction
# ---------------------------------------------------------------------------

class TestAccountAndProject:
    def test_account_extracted_from_cwd(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        f = _make_jsonl(tmp_path, [
            {"type": "user", "message": {"content": "hello there friend"},
             "cwd": "/Users/rob_dev/DocsLocal/myproject"},
            {"type": "assistant", "message": {"content": "hi back"}},
        ])
        result = export_session(f, vault)
        text = result.read_text()
        assert "account: rob_dev" in text
        assert 'project: "/Users/rob_dev/DocsLocal/myproject"' in text

    def test_codex_project_from_session_meta(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        f = _make_jsonl(tmp_path, [
            {"timestamp": "t", "type": "session_meta", "payload": {
                "id": "019c-test", "cwd": "/Users/rob_dev/projects/codex_proj"
            }},
            {"timestamp": "t", "type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "test codex session here"}],
            }},
            {"timestamp": "t", "type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "reply from codex"}],
            }},
        ])
        result = export_session(f, vault, source_tag="codex")
        text = result.read_text()
        assert 'project: "/Users/rob_dev/projects/codex_proj"' in text
        assert "account: rob_dev" in text


# ---------------------------------------------------------------------------
# Export: title chain with Codex titles
# ---------------------------------------------------------------------------

class TestCodexTitleIntegration:
    def test_codex_thread_name_used_as_title(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        f = _make_jsonl(tmp_path, [
            {"timestamp": "t", "type": "session_meta", "payload": {
                "id": "abc-123-test", "cwd": "/tmp"
            }},
            {"timestamp": "t", "type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "do something interesting"}],
            }},
            {"timestamp": "t", "type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "done"}],
            }},
        ])
        codex_titles = {"abc-123-test": "Build Game of Life"}
        result = export_session(f, vault, source_tag="codex", codex_titles=codex_titles)
        text = result.read_text()
        assert "Build Game of Life" in text
        assert "title_source: codex" in text


# ---------------------------------------------------------------------------
# Export: double quotes escaped in YAML
# ---------------------------------------------------------------------------

class TestYamlEscaping:
    def test_double_quotes_in_title_escaped(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        f = _make_jsonl(tmp_path, [
            {"type": "user", "message": {"content": 'Build "The Widget" for production use'},
             "cwd": "/Users/test/project"},
            {"type": "assistant", "message": {"content": "OK"}},
        ])
        result = export_session(f, vault)
        text = result.read_text()
        # Double quotes should be replaced with single quotes in YAML
        assert '"The Widget"' not in text.split("---")[1]
        assert "'The Widget'" in text.split("---")[1]


# ---------------------------------------------------------------------------
# Export: XML tags stripped from first message fallback
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Find session files
# ---------------------------------------------------------------------------

class TestFindSessionFiles:
    def test_finds_claude_in_direct_project_dir(self, tmp_path):
        """When claude_projects points to a dir with JSONL, find them."""
        proj = tmp_path / "project"
        proj.mkdir()
        # Create multi-turn JSONL (passes interactive filter)
        f = proj / "session1.jsonl"
        f.write_text(
            json.dumps({"type": "user", "message": {"content": "first"}}) + "\n"
            + json.dumps({"type": "assistant", "message": {"content": "reply"}}) + "\n"
            + json.dumps({"type": "user", "message": {"content": "second"}}) + "\n"
            + json.dumps({"type": "assistant", "message": {"content": "reply2"}}) + "\n"
        )
        codex = tmp_path / "codex"
        codex.mkdir()
        found = find_session_files([proj], codex)
        assert len(found) == 1
        assert found[0][0] == "claude"

    def test_finds_claude_in_parent_dir(self, tmp_path):
        """When claude_projects points to parent, enumerate subdirs."""
        parent = tmp_path / "projects"
        parent.mkdir()
        proj_a = parent / "project-a"
        proj_a.mkdir()
        f = proj_a / "session1.jsonl"
        f.write_text(
            json.dumps({"type": "user", "message": {"content": "first"}}) + "\n"
            + json.dumps({"type": "assistant", "message": {"content": "reply"}}) + "\n"
            + json.dumps({"type": "user", "message": {"content": "second"}}) + "\n"
        )
        proj_b = parent / "project-b"
        proj_b.mkdir()
        # Single-turn without enqueue — should be kept
        g = proj_b / "session2.jsonl"
        g.write_text(
            json.dumps({"type": "user", "message": {"content": "only turn"}}) + "\n"
            + json.dumps({"type": "assistant", "message": {"content": "reply"}}) + "\n"
        )
        codex = tmp_path / "codex"
        codex.mkdir()
        found = find_session_files([parent], codex)
        assert len(found) == 2

    def test_filters_single_turn_enqueue(self, tmp_path):
        """Single-turn sessions with queue-operation are filtered out."""
        proj = tmp_path / "project"
        proj.mkdir()
        f = proj / "enrichment-call.jsonl"
        f.write_text(
            json.dumps({"type": "queue-operation", "operation": "enqueue", "content": "prompt"}) + "\n"
            + json.dumps({"type": "user", "message": {"content": "Generate a title"}}) + "\n"
            + json.dumps({"type": "assistant", "message": {"content": "Title Here"}}) + "\n"
        )
        codex = tmp_path / "codex"
        codex.mkdir()
        found = find_session_files([proj], codex)
        assert len(found) == 0

    def test_finds_codex_sessions(self, tmp_path):
        proj = tmp_path / "claude"
        proj.mkdir()
        codex = tmp_path / "codex"
        codex.mkdir()
        f = codex / "rollout-2026-03-05T18-34-06-abc123.jsonl"
        f.write_text(
            json.dumps({"timestamp": "t", "type": "session_meta", "payload": {"id": "abc"}}) + "\n"
        )
        found = find_session_files([proj], codex)
        assert len(found) == 1
        assert found[0][0] == "codex"

    def test_skips_nonexistent_dirs(self, tmp_path):
        codex = tmp_path / "codex"
        codex.mkdir()
        found = find_session_files([tmp_path / "nonexistent"], codex)
        assert len(found) == 0


# ---------------------------------------------------------------------------
# Desktop title loading
# ---------------------------------------------------------------------------

class TestLoadDesktopTitles:
    def test_loads_titles_from_dir(self, tmp_path):
        desktop = tmp_path / "desktop"
        desktop.mkdir()
        (desktop / "local_abc.json").write_text(json.dumps({
            "sessionId": "local_abc",
            "cliSessionId": "cli-123",
            "title": "My Desktop Session",
            "cwd": "/tmp",
        }))
        (desktop / "local_def.json").write_text(json.dumps({
            "sessionId": "local_def",
            "cliSessionId": "cli-456",
            "title": "Another Session",
            "cwd": "/tmp",
        }))
        titles = load_desktop_titles(str(desktop))
        assert titles["cli-123"] == "My Desktop Session"
        assert titles["cli-456"] == "Another Session"

    def test_missing_dir(self, tmp_path):
        titles = load_desktop_titles(str(tmp_path / "nonexistent"))
        assert titles == {}

    def test_skips_bak_files(self, tmp_path):
        desktop = tmp_path / "desktop"
        desktop.mkdir()
        # .json.bak won't match *.json glob, but test the .bak check anyway
        # by creating a file whose name contains .bak but ends in .json
        (desktop / "local_abc.json.bak.json").write_text(json.dumps({
            "cliSessionId": "cli-bak", "title": "Backup",
        }))
        titles = load_desktop_titles(str(desktop))
        assert "cli-bak" not in titles

    def test_skips_malformed_json(self, tmp_path):
        desktop = tmp_path / "desktop"
        desktop.mkdir()
        (desktop / "local_bad.json").write_text("not json{{{")
        titles = load_desktop_titles(str(desktop))
        assert titles == {}


# ---------------------------------------------------------------------------
# Export: custom title branch
# ---------------------------------------------------------------------------

    def test_empty_content_list(self):
        line = {
            "timestamp": "t", "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": []},
        }
        assert codex_process_line(line) is None

    def test_non_dict_first_block(self):
        line = {
            "timestamp": "t", "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": ["just a string"]},
        }
        assert codex_process_line(line) is None

    def test_unknown_message_role(self):
        line = {
            "timestamp": "t", "type": "response_item",
            "payload": {"type": "message", "role": "system", "content": [{"type": "text", "text": "sys"}]},
        }
        assert codex_process_line(line) is None


class TestExportCustomTitle:
    def test_custom_title_used_in_export(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        f = _make_jsonl(tmp_path, [
            {"type": "user", "message": {"content": "hello there friend"},
             "cwd": "/Users/test/project"},
            {"type": "assistant", "message": {"content": "hi back"}},
            {"type": "custom-title", "customTitle": "my-custom-session"},
        ])
        result = export_session(f, vault)
        text = result.read_text()
        assert "my-custom-session" in text
        assert "title_source: custom" in text
        assert "my-custom-session" in result.name


# ---------------------------------------------------------------------------
# Export: assistant message truncation
# ---------------------------------------------------------------------------

class TestAssistantTruncation:
    def test_long_assistant_message_truncated(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        f = _make_jsonl(tmp_path, [
            {"type": "user", "message": {"content": "give me a long response please"},
             "cwd": "/Users/test/project"},
            {"type": "assistant", "message": {"content": "x" * 10000}},
        ])
        result = export_session(f, vault)
        text = result.read_text()
        assert "[Response truncated" in text


# ---------------------------------------------------------------------------
# Codex: load_codex_titles default path branch
# ---------------------------------------------------------------------------

class TestLoadCodexTitlesDefault:
    def test_no_path_uses_home(self):
        """When no path given, falls back to ~/.codex/session_index.jsonl."""
        # This just verifies it doesn't crash — the file may or may not exist
        titles = load_codex_titles()
        assert isinstance(titles, dict)


# ---------------------------------------------------------------------------
# Malformed JSONL handling
# ---------------------------------------------------------------------------

class TestMalformedJsonl:
    def test_parse_claude_handles_blank_lines_and_bad_json(self, tmp_path):
        from export_sessions_to_obsidian import parse_claude_session
        f = tmp_path / "messy.jsonl"
        f.write_text(
            "\n"
            "not valid json\n"
            + json.dumps({"type": "user", "message": {"content": "real message here"}}) + "\n"
            "\n"
            + json.dumps({"type": "assistant", "message": {"content": "reply"}}) + "\n"
        )
        messages = parse_claude_session(f)
        assert len(messages) == 2
        assert messages[0] == ("user", "real message here")

    def test_parse_codex_handles_blank_lines_and_bad_json(self, tmp_path):
        f = tmp_path / "messy.jsonl"
        f.write_text(
            "\n"
            "{broken\n"
            + json.dumps({"timestamp": "t", "type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "codex question"}],
            }}) + "\n"
            + json.dumps({"timestamp": "t", "type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "codex answer"}],
            }}) + "\n"
        )
        messages = parse_codex_session(f)
        assert len(messages) == 2

    def test_extract_custom_title_handles_blank_and_bad(self, tmp_path):
        f = tmp_path / "messy.jsonl"
        f.write_text(
            "\n"
            "bad json\n"
            + json.dumps({"type": "user", "message": {"content": "hello"}}) + "\n"
        )
        assert extract_custom_title(f) == ""

    def test_extract_codex_meta_handles_blank_and_bad(self, tmp_path):
        f = tmp_path / "messy.jsonl"
        f.write_text(
            "\n"
            "{corrupt\n"
            + json.dumps({"timestamp": "t", "type": "response_item", "payload": {}}) + "\n"
        )
        sid, cwd = extract_codex_meta(f)
        assert sid == ""

    def test_load_codex_titles_handles_blank_and_bad(self, tmp_path):
        index = tmp_path / "session_index.jsonl"
        index.write_text(
            "\n"
            "not json\n"
            + json.dumps({"id": "abc", "thread_name": "Good Title"}) + "\n"
        )
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        titles = load_codex_titles(str(sessions))
        assert titles["abc"] == "Good Title"

    def test_export_session_handles_no_cwd_in_metadata(self, tmp_path):
        """When no JSONL line has cwd, falls back to parent dir name."""
        vault = tmp_path / "vault"
        vault.mkdir()
        f = _make_jsonl(tmp_path, [
            {"type": "user", "message": {"content": "hello there friend"}},
            {"type": "assistant", "message": {"content": "hi back"}},
        ])
        result = export_session(f, vault)
        text = result.read_text()
        # project falls back to parent directory name
        assert f'project: "{tmp_path.name}"' in text


# ---------------------------------------------------------------------------
# find_session_files: non-dir child in parent
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Co-work session loading
# ---------------------------------------------------------------------------

class TestLoadCoworkSessions:
    def test_loads_titles_and_finds_jsonl(self, tmp_path):
        cowork = tmp_path / "cowork"
        cowork.mkdir()
        # Create metadata JSON
        (cowork / "local_abc123.json").write_text(json.dumps({
            "sessionId": "local_abc123",
            "cliSessionId": "cli-cowork-1",
            "title": "Review thesis draft",
            "cwd": "/sessions/awesome-fervent-hypatia",
            "model": "claude-opus-4-6",
        }))
        # Create paired directory with JSONL
        paired = cowork / "local_abc123" / ".claude" / "projects" / "-sessions-awesome-fervent-hypatia"
        paired.mkdir(parents=True)
        jsonl = paired / "cli-cowork-1.jsonl"
        jsonl.write_text(
            json.dumps({"type": "user", "message": {"content": "review this thesis"}}) + "\n"
            + json.dumps({"type": "assistant", "message": {"content": "reviewing..."}}) + "\n"
            + json.dumps({"type": "user", "message": {"content": "what do you think?"}}) + "\n"
        )
        # Create audit.jsonl (should be skipped)
        (cowork / "local_abc123" / "audit.jsonl").write_text('{"type": "audit"}\n')

        titles, jsonl_files = load_cowork_sessions(str(cowork))
        assert titles["cli-cowork-1"] == "Review thesis draft"
        assert len(jsonl_files) == 1
        assert jsonl_files[0].name == "cli-cowork-1.jsonl"

    def test_missing_dir(self, tmp_path):
        titles, files = load_cowork_sessions(str(tmp_path / "nonexistent"))
        assert titles == {}
        assert files == []

    def test_skips_bak_files(self, tmp_path):
        cowork = tmp_path / "cowork"
        cowork.mkdir()
        (cowork / "local_abc.json.bak.json").write_text(json.dumps({
            "cliSessionId": "bak-id", "title": "Backup",
        }))
        titles, files = load_cowork_sessions(str(cowork))
        assert "bak-id" not in titles


class TestCoworkExport:
    def test_cowork_session_exported_with_correct_source(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        f = _make_jsonl(tmp_path, [
            {"type": "user", "message": {"content": "review my thesis draft"},
             "cwd": "/sessions/awesome-fervent-hypatia"},
            {"type": "assistant", "message": {"content": "I'll review it now"}},
        ])
        cowork_titles = {"test-session": "Review Thesis Draft"}
        result = export_session(f, vault, source_tag="cowork",
                                cowork_titles=cowork_titles)
        text = result.read_text()
        assert "source: claude-cowork" in text
        assert "claude-cowork" in result.name

    def test_cowork_title_used_from_metadata(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        f = _make_jsonl(tmp_path, [
            {"type": "user", "message": {"content": "do something"},
             "cwd": "/sessions/test-session"},
            {"type": "assistant", "message": {"content": "done"}},
        ], name="cli-id-123.jsonl")
        cowork_titles = {"cli-id-123": "My Cowork Session Title"}
        result = export_session(f, vault, source_tag="cowork",
                                cowork_titles=cowork_titles)
        text = result.read_text()
        assert "My Cowork Session Title" in text
        assert "title_source: desktop" in text  # reuses desktop title path


class TestFindSessionFilesWithCowork:
    def test_includes_cowork_files(self, tmp_path):
        proj = tmp_path / "claude"
        proj.mkdir()
        codex = tmp_path / "codex"
        codex.mkdir()
        cowork_file = tmp_path / "cowork-session.jsonl"
        cowork_file.write_text(
            json.dumps({"type": "user", "message": {"content": "first"}}) + "\n"
            + json.dumps({"type": "assistant", "message": {"content": "reply"}}) + "\n"
        )
        found = find_session_files([proj], codex, cowork_jsonl_files=[cowork_file])
        cowork_found = [f for tag, f in found if tag == "cowork"]
        assert len(cowork_found) == 1


class TestManifestCachedDiscovery:
    def test_noninteractive_cached_and_skipped(self, tmp_path):
        """Non-interactive sessions are cached in manifest and skipped on second call."""
        from manifest import _empty_manifest, cache_interactive_status

        proj = tmp_path / "project"
        proj.mkdir()
        # Create a non-interactive file (single turn + enqueue)
        f = proj / "enrichment-call.jsonl"
        f.write_text(
            json.dumps({"type": "queue-operation", "operation": "enqueue", "content": "p"}) + "\n"
            + json.dumps({"type": "user", "message": {"content": "Generate a title"}}) + "\n"
            + json.dumps({"type": "assistant", "message": {"content": "Title"}}) + "\n"
        )
        codex = tmp_path / "codex"
        codex.mkdir()

        # First call: no manifest, should check file and find 0 interactive
        found = find_session_files([proj], codex)
        assert len(found) == 0

        # Second call with manifest: populate cache
        manifest = _empty_manifest()
        found = find_session_files([proj], codex, manifest=manifest)
        assert len(found) == 0
        # The cache should now have is_interactive=False
        entry = manifest["sessions"].get("enrichment-call", {})
        assert entry.get("source", {}).get("is_interactive") is False

    def test_interactive_cached(self, tmp_path):
        """Interactive sessions are cached as True in manifest."""
        from manifest import _empty_manifest

        proj = tmp_path / "project"
        proj.mkdir()
        f = proj / "real-session.jsonl"
        f.write_text(
            json.dumps({"type": "user", "message": {"content": "first"}}) + "\n"
            + json.dumps({"type": "assistant", "message": {"content": "reply"}}) + "\n"
            + json.dumps({"type": "user", "message": {"content": "second"}}) + "\n"
        )
        codex = tmp_path / "codex"
        codex.mkdir()

        manifest = _empty_manifest()
        found = find_session_files([proj], codex, manifest=manifest)
        assert len(found) == 1
        entry = manifest["sessions"].get("real-session", {})
        assert entry.get("source", {}).get("is_interactive") is True

    def test_cached_noninteractive_skips_file_read(self, tmp_path):
        """With a cached non-interactive verdict and unchanged mtime, file is not read."""
        from manifest import _empty_manifest

        proj = tmp_path / "project"
        proj.mkdir()
        f = proj / "cached-skip.jsonl"
        f.write_text(
            json.dumps({"type": "queue-operation", "operation": "enqueue", "content": "p"}) + "\n"
            + json.dumps({"type": "user", "message": {"content": "Gen title"}}) + "\n"
        )
        codex = tmp_path / "codex"
        codex.mkdir()

        # Pre-populate manifest with cached non-interactive status
        stat = f.stat()
        manifest = _empty_manifest()
        manifest["sessions"]["cached-skip"] = {
            "source": {
                "path": str(f),
                "mtime": stat.st_mtime,
                "size": stat.st_size,
                "is_interactive": False,
            }
        }

        found = find_session_files([proj], codex, manifest=manifest)
        assert len(found) == 0


class TestFindSessionFilesEdgeCases:
    def test_non_dir_child_in_parent_skipped(self, tmp_path):
        parent = tmp_path / "projects"
        parent.mkdir()
        # File, not a dir
        (parent / "README.md").write_text("not a project")
        # Actual project dir
        proj = parent / "real-project"
        proj.mkdir()
        f = proj / "session.jsonl"
        f.write_text(
            json.dumps({"type": "user", "message": {"content": "first"}}) + "\n"
            + json.dumps({"type": "assistant", "message": {"content": "reply"}}) + "\n"
            + json.dumps({"type": "user", "message": {"content": "second"}}) + "\n"
        )
        codex = tmp_path / "codex"
        codex.mkdir()
        found = find_session_files([parent], codex)
        assert len(found) == 1


class TestXmlStripping:
    def test_command_tags_stripped_from_title(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        f = _make_jsonl(tmp_path, [
            {"type": "user", "message": {"content": "<command-message>init</command-message>\n<command-name>/init</command-name>"},
             "cwd": "/Users/test/project"},
            {"type": "assistant", "message": {"content": "analyzing..."}},
            {"type": "user", "message": {"content": "do more stuff please"}},
            {"type": "assistant", "message": {"content": "done"}},
        ])
        result = export_session(f, vault)
        text = result.read_text()
        assert "command-message" not in text.split("---")[1]
        assert "init /init" in text.split("---")[1]


class TestArchiveVaultFile:
    def _make_vault_file(self, vault_dir, filename, content="# Test"):
        f = vault_dir / filename
        f.write_text(content)
        return f

    def test_copies_to_archive_subdir(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        self._make_vault_file(vault, "session_abc.md", "# Full history")
        result = archive_vault_file(vault, "session_abc.md")
        assert result is not None
        assert result.parent == vault / "archive"
        assert result.exists()
        assert result.read_text() == "# Full history"

    def test_source_file_preserved(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        self._make_vault_file(vault, "session_abc.md")
        archive_vault_file(vault, "session_abc.md")
        assert (vault / "session_abc.md").exists()

    def test_archive_filename_includes_date(self, tmp_path):
        from datetime import date
        vault = tmp_path / "vault"
        vault.mkdir()
        self._make_vault_file(vault, "session_abc.md")
        result = archive_vault_file(vault, "session_abc.md")
        assert date.today().isoformat() in result.name

    def test_collision_gets_counter(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        self._make_vault_file(vault, "session_abc.md")
        r1 = archive_vault_file(vault, "session_abc.md")
        r2 = archive_vault_file(vault, "session_abc.md")
        assert r1 != r2
        assert r1.exists()
        assert r2.exists()

    def test_missing_file_returns_none(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        assert archive_vault_file(vault, "no_such.md") is None

    def test_archive_dir_created_if_missing(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        self._make_vault_file(vault, "session_abc.md")
        assert not (vault / "archive").exists()
        archive_vault_file(vault, "session_abc.md")
        assert (vault / "archive").is_dir()


class TestCompactionInPipeline:
    """Integration tests for _archive_if_compacted in export_all.py."""

    def _make_vault_md(self, vault_dir, filename):
        """Write a minimal vault file."""
        (vault_dir / filename).write_text("# Session\n\nSome content.\n")

    def test_archive_created_when_file_shrank(self, tmp_path):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from export_all import _archive_if_compacted

        vault = tmp_path / "vault"
        vault.mkdir()
        old_filename = "claude-cli_test-session_abc123.md"
        self._make_vault_md(vault, old_filename)

        entry = {
            "source": {"size": 800},
            "vault": {"source_size_at_export": 5000},
        }
        archived = _archive_if_compacted(vault, old_filename, entry)
        assert archived is not None
        assert archived.parent == vault / "archive"
        assert archived.exists()
        assert (vault / old_filename).exists()

    def test_no_archive_when_file_grew(self, tmp_path):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from export_all import _archive_if_compacted

        vault = tmp_path / "vault"
        vault.mkdir()
        old_filename = "claude-cli_test-session_abc123.md"
        self._make_vault_md(vault, old_filename)

        entry = {
            "source": {"size": 8000},
            "vault": {"source_size_at_export": 5000},
        }
        archived = _archive_if_compacted(vault, old_filename, entry)
        assert archived is None
        assert not (vault / "archive").exists()

    def test_no_archive_when_size_at_export_unknown(self, tmp_path):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from export_all import _archive_if_compacted

        vault = tmp_path / "vault"
        vault.mkdir()
        old_filename = "claude-cli_test-session_abc123.md"
        self._make_vault_md(vault, old_filename)

        entry = {
            "source": {"size": 800},
            "vault": {},
        }
        archived = _archive_if_compacted(vault, old_filename, entry)
        assert archived is None
