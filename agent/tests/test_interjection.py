"""Tests for the R24 mid-turn interjection buffer.

Pins the three pieces ported from grok-build's ``xai-interjection-core``:

* :func:`format_interjection` / :func:`user_query` — the canonical
  ``<user_query>`` envelope, the mid-turn note, and the 25_000-char
  truncation guard.
* :class:`EventQueue` — the clone-shared FIFO queue (``push`` /
  ``push_capped`` / ``drain_matching`` / ``drain_all`` / ``snapshot``).
  The headline invariant — **clones share one underlying queue** — is
  the grok ``Arc<Mutex<Vec<E>>>`` contract, and it gets its own test.
* :func:`drain_formatted` — one synthetic user message per buffered
  entry, FIFO, **never merged**, with the host ``sanitize_text`` hook
  running on the *raw* text first.

Plus four integration tests through :class:`AgentCore`: the
``queue_interjection`` / ``drain_interjections`` API surface and the
end-to-end run-loop drain — an interjection queued before a turn that
performs a tool call surfaces as a synthetic ``user`` message on the
LLM's *next* loop iteration, between the tool result and the follow-up
call (the grok "safe drain point").
"""

from __future__ import annotations

from typing import Any

import pytest

from minimax_code.agent.core import AgentConfig, AgentCore
from minimax_code.agent.interjection import (
    LARGE_PROMPT_THRESHOLD,
    EventQueue,
    FormattedInterjection,
    PendingInterjection,
    drain_formatted,
    format_interjection,
    user_query,
)
from minimax_code.agent.llm import StreamChunk
from minimax_code.agent.tools import Tool, ToolRegistry, ToolResult

# ---------------------------------------------------------------------------
# format.rs — envelope + truncation
# ---------------------------------------------------------------------------


def test_user_query_wraps_message_in_envelope() -> None:
    """The canonical envelope is exactly ``<user_query>\\n{msg}\\n</user_query>``."""
    assert user_query("hi") == "<user_query>\nhi\n</user_query>"


def test_format_interjection_wraps_short_text_with_mid_turn_note() -> None:
    """Short text is wrapped verbatim — no truncation — and carries the note."""
    out = format_interjection("stop now")
    assert out.startswith("The user sent a message while you are working:\n")
    assert "<user_query>\nstop now\n</user_query>" in out


def test_format_interjection_at_exact_threshold_is_not_truncated() -> None:
    """Boundary: text of exactly LARGE_PROMPT_THRESHOLD chars stays intact.

    The guard is ``> threshold`` (strict), matching grok — the last
    permitted length is the threshold itself.
    """
    text = "a" * LARGE_PROMPT_THRESHOLD
    out = format_interjection(text)
    assert "[truncated]" not in out
    assert text in out


def test_format_interjection_truncates_over_threshold_and_marks_it() -> None:
    """Text longer than the threshold is capped and tagged ``[truncated]``."""
    text = "b" * (LARGE_PROMPT_THRESHOLD + 10)
    out = format_interjection(text)
    assert "[truncated]" in out
    # The truncated body is exactly threshold chars — a runaway paste can't
    # blow the prompt budget.
    assert "b" * LARGE_PROMPT_THRESHOLD in out
    # And the over-budget tail is gone.
    assert "b" * (LARGE_PROMPT_THRESHOLD + 1) not in out


# ---------------------------------------------------------------------------
# events.rs — EventQueue
# ---------------------------------------------------------------------------


def test_push_then_len_reflects_count() -> None:
    q: EventQueue[int] = EventQueue()
    assert len(q) == 0
    q.push(1)
    q.push(2)
    assert len(q) == 2


def test_is_empty_reflects_state() -> None:
    q: EventQueue[int] = EventQueue()
    assert q.is_empty() is True
    q.push(7)
    assert q.is_empty() is False


def test_clones_share_one_underlying_queue() -> None:
    """The defining grok contract: clone() returns a *new handle* over the
    *same* backing queue. An event pushed through one clone is visible to
    every other clone, and a drain through one empties them all."""
    producer = EventQueue[str]()
    consumer = producer.clone()

    producer.push("a")
    producer.push("b")
    # The consumer clone sees what the producer pushed.
    assert len(consumer) == 2
    assert consumer.snapshot() == ["a", "b"]

    # Drain through the consumer; the producer sees the queue empty too.
    assert consumer.drain_all() == ["a", "b"]
    assert len(producer) == 0


def test_push_capped_drops_oldest_when_over_limit() -> None:
    """Over the cap, the oldest (front) entries are dropped to make room."""
    q: EventQueue[int] = EventQueue()
    q.push_capped(1, max_size=3)
    q.push_capped(2, max_size=3)
    q.push_capped(3, max_size=3)
    q.push_capped(4, max_size=3)  # over → drop the oldest (1)
    assert q.snapshot() == [2, 3, 4]


def test_push_capped_keeps_all_when_at_or_under_limit() -> None:
    """At or under the cap, nothing is dropped."""
    q: EventQueue[int] = EventQueue()
    q.push_capped(1, max_size=3)
    q.push_capped(2, max_size=3)
    q.push_capped(3, max_size=3)  # exactly at the cap
    assert q.snapshot() == [1, 2, 3]


def test_drain_matching_returns_matched_and_retains_rest_in_fifo() -> None:
    """Matching events are removed + returned; the rest stay, both in FIFO order."""
    q: EventQueue[int] = EventQueue()
    for n in (1, 2, 3, 4):
        q.push(n)
    evens = q.drain_matching(lambda e: e % 2 == 0)
    assert evens == [2, 4]
    assert q.snapshot() == [1, 3]


def test_drain_matching_none_match_retains_all() -> None:
    q: EventQueue[int] = EventQueue()
    for n in (1, 3, 5):
        q.push(n)
    matched = q.drain_matching(lambda e: e % 2 == 0)
    assert matched == []
    assert q.snapshot() == [1, 3, 5]


def test_drain_matching_empty_queue_returns_empty() -> None:
    q: EventQueue[int] = EventQueue()
    assert q.drain_matching(lambda e: True) == []


def test_drain_all_empties_in_fifo_order() -> None:
    q: EventQueue[str] = EventQueue()
    for s in ("first", "second", "third"):
        q.push(s)
    assert q.drain_all() == ["first", "second", "third"]
    assert q.is_empty() is True


def test_clear_discards_all() -> None:
    q: EventQueue[int] = EventQueue()
    q.push(1)
    q.push(2)
    q.clear()
    assert q.is_empty() is True
    assert q.snapshot() == []


def test_snapshot_reads_without_draining() -> None:
    """snapshot() is non-destructive — the queue still owns every entry."""
    q: EventQueue[int] = EventQueue()
    q.push(1)
    q.push(2)
    snap = q.snapshot()
    assert snap == [1, 2]
    # Still there.
    assert q.drain_all() == [1, 2]


# ---------------------------------------------------------------------------
# buffer.rs — drain_formatted
# ---------------------------------------------------------------------------


def test_drain_formatted_empty_buffer_returns_empty() -> None:
    buf: EventQueue[PendingInterjection] = EventQueue()
    assert drain_formatted(buf, sanitize_text=lambda s: s) == []


def test_drain_formatted_one_message_per_entry_never_merged() -> None:
    """Every drained entry becomes its OWN FormattedInterjection — two
    queued entries yield two formatted entries, never one concatenated blob."""
    buf: EventQueue[PendingInterjection] = EventQueue()
    buf.push(PendingInterjection(text="first"))
    buf.push(PendingInterjection(text="second"))

    drained = drain_formatted(buf, sanitize_text=lambda s: s)
    assert len(drained) == 2
    assert isinstance(drained[0], FormattedInterjection)
    assert isinstance(drained[1], FormattedInterjection)
    # Each carries exactly one envelope, not a merge.
    assert "first" in drained[0].text and "second" not in drained[0].text
    assert "second" in drained[1].text and "first" not in drained[1].text


def test_drain_formatted_wraps_each_entry_with_envelope() -> None:
    buf: EventQueue[PendingInterjection] = EventQueue()
    buf.push(PendingInterjection(text="hello"))
    drained = drain_formatted(buf, sanitize_text=lambda s: s)
    assert drained[0].text.startswith("The user sent a message while you are working:\n")
    assert "<user_query>\nhello\n</user_query>" in drained[0].text


def test_drain_formatted_applies_sanitize_to_raw_text_first() -> None:
    """sanitize_text runs on the RAW text before framing — hosts strip
    artifacts (e.g. image-placeholder paths) before the model ever sees it."""
    buf: EventQueue[PendingInterjection] = EventQueue()
    buf.push(PendingInterjection(text="see [img:0] here"))

    def strip_placeholders(s: str) -> str:
        return s.replace("[img:0]", "[attachment]")

    drained = drain_formatted(buf, sanitize_text=strip_placeholders)
    assert "[attachment]" in drained[0].text
    assert "[img:0]" not in drained[0].text


def test_drain_formatted_preserves_fifo_order() -> None:
    buf: EventQueue[PendingInterjection] = EventQueue()
    buf.push(PendingInterjection(text="one"))
    buf.push(PendingInterjection(text="two"))
    buf.push(PendingInterjection(text="three"))
    drained = drain_formatted(buf, sanitize_text=lambda s: s)
    assert ["one", "two", "three"] == [
        d.text.split("<user_query>\n", 1)[1].split("\n</user_query>", 1)[0]
        for d in drained
    ]


def test_drain_formatted_attachments_flow_through_untouched() -> None:
    """The core never inspects attachments — they ride along on the
    FormattedInterjection for the host to render however it likes."""
    buf: EventQueue[PendingInterjection] = EventQueue()
    buf.push(PendingInterjection(text="look", attachments=["asset://a", "asset://b"]))
    drained = drain_formatted(buf, sanitize_text=lambda s: s)
    assert drained[0].attachments == ["asset://a", "asset://b"]


def test_drain_formatted_drains_the_buffer() -> None:
    """drain_formatted empties the buffer — a second drain yields nothing."""
    buf: EventQueue[PendingInterjection] = EventQueue()
    buf.push(PendingInterjection(text="once"))
    assert len(drain_formatted(buf, sanitize_text=lambda s: s)) == 1
    assert drain_formatted(buf, sanitize_text=lambda s: s) == []


# ---------------------------------------------------------------------------
# AgentCore API surface
# ---------------------------------------------------------------------------


def _make_core() -> AgentCore:
    """A core with a no-op LLM stand-in — the API tests below drive the
    queue/drain methods directly and never enter the run loop."""

    class _NullLLM:
        async def stream_chat(self, *args: Any, **kwargs: Any):  # pragma: no cover
            yield  # type: ignore[unreachable]
            raise AssertionError("stream_chat must not be called by these tests")

    return AgentCore(llm=_NullLLM(), registry=ToolRegistry(), config=AgentConfig())


def test_queue_interjection_then_drain_roundtrip() -> None:
    """queue_interjection buffers; drain_interjections frames + returns them."""
    core = _make_core()
    core.queue_interjection("wait!")
    drained = core.drain_interjections()
    assert len(drained) == 1
    assert "wait!" in drained[0].text
    # Drained once → empty thereafter.
    assert core.drain_interjections() == []


def test_drain_interjections_empty_when_nothing_queued() -> None:
    core = _make_core()
    assert core.drain_interjections() == []


def test_drain_interjections_default_sanitize_is_identity() -> None:
    """With no sanitize_text kwarg, the raw text flows through untouched."""
    core = _make_core()
    core.queue_interjection("raw [img:0] text")
    drained = core.drain_interjections()
    assert "[img:0]" in drained[0].text


def test_drain_interjections_custom_sanitize_applied() -> None:
    """A custom sanitize_text kwarg is threaded through to drain_formatted."""
    core = _make_core()
    core.queue_interjection("secret: tok_123")
    drained = core.drain_interjections(sanitize_text=lambda s: s.replace("tok_123", "***"))
    assert "***" in drained[0].text
    assert "tok_123" not in drained[0].text


def test_queue_interjection_attachments_roundtrip() -> None:
    core = _make_core()
    core.queue_interjection("see this", attachments=["asset://x"])
    drained = core.drain_interjections()
    assert drained[0].attachments == ["asset://x"]


# ---------------------------------------------------------------------------
# Run-loop integration — the safe drain point
# ---------------------------------------------------------------------------


class _ScriptedLLM:
    """Replays a fixed list of chunk-lists in order, capturing the
    ``messages`` payload of every ``stream_chat`` call so the test can
    assert what the run loop fed to the LLM on each iteration."""

    def __init__(self, scripts: list[list[StreamChunk]]) -> None:
        self._scripts = list(scripts)
        self.captured: list[list[dict[str, Any]]] = []
        self.thinking_count = 0

    async def stream_chat(self, messages: list[dict[str, Any]], **_: Any):
        self.captured.append([dict(m) for m in messages])
        chunks = self._scripts.pop(0)
        for c in chunks:
            yield c


class _EchoTool(Tool):
    name = "echo"
    description = "echoes input"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        return ToolResult.ok(output={"echoed": kwargs.get("text")})


def _tool_call(name: str, args: dict[str, Any], call_id: str = "call_1") -> dict[str, Any]:
    import json

    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


@pytest.mark.asyncio
async def test_run_loop_drains_interjection_as_synthetic_user_message() -> None:
    """End-to-end: an interjection queued *before* a turn that performs a
    tool call is flushed at the safe drain point (between the tool turn and
    the follow-up LLM call) and surfaces as a synthetic ``user`` message in
    the LLM's *next* payload — exactly grok's post-tool ``drain_formatted``.
    """
    # LLM iteration 1 → echo tool_call; iteration 2 → final text.
    llm = _ScriptedLLM(
        scripts=[
            [
                StreamChunk(
                    tool_call_deltas=[_tool_call("echo", {"text": "hi"}, "call_a")],
                    finish_reason="tool_calls",
                ),
            ],
            [StreamChunk(delta="all done", finish_reason="stop")],
        ]
    )
    reg = ToolRegistry()
    reg.register(_EchoTool())
    core = AgentCore(
        llm=llm,  # type: ignore[arg-type]
        registry=reg,
        config=AgentConfig(),
    )

    # Queue the interjection BEFORE the turn starts — the run loop will pick
    # it up at the first drain point (after the echo tool completes).
    core.queue_interjection("hold on!")

    await core.run(session_id="s1", user_message="use the tool")

    # Two LLM iterations: tool-call turn, then follow-up turn.
    assert len(llm.captured) == 2
    second_payload = llm.captured[1]
    roles = [m["role"] for m in second_payload]

    # The interjection became a synthetic user message, sitting after the
    # tool result (the safe-point ordering), not merged into anything.
    assert "user" in roles
    inj_messages = [m for m in second_payload if m["role"] == "user"]
    assert any("hold on!" in str(m.get("content", "")) for m in inj_messages)
    # And it carries the canonical envelope + mid-turn note.
    assert any(
        "<user_query>" in str(m.get("content", ""))
        and "while you are working" in str(m.get("content", ""))
        for m in inj_messages
    )
    # The buffer is now empty — drained exactly once, never re-drained.
    assert core.drain_interjections() == []


@pytest.mark.asyncio
async def test_run_loop_without_interjection_is_unchanged() -> None:
    """Regression guard: with no interjection queued, the run loop behaves
    exactly as before R24 — no spurious synthetic user message appears."""
    llm = _ScriptedLLM(
        scripts=[
            [StreamChunk(delta="plain answer", finish_reason="stop")],
        ]
    )
    core = AgentCore(
        llm=llm,  # type: ignore[arg-type]
        registry=ToolRegistry(),
        config=AgentConfig(),
    )
    # Nothing queued.
    await core.run(session_id="s1", user_message="hi")
    assert len(llm.captured) == 1
    roles = [m["role"] for m in llm.captured[0]]
    assert roles.count("user") == 1  # only the original user message
