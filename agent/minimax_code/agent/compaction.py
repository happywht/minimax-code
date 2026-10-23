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
        # The "[Conversation summary (compacted)]" prefix is a contract:
        # observers and tests match on it to detect compacted payloads.
        result.append({
            "role": "user",
            "content": f"[Conversation summary (compacted)]\n{summary}",
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
    """Summarise old turns into a structured, six-section digest (M3).

    Deterministic extraction — no extra LLM call. The sections mirror
    what a resuming model needs to continue the task faithfully:
    任务目标 / 用户约束 / 关键决策 / 修改清单 / 验证结果 / 待办.
    """

    # --- pass 1: collect raw material across all old turns ------------
    first_user_text = ""
    constraint_sentences: list[str] = []
    assistant_points: list[str] = []
    changed_paths: list[str] = []
    tool_count = 0
    verification_lines: list[str] = []
    last_assistant_text = ""

    for group in turn_groups:
        for msg in group:
            role = msg.get("role", "")
            content = _msg_text(msg)

            if role == "user":
                if not first_user_text:
                    first_user_text = content
                for sentence in _split_sentences(content):
                    if _is_constraint(sentence):
                        constraint_sentences.append(sentence)
            elif role == "assistant":
                if content:
                    last_assistant_text = content
                    if len(assistant_points) < 6:
                        assistant_points.append(content[:150] + ("..." if len(content) > 150 else ""))
                for call in _message_tool_calls(msg):
                    tool_count += 1
                    path = _mutating_path(call)
                    if path and path not in changed_paths:
                        changed_paths.append(path)
            elif role == "tool":
                tool_count += 1
                if len(verification_lines) < 4 and _looks_like_verification(msg, group):
                    verification_lines.append(content[:160] + ("..." if len(content) > 160 else ""))

    # --- pass 2: render the six sections ------------------------------
    parts: list[str] = []

    objective = (first_user_text or "").strip()
    if objective:
        parts.append(f"## Task objective\n{objective[:500]}{'...' if len(objective) > 500 else ''}")

    if constraint_sentences:
        shown = "; ".join(_dedupe(constraint_sentences)[:6])
        parts.append(f"## User constraints\n{shown[:400]}")

    if assistant_points:
        parts.append("## Key assistant responses\n" + "\n".join(f"- {p}" for p in assistant_points[:4]))

    if changed_paths:
        shown = ", ".join(_dedupe(changed_paths)[:12])
        extra = "" if len(changed_paths) <= 12 else f" (+{len(changed_paths) - 12} more)"
        parts.append(f"## Files modified by tools\n{shown}{extra}")

    if verification_lines:
        parts.append("## Command / verification results\n" + "\n".join(f"- {v}" for v in verification_lines))

    pending = (last_assistant_text or "").strip()
    if pending:
        parts.append(f"## Where the work stood\n{pending[-300:]}")
    if tool_count:
        parts.append(f"({tool_count} tool call(s) across the compacted turns.)")

    return "\n\n".join(parts)


_CONSTRAINT_MARKERS = (
    "不要", "别", "必须", "不能", "禁止", "务必", "只能", "记得",
    "do not", "don't", "never", "must", "only", "always", "avoid",
    "make sure", "keep ", "without",
)


def _split_sentences(text: str) -> list[str]:
    """Split on CJK and Latin sentence terminators."""
    import re as _re

    raw = _re.split(r"[。！？；\n.!?;]", text)
    return [s.strip() for s in raw if s.strip()]


def _is_constraint(sentence: str) -> bool:
    lowered = sentence.lower()
    return len(sentence) <= 200 and any(m in lowered for m in _CONSTRAINT_MARKERS)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _message_tool_calls(msg: Message) -> list[dict[str, Any]]:
    """Extract tool calls from an assistant message (nested or flat)."""
    calls = msg.get("tool_calls") or []
    out: list[dict[str, Any]] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        name = str(fn.get("name") or call.get("name") or "")
        raw_args = fn.get("arguments") if isinstance(fn, dict) else None
        args: Any = raw_args
        if isinstance(raw_args, str):
            import json as _json

            try:
                args = _json.loads(raw_args)
            except ValueError:
                args = {}
        out.append({"name": name, "args": args if isinstance(args, dict) else {}})
    return out


_MUTATING_TOOLS = ("write_file", "edit_file", "append_file", "read_file", "exec_command")


def _mutating_path(call: dict[str, Any]) -> str:
    """Return the file a tool call touched, for the change ledger."""
    name = call.get("name", "")
    if name not in ("write_file", "edit_file", "append_file"):
        return ""
    args = call.get("args") or {}
    for key in ("file_path", "path"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _looks_like_verification(msg: Message, group: list[Message]) -> bool:
    """True when a tool message follows an exec-style call in its turn.

    Heuristic: the assistant message right before the tool result asked
    for a command execution (exec_command / Bash family), or the result
    text opens with exit-code markers.
    """
    text = _msg_text(msg)[:200].lower()
    if "exit code" in text or "exit_code" in text:
        return True
    for candidate in group:
        if candidate is msg:
            break
        if candidate.get("role") == "assistant":
            for call in _message_tool_calls(candidate):
                if call.get("name") in ("exec_command", "bash", "run_terminal"):
                    return True
    return False


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
