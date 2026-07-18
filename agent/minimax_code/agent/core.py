"""Agent conversation loop.

The core of the agent: given a user message and a session id, it

1. Loads (or reconstructs) the conversation history.
2. Streams the assistant turn from the LLM.
3. Parses any ``tool_calls`` the LLM emitted.
4. Dispatches each tool call through the :class:`ToolRegistry`.
5. Feeds the tool results back into the LLM.
6. Repeats until the LLM produces a final text answer, the
   max-iteration cap is hit, or the caller cancels.

Streaming surface
-----------------

The loop exposes a small set of *callbacks* (set as dataclass
attributes) that the IPC layer hooks up to push events to the
frontend. Callbacks are simple async callables; we call them
sequentially (in the order events happen) and any exception in
a callback is logged but does not abort the loop.

Cancellation
------------

The :class:`AgentCore.cancel` coroutine sets a flag the loop
checks on every chunk boundary. The active stream is also
closed via :meth:`httpx.Response.aclose` (best-effort) so the
underlying TCP connection does not leak. Cancellation is
cooperative — the loop finishes the current ``tool.run``
coroutine naturally before bailing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

from ..hooks import HookManager
from ..telemetry.tracing import Tracer, get_tracer
from .llm import LLMError, LLMResponse, MiniMaxClient, StreamChunk
from .prompts import build_system_prompt
from .reliability import (
    BreakerConfig,
    BreakerOpen,
    CircuitBreaker,
    CircuitBreakerRegistry,
    Outcome,
    RetryPolicy,
    with_retry,
)
from .tools import ToolRegistry, ToolResult, get_default_registry
from .types import LLMStreamTimeout

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Two-phase tool dispatch (R23 — fuse grok xai-tool-runtime concurrency model)
# ---------------------------------------------------------------------------
#
# grok splits a tool call into a serial *prepare* stage (arg parse +
# permission consent + pre-hook + breaker entry check) and a parallel
# *execute* stage (registry dispatch + truncate + post-hook + audit),
# so a batch of independent tool calls overlaps their I/O while same-file
# writes stay serialized. MiniMax previously ran the whole batch strictly
# serial. The split lives in :class:`_PreparedToolCall` — the small bag
# handed from Phase 1 to Phase 2.


@dataclass
class _PreparedToolCall:
    """Phase-1 output: one tool call parsed + permission-checked + breaker-checked.

    Either ``short_circuit`` is set — the call was rejected during prepare
    (deny / user-deny / hook-block / breaker-open / malformed-args /
    cancel) and that result was already emitted + audited — or the call
    is ready to execute and ``breaker`` / ``action`` are set for Phase 2.

    Carried between :meth:`AgentCore._prepare_tool_call` (serial) and
    :meth:`AgentCore._execute_tool_call` (concurrent) so the two phases
    stay decoupled: Phase 1 never reaches into Phase 2's locals and
    vice-versa.
    """

    call_log: dict[str, Any]
    name: str
    args: dict[str, Any]
    action: str
    breaker: CircuitBreaker | None
    short_circuit: ToolResult | None


#: Built-in tools whose dispatch mutates a file and so must serialize per
#: target path. Mirrors grok's write-tool kind set; reads / searches /
#: terminals are path-less or read-only and stay fully concurrent.
_WRITE_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file"})


# ---------------------------------------------------------------------------
# Permission gating
# ---------------------------------------------------------------------------
#
# The agent loop is wired with two optional collaborators:
#
# * ``permission_store`` — a :class:`~minimax_code.permissions.PermissionStore`
#   that knows the current rule set (action=allow/deny/ask per tool).
# * ``permission_gater`` — a :class:`~minimax_code.perm_consent.PermissionGater`
#   that emits the ``permission.request`` event and blocks until the
#   frontend POSTs ``permission.resolve``.
#
# When ``permission_gater`` is None the loop falls back to the store's
# ``is_allowed`` decision (default-allow for unconfigured tools), which
# is the behaviour every test that didn't opt in continues to see.


def _check_rule(
    store: Any,
    tool_name: str,
) -> str | None:
    """Return the configured action for ``tool_name`` (or ``None``).

    The result is one of ``"allow"`` / ``"deny"`` / ``"ask"`` /
    ``None`` (no rule). The :class:`PermissionStore` already returns
    ``True`` for unconfigured tools, but here we need the raw action
    string so we can distinguish "ask" from the default-allow path.
    """
    if store is None:
        return None
    rule = store.lookup(tool_name)
    if not rule:
        return None
    return str(rule.get("action") or "").strip().lower() or None


# ---------------------------------------------------------------------------
# Callback types
# ---------------------------------------------------------------------------


ChunkCallback = Callable[[str, bool, dict[str, Any] | None], Awaitable[None]]
"""(delta_text, done_flag, metadata) — pushed after every streamed chunk.

``done_flag`` is ``True`` only on the final chunk of the *whole
turn* (i.e. after all tool calls have been resolved).

``metadata`` is an optional dict the loop populates with the
per-turn thinking / token counts (``{"thinking_count": int,
"tokens_in": int, "tokens_out": int}``). It is set on **at most
one** chunk per turn — the loop picks a single chunk to carry
it (the one emitted right after the LLM call returns) and passes
``None`` for the rest. The downstream handler is free to ignore
it; the v0.3.0 ``agent.message_chunk`` wire format expects it on
at least one chunk per turn so the UI can render the
"思考 N 次" summary.
"""

ToolCallCallback = Callable[[dict[str, Any]], Awaitable[None]]
"""Pushed when a tool call starts executing."""

ToolResultCallback = Callable[[dict[str, Any], ToolResult], Awaitable[None]]
"""Pushed after a tool finishes, with its :class:`ToolResult`."""

StatusCallback = Callable[[str, dict[str, Any]], Awaitable[None]]
"""High-level status updates: "thinking", "calling_tool", "finalizing", …."""

UsageCallback = Callable[[dict[str, int]], Awaitable[None]]
"""Token usage updates — pushed once per LLM call."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class AgentConfig:
    """Tunable knobs for a single :class:`AgentCore` instance."""

    model: str = "MiniMax-M3"
    max_iterations: int = 12
    # Per-tool dispatch timeout (seconds). The tool itself can
    # also enforce its own (shorter) limit; this is the ceiling.
    tool_timeout: float = 120.0
    temperature: float | None = None
    system_prompt_extra: str | None = None
    skill_instructions: str | None = None
    # If non-None, every tool result that exceeds this many bytes
    # is truncated before being fed back to the LLM. Prevents
    # accidental 100-MB cat-bombs from blowing the context window.
    max_tool_output_bytes: int = 50_000
    # Per-chunk idle timeout (seconds). If no SSE chunk arrives from
    # the LLM within this window the turn is aborted with a
    # TimeoutError.  Set to 0 or None to disable.
    stall_timeout: float = 120.0
    # Compaction: when the conversation history exceeds this fraction
    # of the context window, older turns are summarised.  None disables
    # compaction (default).  Typical value: 0.8.
    compaction_threshold: float | None = None
    # Context window size (tokens) for the current model.  Used by
    # compaction to decide when to summarise.  None = unknown / disabled.
    context_window: int | None = None
    # R13 reliability gates.  None ⇒ use the module preset
    # (``RetryPolicy.llm`` / ``BreakerConfig.server`` / ``.client``).
    # Override to tune retry counts, breaker thresholds, or disable a
    # gate (a policy with max_attempts=1, or a breaker with enabled=False).
    llm_retry_policy: RetryPolicy | None = None
    llm_breaker_config: BreakerConfig | None = None
    tool_breaker_config: BreakerConfig | None = None


# ---------------------------------------------------------------------------
# Loop outcome
# ---------------------------------------------------------------------------


@dataclass
class AgentRunResult:
    """Return value of :meth:`AgentCore.run`."""

    final_text: str
    iterations: int
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    cancelled: bool = False
    truncated: bool = False  # hit max_iterations


# ---------------------------------------------------------------------------
# Conversation message bookkeeping
# ---------------------------------------------------------------------------


Message = dict[str, Any]


# ---------------------------------------------------------------------------
# Agent core
# ---------------------------------------------------------------------------


class AgentCore:
    """Drives a single conversation turn against an LLM + tool registry.

    Parameters
    ----------
    llm:
        Configured :class:`MiniMaxClient`. The agent does **not**
        close it on shutdown — callers are expected to manage
        the lifecycle.
    registry:
        The :class:`ToolRegistry` the loop will dispatch into.
        Defaults to the process-wide registry, which already
        contains file_ops, terminal, edit, and search.
    config:
        :class:`AgentConfig` with the per-instance tunables.
    history_provider:
        Async callable ``(session_id) -> list[Message]`` that
        returns the messages preceding the new user turn. When
        ``None``, the loop assumes a fresh session.
    persist_message:
        Async callable ``(session_id, message) -> None`` invoked
        after each new message is appended (user, assistant, or
        tool). The storage-layer ``MessagesDAO.create`` satisfies
        this contract directly. ``None`` disables persistence
        (tests rely on this).
    callbacks:
        Optional pre-bound :class:`Callbacks` dataclass; the
        attribute-style API (``core.on_chunk = ...``) is
        equivalent. Both are merged.
    """

    def __init__(
        self,
        *,
        llm: MiniMaxClient | None = None,
        registry: ToolRegistry | None = None,
        config: AgentConfig | None = None,
        history_provider: Callable[[str], Awaitable[Sequence[Message]]] | None = None,
        persist_message: Callable[[str, Message], Awaitable[None]] | None = None,
        permission_store: Any | None = None,
        permission_gater: Any | None = None,
        hooks: HookManager | None = None,
    ) -> None:
        self.llm = llm or MiniMaxClient()
        self.registry = registry or get_default_registry()
        self.config = config or AgentConfig()
        self._history_provider = history_provider
        self._persist_message = persist_message
        self._cancel = asyncio.Event()
        self._permission_store = permission_store
        self._permission_gater = permission_gater
        # Lifecycle hooks (R7). None ⇒ disabled, zero overhead.
        self.hooks: HookManager | None = hooks
        self._current_session_id: str = ""
        # Audit DAO — when set, every tool dispatch is recorded for
        # traceability.  Set externally via ``core.audit_dao = dao``.
        self.audit_dao: Any | None = None
        self._audit_session_id: str | None = None
        # Telemetry engine (R11) — when set, every tool dispatch is mirrored
        # into the in-memory event bus for real-time observability. Set
        # externally (mirrors audit_dao). None ⇒ disabled, zero overhead.
        self.telemetry_engine: Any | None = None
        self.on_chunk: ChunkCallback | None = None
        self.on_tool_call: ToolCallCallback | None = None
        self.on_tool_result: ToolResultCallback | None = None
        self.on_status: StatusCallback | None = None
        self.on_usage: UsageCallback | None = None
        # R13 — circuit-breaker registry. One breaker per protected key
        # ("llm" + "tool:<name>"), lazily created on first check. Retry +
        # breaker knobs are read from self.config at call time so they
        # stay hot-reloadable across a session.
        self._breakers = CircuitBreakerRegistry()
        # R23 — per-file write locks. Keyed by the target file path so
        # concurrent write_file / edit_file calls against the *same* path
        # serialize (mirrors grok's lock_path_for_args); cross-file writes
        # and every read / search / terminal call stay fully concurrent.
        self._write_locks: dict[str, asyncio.Lock] = {}
        # R20 — telemetry adapter for the ``llm`` breaker. Resolves the engine
        # lazily through a closure over ``self`` (the engine is injected from
        # ``app.py`` after this constructor returns), so a late-arriving engine
        # still observes breaker transitions on the next turn. The import is
        # deferred to call time to mirror ``_record_audit`` and keep ``core``'s
        # top-level import graph free of a telemetry re-entry.
        from ..telemetry.observer_adapter import ReliabilityTelemetryObserver

        self._llm_breaker_observer = ReliabilityTelemetryObserver(
            lambda: self.telemetry_engine, name="llm"
        )
        # R14 — process tracer. Opens one root span per turn (``agent.turn``)
        # so every LLM/tool span inside the loop nests under a shared
        # trace_id. Lazily wired to the telemetry engine via ``get_tracer``;
        # emits are fail-open, so this never blocks or raises.
        self._tracer: Tracer = get_tracer()

    # -- cancellation ------------------------------------------------------

    def cancel(self) -> None:
        """Request cancellation. Idempotent and async-safe."""
        self._cancel.set()

    def reset_cancel(self) -> None:
        self._cancel.clear()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # -- main entry point --------------------------------------------------

    async def run(
        self,
        *,
        session_id: str,
        user_message: str,
    ) -> AgentRunResult:
        """Run one conversation turn.

        ``user_message`` is appended to the history as a ``user``
        role message. The function streams the assistant's reply
        (and any intermediate tool results) via the configured
        callbacks, and returns a :class:`AgentRunResult` with the
        final text and accounting.
        """
        self.reset_cancel()
        self._current_session_id = session_id

        history: list[Message] = []
        if self._history_provider is not None:
            history = list(await self._history_provider(session_id) or [])

        # Compaction: compress older turns if approaching context limit.
        if self.config.compaction_threshold and self.config.context_window:
            from .compaction import compact_history, estimate_tokens
            total = sum(estimate_tokens(str(m)) for m in history)
            threshold = int(self.config.context_window * self.config.compaction_threshold)
            if total > threshold:
                logger.info(
                    "compacting history: %d estimated tokens > %d threshold",
                    total, threshold,
                )
                history = compact_history(
                    history, max_tokens=threshold, keep_recent=4,
                )

        system_prompt = build_system_prompt(
            extra=self.config.system_prompt_extra,
            skill_instructions=self.config.skill_instructions,
            model_name=self.config.model,
        )

        messages: list[Message] = [{"role": "system", "content": system_prompt}]
        messages.extend(history)

        user_msg: Message = {"role": "user", "content": user_message}
        messages.append(user_msg)
        await self._maybe_persist(session_id, user_msg)

        tool_calls_log: list[dict[str, Any]] = []
        tool_results_log: list[ToolResult] = []
        usage_total: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        iterations = 0
        cancelled = False
        truncated = False
        final_text = ""
        final_message: Message | None = None

        # R14 — root span wraps the whole turn so every LLM span
        # (``_call_llm_with_resilience``) and every tool span
        # (``_dispatch_tool``) opened inside the loop nests under one
        # trace_id. The waterfall this produces answers "where did this
        # turn's wall-clock go" — the flat R11 event log could not.
        async with self._tracer.start_span(
            "agent.turn",
            session_id=session_id,
            user_message=(user_message or "")[:200],
            max_iterations=self.config.max_iterations,
        ):
            for iteration in range(self.config.max_iterations):
                iterations = iteration + 1
                if self.cancelled:
                    cancelled = True
                    break

                await self._emit_status("thinking", {"iteration": iterations})

                try:
                    response = await self._call_llm_with_resilience(messages)
                except LLMError as exc:
                    await self._emit_status(
                        "error",
                        {"iteration": iterations, "detail": str(exc)},
                    )
                    raise

                _accumulate_usage(usage_total, response.usage)
                await self._maybe_emit_usage(response.usage)

                # Persist the assistant message that came out of this
                # LLM call (may contain tool_calls).
                assistant_msg = response.message
                messages.append(assistant_msg)
                await self._maybe_persist(session_id, assistant_msg)

                tool_calls = _extract_tool_calls(assistant_msg)
                if not tool_calls:
                    # Final answer.
                    final_text = assistant_msg.get("content") or ""
                    final_message = assistant_msg
                    # Attach the per-turn metadata to the done=True
                    # chunk so the UI can paint the "思考 N 次" line.
                    # All earlier chunks passed ``metadata=None``; the
                    # store keeps the latest non-null value per message.
                    await self._emit_chunk("", True, response.metadata)
                    await self._emit_status("done", {"iterations": iterations})
                    break

                # Resolve each tool call, feed the results back.
                await self._emit_status("calling_tool", {"iteration": iterations, "count": len(tool_calls)})
                tool_messages: list[Message] = []
                # R23 — two-phase batch dispatch: serial prepare
                # (permission / hooks / breaker / status emits stay
                # ordered) then parallel execute (per-file-locked
                # registry dispatch). gather preserves call order in the
                # returned list so the tool messages line up 1:1 with
                # the LLM's tool_calls, even though execution overlaps.
                results = await self._run_tool_batch(tool_calls)
                for call, result in zip(tool_calls, results, strict=True):
                    tool_calls_log.append(call)
                    tool_results_log.append(result)
                    tool_msg = _tool_message(call, result)
                    messages.append(tool_msg)
                    tool_messages.append(tool_msg)
                    await self._maybe_persist(session_id, tool_msg)
                if self.cancelled:
                    cancelled = True
                    break
                # Loop back to call the LLM with the tool messages.
            else:
                # Loop exhausted without a final answer.
                truncated = True
                await self._emit_status("max_iterations", {"iterations": self.config.max_iterations})
                final_text = (
                    f"I stopped after reaching the {self.config.max_iterations}-iteration limit "
                    "before producing a final answer."
                )
                final_message = {
                    "role": "assistant",
                    "content": final_text,
                    "metadata": {
                        "thinking_count": int(getattr(self.llm, "thinking_count", 0) or 0),
                        "tokens_in": int(usage_total.get("prompt_tokens", 0) or 0),
                        "tokens_out": int(usage_total.get("completion_tokens", 0) or 0),
                        "truncated": True,
                    },
                }
                messages.append(final_message)
                await self._maybe_persist(session_id, final_message)
                await self._emit_chunk(final_text, True, final_message["metadata"])

        return AgentRunResult(
            final_text=final_text,
            iterations=iterations,
            tool_calls=tool_calls_log,
            tool_results=tool_results_log,
            usage=usage_total,
            cancelled=cancelled,
            truncated=truncated,
        )

    # -- streaming + tool dispatch -----------------------------------------

    async def _call_llm_with_resilience(self, messages: list[Message]) -> LLMResponse:
        """LLM call wrapped in circuit-breaker + app-level retry (R13).

        Wraps :meth:`_stream_turn` with two gates MiniMax previously
        lacked:

        - a per-``"llm"`` circuit breaker (``BreakerConfig.server`` preset
          unless ``config.llm_breaker_config`` overrides). A ``BreakerOpen``
          on the entry check is re-raised as :class:`LLMError` so the
          caller's existing ``except LLMError`` branch handles it uniformly.
        - :func:`with_retry` with ``config.llm_retry_policy`` (or the
          ``RetryPolicy.llm`` preset). The original exception is preserved
          on exhaustion so callers see the real ``LLMError`` / timeout.

        Exactly one outcome (success / failure of the whole retried
        sequence) is recorded into the breaker — mirroring grok's "one
        record per logical call" semantics, not one per attempt.
        """
        breaker = await self._breakers.get_or_create(
            "llm", self.config.llm_breaker_config or BreakerConfig.server(),
        )
        # R20 — re-attach the telemetry observer each turn (idempotent). The
        # breaker may have been created on a prior turn before the engine was
        # injected, so a late-arriving engine still observes future transitions.
        breaker.attach_observer(self._llm_breaker_observer)
        try:
            await breaker.check()
        except BreakerOpen as exc:
            raise LLMError(
                "LLM circuit breaker open — too many recent failures "
                f"(retry after {exc.retry_after:.0f}s)",
                status_code=503,
                breaker_open=True,
            ) from exc
        try:
            # R14 — one LLM span per turn-loop iteration; nests under the
            # root ``agent.turn`` span via the contextvars parent chain.
            async with self._tracer.start_span(
                "llm.stream", model=getattr(self.config, "model", None)
            ):
                # R20 — on_retry emits one RETRY telemetry event per retried
                # attempt (carrying this turn's session_id), fusing grok's
                # xai-grok-telemetry retry-event into the in-memory bus.
                response = await with_retry(
                    lambda: self._stream_turn(messages),
                    self.config.llm_retry_policy or RetryPolicy.llm(),
                    on_retry=self._on_llm_retry,
                )
        except BaseException:
            await breaker.record(Outcome.FAILURE)
            raise
        await breaker.record(Outcome.SUCCESS)
        return response

    def _on_llm_retry(
        self, attempt: int, exc: BaseException, delay: float
    ) -> None:
        """Emit one ``RETRY`` telemetry event per retried LLM attempt (R20).

        Called from :func:`with_retry`'s ``on_retry`` hook before each backoff
        sleep. Fail-open: a missing engine or an emit fault never breaks the
        retry loop (``with_retry`` itself also wraps this hook in try/except,
        so the guard here is belt-and-braces). The event carries the owning
        turn's ``session_id`` so retries are attributable per-conversation.

        ``breaker_open`` in the payload lets a dashboard distinguish retries
        that ended TERMINAL (breaker shedding load — R19 fused this signal)
        from ordinary transient retries; without it a breaker-open retry
        looks identical to a flaky-network retry in the event stream.
        """
        engine = self.telemetry_engine
        if engine is None:
            return  # telemetry disabled — zero overhead, no allocation
        try:
            from ..telemetry import EventType, Severity, TelemetryEvent

            engine.emit(
                TelemetryEvent(
                    type=EventType.RETRY,
                    session_id=self._current_session_id or None,
                    severity=Severity.WARN,
                    name="llm",
                    payload={
                        "attempt": attempt,
                        "max_attempts": (
                            self.config.llm_retry_policy or RetryPolicy.llm()
                        ).max_attempts,
                        "delay_s": round(delay, 3),
                        "error_type": type(exc).__name__,
                        "breaker_open": bool(getattr(exc, "breaker_open", False)),
                    },
                )
            )
        except Exception:  # noqa: BLE001 — telemetry must not break retry
            logger.debug("telemetry retry emit failed", exc_info=True)

    async def _stream_turn(self, messages: list[Message]) -> LLMResponse:
        """Stream one LLM call and stitch the chunks into a response.

        The streaming path is used uniformly — :meth:`MiniMaxClient.chat`
        is just ``stream_chat`` with all chunks buffered. The
        ``on_chunk`` callback fires after every chunk so the
        frontend sees the text appearing live.

        A per-chunk idle timeout (``config.stall_timeout``) aborts the
        turn if the LLM stops sending data mid-stream.  This protects
        against silently hanging connections where the SSE stream is
        open but no bytes arrive.
        """
        tools_payload = self.registry.to_llm_functions()
        chunks: list[StreamChunk] = []
        stall = self.config.stall_timeout or 0
        aiter = self.llm.stream_chat(
            messages,
            model=self.config.model,
            tools=tools_payload or None,
            tool_choice="auto" if tools_payload else None,
            temperature=self.config.temperature,
        ).__aiter__()
        while True:
            try:
                if stall and stall > 0:
                    chunk = await asyncio.wait_for(aiter.__anext__(), timeout=stall)
                else:
                    chunk = await aiter.__anext__()
            except StopAsyncIteration:
                break
            except TimeoutError as exc:
                logger.warning(
                    "stream stall: no chunk for %.0fs, aborting turn", stall,
                )
                timeout_notice = (
                    "\n\nLLM stream stopped responding. This run was stopped; "
                    "retry to continue."
                )
                await self._emit_chunk(timeout_notice, False, None)
                raise LLMStreamTimeout(stall) from exc
            if self.cancelled:
                break
            chunks.append(chunk)
            if chunk.delta:
                await self._emit_chunk(chunk.delta, False, None)
        response = _assemble_chunks(chunks, model=self.config.model)
        # Build the per-turn metadata snapshot. Reading
        # ``self.llm.thinking_count`` here is the only way the
        # streaming path can pick up the value — the per-call
        # counter is reset at the top of every ``stream_chat`` and
        # updated as the final usage chunk is parsed.
        thinking_count = int(getattr(self.llm, "thinking_count", 0) or 0)
        tokens_in = int(response.usage.get("prompt_tokens", 0) or 0)
        tokens_out = int(response.usage.get("completion_tokens", 0) or 0)
        response.metadata = {
            "thinking_count": thinking_count,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        }
        return response

    async def _dispatch_tool(self, call: dict[str, Any]) -> ToolResult:
        """Execute one tool call end-to-end (single-call entry point).

        R23 splits the legacy monolithic dispatch into two phases so a
        batch of tool calls can overlap their I/O (see
        :meth:`_run_tool_batch`). This method is the back-compat shell
        that keeps every existing single-call call site and test working
        unchanged — it prepares one call, then executes it.

        Permission gating, hook firing and circuit-breaker semantics are
        documented on :meth:`_prepare_tool_call` / :meth:`_execute_tool_call`.
        """
        prepared = await self._prepare_tool_call(call)
        return await self._execute_tool_call(prepared)

    async def _prepare_tool_call(self, call: dict[str, Any]) -> _PreparedToolCall:
        """Phase 1 (serial): parse args → permission → pre-hook → breaker.

        Every step that must stay ordered lives here: argument parsing,
        the interactive permission-consent prompt (concurrent prompts
        would race the UI), the ``pre_tool_use`` policy hook, and the
        per-tool circuit-breaker entry check (a shared state read). On
        any rejection — deny / user-deny / hook-block / breaker-open /
        malformed args — the call short-circuits: the offending result
        is emitted + audited here and carried as ``short_circuit`` so
        :meth:`_execute_tool_call` returns it without dispatching.

        Returns a :class:`_PreparedToolCall` carrying the parsed args,
        the resolved permission ``action`` (for the audit trail) and the
        breaker handle (for the post-dispatch outcome record). The
        ``tool_call`` / ``tool_running`` status events are emitted here so
        the UI learns call order even when execution is parallel.
        """
        name = call.get("name") or (call.get("function") or {}).get("name", "")
        raw_args = call.get("arguments")
        if raw_args is None and isinstance(call.get("function"), dict):
            raw_args = call["function"].get("arguments")
        logger.debug(
            "prepare_tool_call: id=%s name=%r raw_args_type=%s",
            call.get("id", ""), name, type(raw_args).__name__,
        )
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError as exc:
                err = ToolResult.fail(f"tool '{name}' got malformed JSON args: {exc}")
                await self._maybe_emit_tool_call(call, err)
                return _PreparedToolCall(
                    call_log={**call, "name": name, "args": {}},
                    name=name, args={}, action="malformed",
                    breaker=None, short_circuit=err,
                )
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            args = {}

        tool_call_id = call.get("id") or f"toolu_{uuid.uuid4().hex[:24]}"
        call_log = {**call, "id": tool_call_id, "name": name, "args": args}

        # 1. Permission check (configurable, default-allow).
        action = _check_rule(self._permission_store, name)
        if action == "deny":
            denied = ToolResult.fail(
                f"tool '{name}' denied by permission policy (action=deny)",
                output={"permission": "deny", "tool": name, "args": args},
                permission="deny",
            )
            await self._maybe_emit_tool_call(call_log, None)
            await self._maybe_emit_tool_result(call_log, denied)
            await self._emit_status(
                "permission_denied", {"tool": name, "tool_call_id": tool_call_id}
            )
            # Audit: denied by policy.
            await self._record_audit(
                call_log, "denied", permission="deny", duration_ms=0,
            )
            return _PreparedToolCall(
                call_log, name, args, "deny", None, denied,
            )
        if action == "ask" and self._permission_gater is not None:
            await self._maybe_emit_tool_call(call_log, None)
            allowed = await self._permission_gater.request_consent(
                tool=name, args=args
            )
            if not allowed:
                denied = ToolResult.fail(
                    f"tool '{name}' denied by user",
                    output={"permission": "user_deny", "tool": name, "args": args},
                    permission="user_deny",
                )
                await self._maybe_emit_tool_result(call_log, denied)
                await self._emit_status(
                    "permission_denied",
                    {"tool": name, "tool_call_id": tool_call_id, "by": "user"},
                )
                # Audit: denied by user.
                await self._record_audit(
                    call_log, "denied", permission="user_deny", duration_ms=0,
                )
                return _PreparedToolCall(
                    call_log, name, args, "user_deny", None, denied,
                )

        # 1b. pre_tool_use hooks — policy gate (fail-open). Runs only
        # after permission allows, so a denied tool never wastes a hook.
        if self.hooks is not None and name:
            pre = await self.hooks.fire_pre_tool_use(
                self._current_session_id, name, args
            )
            if pre.blocked:
                blocked = ToolResult.fail(
                    pre.block_reason
                    or f"tool '{name}' blocked by pre_tool_use hook",
                    output={"hook": "pre_tool_use", "tool": name, "args": args},
                    permission="hook_block",
                )
                await self._maybe_emit_tool_call(call_log, None)
                await self._maybe_emit_tool_result(call_log, blocked)
                await self._emit_status(
                    "hook_blocked",
                    {"tool": name, "tool_call_id": tool_call_id},
                )
                await self._record_audit(
                    call_log, "blocked", permission="hook_block", duration_ms=0,
                )
                return _PreparedToolCall(
                    call_log, name, args, "hook_block", None, blocked,
                )

        await self._maybe_emit_tool_call(call_log, None)
        # R13 — per-tool circuit breaker. A flaky tool the LLM keeps
        # re-invoking would otherwise burn all max_iterations before the
        # loop gives up; the breaker fast-fails it after consecutive
        # crashes/timeouts instead. The breaker's internal asyncio.Lock
        # makes the entry check safe even when Phase 2 runs siblings of
        # the same tool concurrently.
        tool_breaker: CircuitBreaker | None = None
        if name:
            tool_breaker = await self._breakers.get_or_create(
                f"tool:{name}",
                self.config.tool_breaker_config or BreakerConfig.client(),
            )
            try:
                await tool_breaker.check()
            except BreakerOpen as exc:
                blocked = ToolResult.fail(
                    f"tool '{name}' circuit open after repeated failures "
                    f"(retry in {exc.retry_after:.0f}s)"
                )
                await self._maybe_emit_tool_result(call_log, blocked)
                await self._emit_status(
                    "circuit_open",
                    {"tool": name, "tool_call_id": tool_call_id},
                )
                await self._record_audit(
                    call_log, "blocked", permission="circuit_open", duration_ms=0,
                )
                return _PreparedToolCall(
                    call_log, name, args, "circuit_open", None, blocked,
                )
        await self._emit_status("tool_running", {"tool": name})
        return _PreparedToolCall(
            call_log, name, args, action or "allow", tool_breaker, None,
        )

    async def _execute_tool_call(self, prepared: _PreparedToolCall) -> ToolResult:
        """Phase 2 (parallel): per-file-locked dispatch → truncate → emit → audit.

        Runs the registry call under an optional per-file ``asyncio.Lock``
        so concurrent writes to the *same* path serialize (mirrors grok's
        ``lock_path_for_args`` rule — "only write tools targeting the
        same file path are serialized"; reads, searches and cross-file
        writes all run concurrently). ``nullcontext`` makes the no-lock
        path zero-overhead. Everything else — truncate, the
        ``post_tool_use`` notification hook, the audit write — is
        independent per call, so the whole phase is ``gather``-safe.

        A prepared call with a non-null ``short_circuit`` (deny / block /
        breaker-open / malformed / cancel) is returned verbatim — the
        rejection was already emitted + audited in
        :meth:`_prepare_tool_call`, so we must not double-emit here.
        """
        if prepared.short_circuit is not None:
            return prepared.short_circuit

        name = prepared.name
        args = prepared.args
        call_log = prepared.call_log
        tool_call_id = call_log.get("id", "")
        tool_breaker = prepared.breaker
        action = prepared.action
        lock = self._write_lock_for(name, args)

        duration_ms = 0
        # R23 — serialize same-file writes; everything else runs free.
        async with (lock or nullcontext()):
            # R14 — one tool span per dispatch; nests under ``agent.turn``.
            # Times only the registry dispatch, so the span duration is
            # the true tool execution time (audit write is out of span).
            async with self._tracer.start_span(
                f"tool.{name}" if name else "tool.dispatch",
                tool=name or "unknown",
                tool_call_id=tool_call_id,
            ):
                t0 = time.monotonic()
                try:
                    result = await asyncio.wait_for(
                        self.registry.dispatch(name, args),
                        timeout=self.config.tool_timeout,
                    )
                except TimeoutError:
                    result = ToolResult.fail(
                        f"tool '{name}' exceeded {self.config.tool_timeout:.0f}s timeout"
                    )
                    if tool_breaker is not None:
                        await tool_breaker.record(Outcome.FAILURE)
                except Exception as exc:  # pragma: no cover — defensive
                    logger.exception("tool %s crashed", name)
                    result = ToolResult.fail(f"{type(exc).__name__}: {exc}")
                    if tool_breaker is not None:
                        await tool_breaker.record(Outcome.FAILURE)
                else:
                    # A normal return — even a business-level
                    # ToolResult.fail — means the tool is reachable; only
                    # crashes / timeouts trip the breaker.
                    if tool_breaker is not None:
                        await tool_breaker.record(Outcome.SUCCESS)
                duration_ms = int((time.monotonic() - t0) * 1000)

        # Truncate pathological output to keep the context window sane.
        result = _truncate_result(result, self.config.max_tool_output_bytes)

        await self._maybe_emit_tool_result(call_log, result)

        # post_tool_use hooks — notification (fail-open). A bad hook
        # must never corrupt the real tool result already in hand.
        if self.hooks is not None and name:
            try:
                await self.hooks.fire_post_tool_use(
                    self._current_session_id, name, args, result.to_dict()
                )
            except Exception:  # noqa: BLE001
                logger.warning("post_tool_use hooks raised for %s", name, exc_info=True)

        # Audit: record the tool dispatch (fire-and-forget).
        status = "success" if result.success else (
            "timeout" if "timeout" in (result.error or "") else "fail"
        )
        exit_code = getattr(result, "exit_code", None)
        await self._record_audit(
            call_log, status,
            permission=action,
            duration_ms=duration_ms,
            error=result.error if not result.success else None,
            exit_code=exit_code,
        )
        return result

    async def _run_tool_batch(
        self, tool_calls: list[dict[str, Any]],
    ) -> list[ToolResult]:
        """Two-phase dispatch for a batch of tool calls (R23).

        Phase 1 (serial ``prepare``) walks the calls one at a time so
        interactive consent prompts, breaker entry checks and the
        ``tool_call`` / ``tool_running`` status emits stay ordered — a UI
        that shows "calling edit_file, then read_file" must not see them
        interleaved.

        Phase 2 (parallel ``execute``) runs every prepared call via
        :func:`asyncio.gather`, which preserves call order in the
        returned list even though execution overlaps. Same-file writes
        are kept correct by the per-file lock inside
        :meth:`_execute_tool_call`; cross-file writes, reads and every
        other tool run concurrently — the grok ``xai-tool-runtime`` model
        MiniMax previously lacked.

        A mid-batch cancellation marks the unprepared tail as cancelled
        (each gets a cancelled :class:`ToolResult` so the OpenAI-style
        tool-call ↔ tool-result pairing stays complete); in-flight
        executes are allowed to finish (bounded by ``tool_timeout``).
        """
        prepared: list[_PreparedToolCall] = []
        for call in tool_calls:
            if self.cancelled:
                prepared.append(self._cancel_prepared(call))
                continue
            prepared.append(await self._prepare_tool_call(call))
        if not prepared:
            return []
        return list(await asyncio.gather(
            *(self._execute_tool_call(p) for p in prepared)
        ))

    def _cancel_prepared(self, call: dict[str, Any]) -> _PreparedToolCall:
        """Build a short-circuiting prepared call for a cancelled slot.

        The result carries ``action="cancelled"`` and a failed
        :class:`ToolResult`; :meth:`_execute_tool_call` returns it
        verbatim. We do NOT emit ``tool_call`` / ``tool_result`` here
        (cancellation is silent at the event layer) — the caller still
        persists the tool message so the LLM sees a result for every
        tool_call id it emitted (OpenAI tool-call pairing stays whole).
        """
        name = call.get("name") or (call.get("function") or {}).get("name", "")
        cancelled = ToolResult.fail(f"tool '{name}' cancelled")
        return _PreparedToolCall(
            call_log={**call, "name": name, "args": {}},
            name=name, args={}, action="cancelled",
            breaker=None, short_circuit=cancelled,
        )

    def _write_lock_for(
        self, name: str, args: dict[str, Any],
    ) -> asyncio.Lock | None:
        """Return the per-file write lock for ``name``+``args``, or ``None``.

        Ports grok's ``lock_path_for_args`` rule: only *write* tools that
        target a concrete file path are serialized, and only against
        siblings writing the *same* path. The path key is read from the
        first present of ``file_path`` / ``path`` / ``target_file``
        (MiniMax's built-in write tools — ``edit_file``, ``write_file`` —
        both use ``path`` today; the alternates future-proof tools that
        follow the grok naming). Reads, searches, terminals and path-less
        writes return ``None`` → fully concurrent.
        """
        if name not in _WRITE_TOOLS:
            return None
        path = args.get("file_path") or args.get("path") or args.get("target_file")
        if not isinstance(path, str) or not path:
            return None
        lock = self._write_locks.get(path)
        if lock is None:
            lock = asyncio.Lock()
            self._write_locks[path] = lock
        return lock

    # -- audit ---------------------------------------------------------------

    _SANITIZE_KEYS = frozenset({
        "api_key", "token", "secret", "password", "authorization",
        "apikey", "access_token", "refresh_token", "private_key",
    })

    async def _record_audit(
        self,
        call_log: dict[str, Any],
        result_status: str,
        *,
        permission: str | None = None,
        duration_ms: int = 0,
        error: str | None = None,
        exit_code: int | None = None,
    ) -> None:
        """Write an audit record (fire-and-forget). Errors are logged but
        never propagated — the audit trail must not break the tool loop.

        Also mirrors the dispatch into the in-memory telemetry bus (R11)
        when an engine is attached — real-time observability is independent
        of on-disk persistence. Fail-open always.
        """
        engine = self.telemetry_engine
        if engine is not None:
            try:
                from ..telemetry import EventType, Severity, TelemetryEvent

                severity = (
                    Severity.ERROR if result_status == "error" else Severity.INFO
                )
                engine.emit(
                    TelemetryEvent(
                        type=EventType.TOOL_CALL,
                        session_id=self._audit_session_id,
                        severity=severity,
                        name=call_log.get("name", "unknown"),
                        payload={
                            "tool": call_log.get("name", "unknown"),
                            "args": _sanitize_args(
                                call_log.get("args", {}), self._SANITIZE_KEYS
                            ),
                            "permission": permission,
                            "status": result_status,
                            "exit_code": exit_code,
                            "duration_ms": duration_ms,
                            "error": (error or "")[:500] if error else None,
                        },
                    )
                )
            except Exception:  # noqa: BLE001 — telemetry must not break tools
                logger.debug("telemetry tool_call emit failed", exc_info=True)
        dao = self.audit_dao
        if dao is None:
            return
        try:
            await dao.record(
                session_id=self._audit_session_id,
                tool_name=call_log.get("name", "unknown"),
                tool_args=_sanitize_args(call_log.get("args", {}), self._SANITIZE_KEYS),
                permission=permission,
                result_status=result_status,
                exit_code=exit_code,
                duration_ms=duration_ms,
                error=error,
            )
        except Exception:  # pragma: no cover — defensive
            logger.exception("audit recording failed for tool %s", call_log.get("name"))

    # -- callback helpers --------------------------------------------------

    async def _emit_chunk(
        self,
        delta: str,
        done: bool,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.on_chunk is None:
            return
        try:
            await self.on_chunk(delta, done, metadata)
        except Exception:  # pragma: no cover — defensive
            logger.exception("on_chunk callback raised")

    async def _maybe_emit_tool_call(self, call: dict[str, Any], result: ToolResult | None) -> None:
        if self.on_tool_call is None:
            return
        try:
            await self.on_tool_call(call)
        except Exception:  # pragma: no cover
            logger.exception("on_tool_call callback raised")

    async def _maybe_emit_tool_result(self, call: dict[str, Any], result: ToolResult) -> None:
        if self.on_tool_result is None:
            return
        try:
            await self.on_tool_result(call, result)
        except Exception:  # pragma: no cover
            logger.exception("on_tool_result callback raised")

    async def _emit_status(self, status: str, detail: dict[str, Any]) -> None:
        if self.on_status is None:
            return
        try:
            await self.on_status(status, detail)
        except Exception:  # pragma: no cover
            logger.exception("on_status callback raised")

    async def _maybe_emit_usage(self, usage: dict[str, int]) -> None:
        if self.on_usage is None or not usage:
            return
        try:
            await self.on_usage(usage)
        except Exception:  # pragma: no cover
            logger.exception("on_usage callback raised")

    async def _maybe_persist(self, session_id: str, message: Message) -> None:
        if self._persist_message is None:
            return
        try:
            await self._persist_message(session_id, dict(message))
        except Exception:  # pragma: no cover — defensive
            logger.exception("persist_message failed; continuing")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _accumulate_usage(acc: dict[str, int], usage: dict[str, int]) -> None:
    for k, v in usage.items():
        try:
            acc[k] = acc.get(k, 0) + int(v)
        except (TypeError, ValueError):
            continue


def _extract_tool_calls(message: Message) -> list[dict[str, Any]]:
    """Normalise the tool_calls field across payload shapes.

    MiniMax / OpenAI both return ``tool_calls=[{id, type, function: {name, arguments}}]``;
    some proxies flatten the function into the top level. We
    return a canonical list with ``id``, ``name``, ``arguments``
    always present at the top level for convenience.
    """
    raw = message.get("tool_calls") or []
    logger.debug("extract_tool_calls: raw=%s", json.dumps(raw, default=str)[:500])
    out: list[dict[str, Any]] = []
    for i, call in enumerate(raw):
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        name = call.get("name") or fn.get("name", "")
        args = call.get("arguments")
        if args is None:
            args = fn.get("arguments", "")
        # --- Skip ghost tool calls ------------------------------------------
        # When the LLM returns a text block at content index 0 and a
        # tool_use block at index 1, ``_merge_tool_call_delta`` creates a
        # phantom slot at index 0 with empty id/name/arguments.  Although
        # ``_assemble_chunks`` filters these out, we guard here too as a
        # safety net.
        if not name.strip():
            logger.warning("extract_tool_calls: skipping ghost entry at index %d (no name)", i)
            continue
        # Ensure every tool call has a non-empty, unique ID.
        # Anthropic API requires ``toolu_`` prefix; using the same
        # prefix here keeps IDs consistent when ``_convert_messages``
        # processes the original message later in the loop.
        call_id = call.get("id") or ""
        if not call_id.strip():
            call_id = f"toolu_{uuid.uuid4().hex[:24]}"
            # Propagate the generated ID back to the original
            # message so ``_convert_messages`` sees the same value
            # when building the next Anthropic API request.
            call["id"] = call_id
            logger.warning("extract_tool_calls: empty id at index %d, generated %s", i, call_id)
        out.append(
            {
                "id": call_id,
                "type": call.get("type", "function"),
                "name": name,
                "arguments": args,
            }
        )
    logger.debug("extract_tool_calls: result=%s", json.dumps(out, default=str)[:500])
    return out


def _tool_message(call: dict[str, Any], result: ToolResult) -> Message:
    """Format a tool result as the ``tool`` role message OpenAI expects."""
    payload = json.dumps(result.to_dict(), ensure_ascii=False, default=str)
    tc_id = call.get("id") or ""
    if not tc_id.strip():
        tc_id = f"toolu_{uuid.uuid4().hex[:24]}"
    return {
        "role": "tool",
        "tool_call_id": tc_id,
        "name": call.get("name", ""),
        "content": payload,
    }


def _assemble_chunks(chunks: Sequence[StreamChunk], *, model: str) -> LLMResponse:
    """Mirror of :func:`minimax_code.agent.llm._assemble` for the local path."""
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    finish_reason = "stop"
    usage: dict[str, int] = {}
    for c in chunks:
        if c.delta:
            text_parts.append(c.delta)
        if c.usage:
            usage = c.usage
        if c.finish_reason:
            finish_reason = c.finish_reason
        for delta in c.tool_call_deltas:
            _merge_tool_call_delta(tool_calls, delta)
    # --- Ghost-slot filter ---------------------------------------------------
    # ``_merge_tool_call_delta`` pads the accumulator so that
    # ``acc[index]`` is always valid (it uses ``while len(acc) <= index``).
    # When Anthropic returns a text block at index 0 followed by a
    # tool_use block at index 1, the pad creates a phantom entry at
    # index 0 with empty id/name/arguments.  Such entries must be
    # stripped out before they reach ``_extract_tool_calls``.
    tool_calls = [
        tc for tc in tool_calls
        if (tc.get("id") or "").strip()
        or ((tc.get("function") or {}).get("name") or "").strip()
    ]
    message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts)}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return LLMResponse(
        message=message,
        usage=usage,
        finish_reason=finish_reason,
        model=model,
    )


def _merge_tool_call_delta(acc: list[dict[str, Any]], delta: dict[str, Any]) -> None:
    try:
        index = int(delta.get("index", 0))
    except (TypeError, ValueError):
        index = 0
    while len(acc) <= index:
        acc.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
    target = acc[index]
    if delta.get("id"):
        target["id"] = delta["id"]
    if delta.get("type"):
        target["type"] = delta["type"]
    fn_delta = delta.get("function") or {}
    if isinstance(fn_delta.get("name"), str) and fn_delta["name"]:
        target["function"]["name"] = target["function"].get("name", "") + fn_delta["name"]
    if isinstance(fn_delta.get("arguments"), str):
        target["function"]["arguments"] = target["function"].get("arguments", "") + fn_delta["arguments"]


def _sanitize_args(
    args: dict[str, Any],
    sensitive_keys: frozenset[str],
) -> dict[str, Any]:
    """Return a copy of *args* with sensitive values replaced by '***'."""
    if not isinstance(args, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in args.items():
        if isinstance(k, str) and k.lower() in sensitive_keys:
            out[k] = "***"
        elif isinstance(v, dict):
            out[k] = _sanitize_args(v, sensitive_keys)
        else:
            out[k] = v
    return out


def _truncate_result(result: ToolResult, max_bytes: int) -> ToolResult:
    """Cap a tool result's serialized size.

    The cap is applied to the *serialized* ``output`` field; if
    the cap is exceeded we return a shallow result with a
    truncation notice rather than the full payload. We do not
    raise because the agent loop treats tool failures as data
    the model can react to.
    """
    if max_bytes <= 0:
        return result
    try:
        encoded = json.dumps(result.output, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return result
    if len(encoded) <= max_bytes:
        return result
    truncated_text = encoded[:max_bytes] + f"\n…(truncated, {len(encoded) - max_bytes} bytes omitted)"
    new_output: Any
    if isinstance(result.output, str):
        new_output = truncated_text
    else:
        new_output = {"_truncated": True, "preview": truncated_text, "original_bytes": len(encoded)}
    return ToolResult(
        success=result.success,
        output=new_output,
        error=result.error,
        metadata={**result.metadata, "truncated": True, "original_bytes": len(encoded)},
    )


__all__ = [
    "AgentConfig",
    "AgentCore",
    "AgentRunResult",
    "ChunkCallback",
    "StatusCallback",
    "ToolCallCallback",
    "ToolResultCallback",
    "UsageCallback",
]
