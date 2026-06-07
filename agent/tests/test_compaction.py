"""Unit tests for the conversation history compaction module.

Covers token estimation, compact_history strategy, message text
extraction, and edge cases.
"""

from __future__ import annotations

from minimax_code.agent.compaction import (
    _msg_text,
    compact_history,
    estimate_tokens,
)


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------


class TestEstimateTokens:
    def test_ascii_text(self) -> None:
        """ASCII text: ~4 characters per token."""
        # 40 ASCII characters -> ~10 tokens.
        text = "a" * 40
        tokens = estimate_tokens(text)
        assert tokens == 10

    def test_cjk_text(self) -> None:
        """CJK text: ~2 characters per token (CJK count contributes more)."""
        # 10 CJK characters: cjk_count=10, ascii_count=0
        # tokens = (0 // 4) + 10 + (10 // 2) = 15
        text = "一" * 10
        tokens = estimate_tokens(text)
        assert tokens == 15

    def test_mixed_text(self) -> None:
        """Mixed ASCII and CJK: both contributions counted."""
        # 20 ASCII chars + 4 CJK chars
        # tokens = (20 // 4) + 4 + (4 // 2) = 5 + 4 + 2 = 11
        text = "a" * 20 + "中" * 4
        tokens = estimate_tokens(text)
        assert tokens == 11

    def test_empty_string(self) -> None:
        """Empty string returns 0 tokens."""
        assert estimate_tokens("") == 0

    def test_single_char(self) -> None:
        """Single ASCII character rounds to 0 tokens."""
        assert estimate_tokens("a") == 0

    def test_four_chars_one_token(self) -> None:
        """Exactly 4 ASCII characters = 1 token."""
        assert estimate_tokens("abcd") == 1


# ---------------------------------------------------------------------------
# _msg_text
# ---------------------------------------------------------------------------


class TestMsgText:
    def test_string_content(self) -> None:
        """Plain string content is returned as-is."""
        msg = {"role": "user", "content": "Hello"}
        assert _msg_text(msg) == "Hello"

    def test_list_content(self) -> None:
        """List content is joined with spaces."""
        msg = {"role": "user", "content": ["Hello", "world"]}
        assert _msg_text(msg) == "Hello world"

    def test_list_with_dicts(self) -> None:
        """List content with dicts is stringified and joined."""
        msg = {"role": "user", "content": ["Hello", {"text": "world"}]}
        result = _msg_text(msg)
        assert "Hello" in result
        assert "world" in result

    def test_missing_content(self) -> None:
        """Missing content key returns empty string."""
        msg = {"role": "user"}
        assert _msg_text(msg) == ""

    def test_none_content(self) -> None:
        """None content returns the string 'None'."""
        msg = {"role": "user", "content": None}
        assert _msg_text(msg) == "None"


# ---------------------------------------------------------------------------
# compact_history
# ---------------------------------------------------------------------------


class TestCompactHistory:
    def _make_msg(self, role: str, text: str) -> dict:
        return {"role": role, "content": text}

    def test_empty_messages(self) -> None:
        """Empty message list returns empty list."""
        assert compact_history([], max_tokens=100) == []

    def test_system_messages_preserved(self) -> None:
        """System messages are never compacted."""
        system = self._make_msg("system", "You are helpful.")
        user = self._make_msg("user", "Hi")
        assistant = self._make_msg("assistant", "Hello!")
        result = compact_history([system, user, assistant], max_tokens=1000)
        assert result[0] == system

    def test_keeps_recent_turns(self) -> None:
        """Recent turns are preserved verbatim when budget is exceeded."""
        messages = [self._make_msg("system", "Sys")]
        for i in range(20):
            messages.append(self._make_msg("user", f"User message {i} " * 50))
            messages.append(self._make_msg("assistant", f"Reply {i} " * 50))

        result = compact_history(messages, max_tokens=50, keep_recent=2)
        # The last 2 user turns should be preserved.
        recent_user_texts = [
            m["content"] for m in result if m["role"] == "user"
        ]
        # At least the last 2 user messages should appear verbatim.
        assert any("User message 19" in t for t in recent_user_texts)
        assert any("User message 18" in t for t in recent_user_texts)

    def test_summarizes_old_turns(self) -> None:
        """Old turns are replaced by a summary block."""
        messages = []
        for i in range(10):
            messages.append(self._make_msg("user", f"Question {i} " * 100))
            messages.append(self._make_msg("assistant", f"Answer {i} " * 100))

        result = compact_history(messages, max_tokens=100, keep_recent=2)
        # There should be a compaction summary message.
        summary_msgs = [
            m for m in result
            if m["role"] == "user" and "compacted" in str(m.get("content", "")).lower()
            or m["role"] == "user" and "Conversation summary" in str(m.get("content", ""))
        ]
        assert len(summary_msgs) >= 1

    def test_no_compaction_when_under_budget(self) -> None:
        """Messages under budget are returned unchanged."""
        messages = [
            self._make_msg("user", "Hi"),
            self._make_msg("assistant", "Hello!"),
        ]
        result = compact_history(messages, max_tokens=1000)
        assert result == messages

    def test_handles_various_content_formats(self) -> None:
        """Messages with list-type content are handled."""
        messages = [
            self._make_msg("user", "Hello"),
            {"role": "assistant", "content": ["Part 1", "Part 2"]},
            self._make_msg("user", "Next question " * 200),
            self._make_msg("assistant", "Answer " * 200),
        ]
        # Use a tight budget to force compaction.
        result = compact_history(messages, max_tokens=20, keep_recent=1)
        # Should not raise; output should be a list of dicts.
        assert isinstance(result, list)
        assert all(isinstance(m, dict) for m in result)

    def test_very_few_messages(self) -> None:
        """With very few messages and tight budget, returns gracefully."""
        messages = [
            self._make_msg("user", "Hi"),
            self._make_msg("assistant", "Hello!"),
        ]
        result = compact_history(messages, max_tokens=1, keep_recent=4)
        # Can't compact further (everything is "recent").
        assert isinstance(result, list)

    def test_only_system_messages(self) -> None:
        """System-only messages return unchanged."""
        messages = [
            self._make_msg("system", "You are helpful."),
        ]
        result = compact_history(messages, max_tokens=100)
        assert result == messages

    def test_tool_messages_counted_in_turns(self) -> None:
        """Tool messages within a turn are counted and summarized."""
        messages = [
            self._make_msg("user", "Read file " * 100),
            self._make_msg("assistant", "Calling tool " * 100),
            {"role": "tool", "content": "file contents " * 100},
            self._make_msg("user", "Recent question"),
            self._make_msg("assistant", "Recent answer"),
        ]
        result = compact_history(messages, max_tokens=30, keep_recent=1)
        # Recent turn should be preserved.
        recent_roles = [m["role"] for m in result[-2:]]
        assert "user" in recent_roles
        assert "assistant" in recent_roles
