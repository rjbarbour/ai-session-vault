"""Tests for export_sessions_to_obsidian.py"""
import json
from pathlib import Path

import pytest

sys_path = Path(__file__).resolve().parent.parent / "scripts"
import sys
sys.path.insert(0, str(sys_path))

from export_sessions_to_obsidian import (
    extract_text,
    summarise_tool_use,
    process_message,
    export_session,
    detect_format,
    codex_process_line,
    codex_summarise_function_call,
    parse_codex_session,
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
