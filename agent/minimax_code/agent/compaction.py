"""Conversation history compaction.

When a conversation approaches the LLM's context window limit, this
module provides utilities to compress older turns while preserving
recent context. The strategy:

1. Keep all system messages verbatim (never compact).
2. Keep the most recent N turns (``keep_recent``) verbatim.
3. Summarise older user messages into a single compressed block.
4. Summarise tool call/result pairs into one-line descriptions.
5. Return the compacted list.

Token estimation uses a heuristic without a real tokenizer:
~4 characters per token for ASCII, ~2 characters per token for CJK.
This is sufficient for budget enforcement — the LLM reports actual
usage which the frontend displays.
"""

from __future__ import annotations

import re
from typing import Any

# CJK Unicode ranges: CJK Unified Ideographs + Extension A + Compatibility.
_CJK_RE = re.compile(r"[一-鿿㐀-䶿豈-﫿]")

# Type alias for conversation messages (dict with role/content).
Message = dict[str, Any]


def estimate_tokens(text: str) -> int:
    """Rough token count without a tokenizer.

    Uses ~4 chars/token for ASCII and ~2 chars/token for CJK text.
    Good enough for budget enforcement; the LLM reports actual usage.
    """
    if not text:
        return 0
    cjk_count = len(_CJK_RE.findall(text))
    ascii_count = len(text) - cjk_count
    return (ascii_count // 4) + cjk_count + (cjk_count // 2)


def compact_history(
    messages: list[Message],
    *,
    max_tokens: int,
    keep_recent: int = 4,
) -> list[Message]:
    """Compact conversation history to fit within a token budget.

    Strategy
    --------
    - System messages are never compacted.
    - The most recent ``keep_recent`` user/assistant turns are kept verbatim.
    - Older turns are summarised into a single compacted message block.
    - Tool call/result pairs are compressed to one-line summaries.

    Parameters
    ----------
    messages:
        The conversation history (list of dicts with ``role`` and ``content``).
    max_tokens:
        Target maximum token count for the output.
    keep_recent:
        Number of recent turns to preserve verbatim.

    Returns
    -------
    Compacted message list that should be within ``max_tokens``.
    """
    if not messages:
        return []

    # Separate system messages from conversation turns.
    system_msgs: list[Message] = []
    turns: list[Message] = []
    for msg in messages:
        if msg.get("role") == "system":
            system_msgs.append(msg)
        else:
            turns.append(msg)

    if not turns:
        return system_msgs

    # Estimate current total tokens.
    current_tokens = sum(estimate_tokens(_msg_text(m)) for m in turns)
    if current_tokens <= max_tokens:
        return messages  # No compaction needed.

    # Split into "old" (to compact) and "recent" (to keep).
    # A "turn" is a user message + its assistant response + any tool calls.
    turn_boundaries = _find_turn_boundaries(turns)
    recent_start = max(0, len(turn_boundaries) - keep_recent)
    old_turns = turn_boundaries[:recent_start]
    recent_msgs = turns[turn_boundaries[recent_start][0]:] if recent_start < len(turn_boundaries) else []

    if not old_turns:
        # Everything is recent; can't compact further.
        return system_msgs + turns

    # Compact old turns into a summary.
    summary = _summarise_old_turns(
        [turns[i:j] for i, j in old_turns]
    )

    result = system_msgs.copy()
    if summary:
        result.append({
            "role": "user",
            "content": (
                "[Conversation summary (compacted)]\n"
                f"{summary}"
            ),
        })
    result.extend(recent_msgs)

    return result


def _find_turn_boundaries(turns: list[Message]) -> list[tuple[int, int]]:
    """Find start/end indices of conversation turns.

    A turn starts at each user message and includes all following
    assistant/tool messages until the next user message.
    """
    boundaries: list[tuple[int, int]] = []
    start = None
    for i, msg in enumerate(turns):
        if msg.get("role") == "user":
            if start is not None:
                boundaries.append((start, i))
            start = i
    if start is not None:
        boundaries.append((start, len(turns)))
    return boundaries


def _summarise_old_turns(turn_groups: list[list[Message]]) -> str:
    """Summarise a list of turn groups into a compact text."""
    parts: list[str] = []
    for group in turn_groups:
        user_msg = ""
        tool_count = 0
        assistant_parts: list[str] = []

        for msg in group:
            role = msg.get("role", "")
            content = _msg_text(msg)

            if role == "user":
                # Truncate user message to first 200 chars.
                user_msg = content[:200] + ("..." if len(content) > 200 else "")
            elif role == "assistant":
                if content:
                    assistant_parts.append(content[:150] + ("..." if len(content) > 150 else ""))
            elif role == "tool":
                tool_count += 1
            # tool_call messages are counted but not detailed.

        summary_line = f"User: {user_msg}"
        if assistant_parts:
            summary_line += f" → Assistant: {'; '.join(assistant_parts[:2])}"
        if tool_count:
            summary_line += f" [{tool_count} tool call(s)]"
        parts.append(summary_line)

    return "\n".join(parts)


def _msg_text(msg: Message) -> str:
    """Extract text content from a message dict."""
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Multi-part content (e.g. from tool results).
        return " ".join(
            part if isinstance(part, str) else str(part)
            for part in content
        )
    return str(content)


__all__ = ["estimate_tokens", "compact_history"]
