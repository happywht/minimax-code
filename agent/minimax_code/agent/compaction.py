"""Conversation history compaction.

When a conversation approaches the LLM's context window limit, this
module provides utilities to compress older turns while preserving
recent context. The strategy:

1. Keep all system messages verbatim (never compact).
2. Keep the most recent N turns (``keep_recent``) verbatim when possible.
3. Summarise older user messages into a single compressed block.
4. Summarise tool call/result pairs into one-line descriptions.
5. Return the compacted list.

Token estimation uses a heuristic without a real tokenizer:
~4 characters per token for ASCII, ~2 characters per token for CJK.
This is an estimate, not a tokenizer or a guarantee that protected recent
messages fit the model window. Paid-call budgets are enforced separately.
"""

from __future__ import annotations

import json
import re
from typing import Any

# CJK Unicode ranges: CJK Unified Ideographs + Extension A + Compatibility.
_CJK_RE = re.compile(r"[一-鿿㐀-䶿豈-﫿]")

# Type alias for conversation messages (dict with role/content).
Message = dict[str, Any]


class _CompactedMessage(dict):
    """Wire-compatible message with in-process state for later compactions."""

    def __init__(self, content: str, state: dict[str, Any]):
        super().__init__(role="user", content=content)
        self.state = state


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
    - Recent turns are kept verbatim unless a long tool turn must be split.
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
    A best-effort bounded digest. Protected system/recent messages can
    themselves exceed ``max_tokens`` and are never truncated here.
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
    current_tokens = sum(_message_tokens(m) for m in messages)
    if current_tokens <= max_tokens:
        return messages  # No compaction needed.

    # Split into "old" (to compact) and "recent" (to keep).
    # A "turn" is a user message + its assistant response + any tool calls.
    turn_boundaries = _find_turn_boundaries(turns)
    recent_start = max(0, len(turn_boundaries) - keep_recent)
    old_turns = turn_boundaries[:recent_start]
    recent_msgs = turns[turn_boundaries[recent_start][0]:] if recent_start < len(turn_boundaries) else []

    # A single long Native turn can contain many tool rounds. If the protected
    # recent turns alone exceed the target, compact its older completed rounds
    # too, keeping the latest user message and four complete rounds verbatim.
    if sum(_message_tokens(m) for m in system_msgs + recent_msgs) >= max_tokens:
        split = _long_turn_split(turns, turn_boundaries)
        if split is not None:
            user_index, split_index = split
            old_turns = [*turn_boundaries[:-1], (user_index, split_index)]
            recent_msgs = [turns[user_index], *turns[split_index:]]

    if not old_turns:
        # Everything is recent or no safe completed-tool boundary exists.
        return system_msgs + turns

    # Compact old turns into a summary.
    available = max(0, max_tokens - sum(_message_tokens(m) for m in system_msgs + recent_msgs)
                    - estimate_tokens("[Conversation summary (compacted)]\n"))
    summary, state = _summarise_old_turns([turns[i:j] for i, j in old_turns], available)

    result = system_msgs.copy()
    if summary:
        # The "[Conversation summary (compacted)]" prefix is a contract:
        # observers and tests match on it to detect compacted payloads.
        result.append(_CompactedMessage(f"[Conversation summary (compacted)]\n{summary}", state))
    result.extend(recent_msgs)

    return result


def _find_turn_boundaries(turns: list[Message]) -> list[tuple[int, int]]:
    """Find start/end indices of conversation turns.

    A turn starts at each user message and includes all following
    assistant/tool messages until the next user message.
    """
    boundaries: list[tuple[int, int]] = []
    start = 0 if turns and turns[0].get("role") != "user" else None
    for i, msg in enumerate(turns):
        if msg.get("role") == "user":
            if start is not None:
                boundaries.append((start, i))
            start = i
    if start is not None:
        boundaries.append((start, len(turns)))
    return boundaries


def _long_turn_split(
    turns: list[Message], boundaries: list[tuple[int, int]],
) -> tuple[int, int] | None:
    """Find a boundary before the last four complete tool rounds of the last turn.

    Refuse malformed/incomplete call sequences rather than orphan a tool result.
    """
    if not boundaries:
        return None
    start, end = boundaries[-1]
    if turns[start].get("role") != "user":
        return None
    pending: set[str] = set()
    round_starts: list[int] = []
    for i in range(start + 1, end):
        msg = turns[i]
        if msg.get("role") == "assistant":
            if pending:
                return None
            calls = _message_tool_calls(msg)
            if msg.get("tool_calls") and len(calls) != len(msg["tool_calls"]):
                return None
            if calls:
                ids = [call.get("id") for call in calls]
                if any(not isinstance(call_id, str) or not call_id for call_id in ids):
                    return None
                if len(set(ids)) != len(ids):
                    return None
                pending.update(ids)
                round_starts.append(i)
        elif msg.get("role") == "tool":
            call_id = msg.get("tool_call_id")
            if call_id not in pending:
                return None
            pending.remove(call_id)
        else:
            return None
    if pending or len(round_starts) <= 4:
        return None
    return start, round_starts[-4]


def _summarise_old_turns(turn_groups: list[list[Message]], available: int) -> tuple[str, dict[str, Any]]:
    """Extract bounded, explicitly incomplete context without a model call."""

    # --- pass 1: collect raw material across all old turns ------------
    first_user_text = ""
    later_user_messages: list[str] = []
    later_messages_omitted = False
    constraint_sentences: list[str] = []
    decision_sentences: list[str] = []
    pending_items: list[str] = []
    write_results: list[str] = []
    changed_paths: list[str] = []
    verification_lines: list[str] = []
    captured_output_refs: list[str] = []

    for group in turn_groups:
        for msg in group:
            if isinstance(msg, _CompactedMessage):
                state = msg.state
                first_user_text = first_user_text or state.get("objective", "")
                later_user_messages.extend(state.get("later_user_messages", []))
                later_messages_omitted |= state.get("later_messages_omitted", False)
                constraint_sentences.extend(state.get("constraints", []))
                decision_sentences.extend(state.get("decisions", []))
                pending_items.extend(state.get("pending_items", []))
                write_results.extend(state.get("write_results", []))
                verification_lines.extend(state.get("commands", []))
                changed_paths.extend(state.get("paths", []))
                captured_output_refs.extend(state.get("captured_output_refs", []))
                continue
            role = msg.get("role", "")
            content = _msg_text(msg)

            if role == "user":
                if not first_user_text:
                    first_user_text = content
                elif content.strip():
                    later_user_messages.append(_clip(content.strip(), 300))
                for sentence in _split_sentences(content):
                    if _is_constraint(sentence):
                        constraint_sentences.append(sentence)
            elif role == "assistant":
                for sentence in _split_sentences(content):
                    if _is_decision(sentence):
                        decision_sentences.append(_clip(sentence, 200))
                    if _is_pending(sentence):
                        pending_items.append(_clip(sentence, 200))
                for call in _message_tool_calls(msg):
                    path = _mutating_path(call)
                    if path and path not in changed_paths:
                        changed_paths.append(path)
            elif role == "tool":
                reference = _captured_output_ref(content)
                if reference:
                    captured_output_refs.append(reference)
                write_call = _matching_call(msg, group, ("write_file", "edit_file", "append_file"))
                if write_call:
                    path = _mutating_path(write_call)
                    if path:
                        write_results.append(f"{path}: {_write_result(content)}")
                call = _matching_command(msg, group)
                if call:
                    command = _clip(str(call.get("args", {}).get("command", "")), 64)
                    excerpt = _command_excerpt(content)
                    verification_lines.append(f"{call['name']} result: {excerpt}; command: {command}")

    objective = (first_user_text or "").strip()
    constraints = _dedupe(constraint_sentences)
    if len(constraints) > 6:
        constraints = constraints[:3] + ["[other constraints omitted]"] + constraints[-3:]
    commands = verification_lines[-4:]
    decisions = _dedupe(decision_sentences)[-4:]
    pending_items = _dedupe(pending_items)[-4:]
    write_results = _dedupe(write_results)[-12:]
    changed_paths = _dedupe(changed_paths)[-12:]
    captured_output_refs = _dedupe(captured_output_refs)[-4:]
    later_messages_omitted |= len(later_user_messages) > 4
    later_user_messages = later_user_messages[-4:]
    state = {"objective": _clip(objective, 500), "constraints": constraints, "decisions": decisions,
             "commands": commands, "pending_items": pending_items, "write_results": write_results,
             "paths": changed_paths, "later_user_messages": later_user_messages,
             "captured_output_refs": captured_output_refs,
             "later_messages_omitted": later_messages_omitted}
    later_excerpt = "\n".join(f"- {message}" for message in reversed(later_user_messages))
    if later_messages_omitted:
        later_excerpt += "\n[Earlier user messages omitted]"
    # Reserve separate space for each class, so a long objective cannot
    # crowd out constraints or the most recent command output.
    budget = min(2400, max(0, int(available * .6) - 20))
    parts = []
    for title, value, fraction in (
        ("Task objective", objective, .12),
        ("User constraints", "; ".join(constraints), .14),
        ("Later user messages (newest first; excerpts)", later_excerpt, .14),
        ("Assistant decisions (unverified)", "\n".join(reversed(decisions)), .12),
        ("Assistant pending-work excerpts (may be stale)", "\n".join(reversed(pending_items)), .10),
        ("Command output excerpts (not independently verified)", "\n".join(reversed(commands)), .10),
        ("File tool results (not independently verified)", "\n".join(reversed(write_results)), .10),
        ("File write requests (outcomes not established)", ", ".join(changed_paths), .06),
    ):
        if value:
            cap = int(budget * fraction) - len(title) - 6
            if cap > 16:
                parts.append(f"## {title}\n{_clip(value, cap)}")
    # Never truncate an opaque reference: a partial identifier cannot be read.
    title = "Output refs (read_tool_output; current run, unverified)"
    cap = int(budget * .12) - len(title) - 6
    refs = []
    for ref in reversed(captured_output_refs):
        if len(", ".join(refs + [ref])) > cap:
            break
        refs.append(ref)
    if refs:
        parts.append(f"## {title}\n{', '.join(refs)}")
    return "\n\n".join(parts) or "[Earlier context omitted: insufficient budget]", state


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:max(0, limit - 12)] + " [truncated]"


def _captured_output_ref(content: str) -> str:
    """Extract only the opaque reference, never the captured output or preview."""
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return ""
    output = payload.get("output") if isinstance(payload, dict) else None
    if not isinstance(output, dict) or output.get("_spilled") is not True:
        return ""
    ref = output.get("output_ref")
    return ref if isinstance(ref, str) and re.fullmatch(r"output_[0-9a-f]{32}", ref) else ""


def _command_excerpt(content: str) -> str:
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return _clip(content, 240)
    if not isinstance(payload, dict):
        return _clip(content, 240)
    output = payload.get("output")
    exit_code = output.get("exit_code") if isinstance(output, dict) else None
    facts = {"success": payload.get("success"), "exit_code": exit_code, "error": payload.get("error")}
    return json.dumps(facts, ensure_ascii=False) + " output excerpt: " + _clip(str(output), 160)


def _message_tokens(msg: Message) -> int:
    calls = msg.get("tool_calls")
    return estimate_tokens(_msg_text(msg)) + (estimate_tokens(json.dumps(calls, default=str)) if calls else 0)


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


_DECISION_MARKERS = (
    "我决定", "我们决定", "决定采用", "决定使用", "选择采用", "选择使用",
    "i decided", "we decided", "i chose", "we chose", "decision:",
)


def _is_decision(sentence: str) -> bool:
    """Keep explicit assistant decisions as attributed excerpts, not facts."""
    lowered = sentence.lower()
    return any(marker in lowered for marker in _DECISION_MARKERS)


def _is_pending(sentence: str) -> bool:
    """Retain explicit pending-work statements, attributed and possibly stale."""
    markers = ("待办", "尚未", "仍需", "还需", "下一步", "todo", "remaining", "next step", "still need")
    return any(marker in sentence.lower() for marker in markers)


def _write_result(content: str) -> str:
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return "result unknown"
    if not isinstance(payload, dict) or not isinstance(payload.get("success"), bool):
        return "result unknown"
    status = "reported success" if payload["success"] else "reported failure"
    error = payload.get("error")
    return status + (f"; error: {_clip(str(error), 120)}" if error else "")


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
        out.append({"id": call.get("id"), "name": name, "args": args if isinstance(args, dict) else {}})
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
            return _clip(value.strip(), 256)
    return ""


def _matching_command(msg: Message, group: list[Message]) -> dict[str, Any] | None:
    """Match a result only to its own command call, never a prior unrelated call."""
    return _matching_call(msg, group, ("exec_command", "bash", "run_terminal", "verify_subagent"))


def _matching_call(msg: Message, group: list[Message], names: tuple[str, ...]) -> dict[str, Any] | None:
    call_id = msg.get("tool_call_id")
    if not isinstance(call_id, str) or not call_id:
        return None
    for candidate in group:
        if candidate is msg:
            break
        if candidate.get("role") == "assistant":
            for call in _message_tool_calls(candidate):
                if call.get("id") == call_id and call.get("name") in names:
                    return call
    return None


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
