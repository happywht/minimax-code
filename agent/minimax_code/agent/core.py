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
import os
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..compaction import IntraCompactionConfig, IntraCompactionMode, should_compact
from ..hooks import HookManager
from ..lifecycle import (
    ExtensionRegistry,
    TurnAbortInput,
    TurnAbortReason,
    TurnDoneInput,
    TurnErrorInput,
    TurnStartInput,
)
from ..models import default_model
from ..telemetry.tracing import Tracer, get_tracer
from .interjection import (
    FormattedInterjection,
    InterjectionBuffer,
    PendingInterjection,
    drain_formatted,
)
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
from .tools import (
    ASK_USER_MARKER,
    ASK_USER_TIMEOUT_S,
    ToolRegistry,
    ToolResult,
    get_default_registry,
)
from .tools.ask_user import format_answers
from .types import LLMStreamTimeout

if TYPE_CHECKING:
    # Annotation-only import — R55 wires ``AgentConfig.reasoning_effort`` and
    # the ``_stream_turn`` call site through this type. The runtime coercion
    # lives in the transport via ``coerce_effort`` (R54); the field is a
    # passive carrier until then (default ``None`` ⇒ zero wire change).
    from .reasoning import ReasoningEffort

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

AskUserCallback = Callable[[dict[str, Any]], Awaitable[None]]
"""Pushed when the model asks the user a structured clarification
question (``ask_user`` tool). Payload: ``{"request_id", "questions",
"session_id", "timeout_s"}``. The interactive wait happens *after*
this callback returns — hosts wire it to the ``agent.ask_user``
broadcast so the frontend can render an inline question card.
"""

# v1.1.1 — ask_user request routing. request_id → the AgentCore whose
# turn is suspended waiting for that answer. Module-level (not per-core)
# because the ``agent.answer_user`` IPC handler only receives the
# request_id and must find the owning core without knowing the session.
# Entries are added in ``_wait_for_ask_user`` and always removed in its
# ``finally`` (answer / timeout / cancel), so the table never leaks.
_ASK_USER_ROUTES: dict[str, AgentCore] = {}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# v1.1.1 — iteration budget default. The old 12-iteration cap was sized
# for a small context window and silently truncated long agentic tasks;
# with a 1M-token window (and intra-loop compaction managing history
# growth) a safety valve only needs to catch runaway loops, so the
# default is now 200. Operators can tune it per deployment via the
# MINIMAX_MAX_ITERATIONS env var; values are clamped to [1, 10_000] so
# a typo can neither disable the valve (0) nor hang the turn forever.
_DEFAULT_MAX_ITERATIONS = 200
_MAX_ITERATIONS_HARD_CAP = 10_000


def _default_max_iterations() -> int:
    raw = os.environ.get("MINIMAX_MAX_ITERATIONS", "")
    try:
        value = int(raw) if raw.strip() else _DEFAULT_MAX_ITERATIONS
    except ValueError:
        logger.warning(
            "MINIMAX_MAX_ITERATIONS=%r is not an int; using %d",
            raw, _DEFAULT_MAX_ITERATIONS,
        )
        return _DEFAULT_MAX_ITERATIONS
    return max(1, min(value, _MAX_ITERATIONS_HARD_CAP))


@dataclass
class AgentConfig:
    """Tunable knobs for a single :class:`AgentCore` instance."""

    model: str = default_model()  # R50: was "MiniMax-M3" literal, now from vocabulary
    # R55: reasoning-effort control axis (the companion to ``model`` — together
    # they are the two configuration axes of an LLM call: ``model`` picks *who*
    # answers, ``reasoning_effort`` picks *how deeply* it thinks). Accepted in
    # the same permissive form as the client/transport surface
    # (:class:`~minimax_code.agent.reasoning.ReasoningEffort` | wire-token
    # ``str`` | ``None``); the runtime coercion lives in the transport via
    # :func:`~minimax_code.agent.reasoning.coerce_effort` (R54). ``None`` (the
    # default) is pipe-through-only — nothing is emitted on the wire until the
    # per-protocol effort contract settles, so every existing call stays
    # byte-identical to the pre-R55 behaviour.
    reasoning_effort: ReasoningEffort | str | None = None
    # v1.1.1: iteration safety valve (see _default_max_iterations).
    # Tune per deployment via MINIMAX_MAX_ITERATIONS; explicit
    # AgentConfig(max_iterations=...) callers are unaffected.
    max_iterations: int = field(default_factory=_default_max_iterations)
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
    compaction_threshold: float | None = 0.8
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
    # v1.1.0: auto-continue — when a turn ends budget-truncated, the
    # send-message pipeline automatically feeds the fixed continuation
    # prompt back and starts the next block, until the model produces a
    # final answer, the user cancels, or the block cap below is hit.
    # False (the default) keeps the manual "continue" affordance as the
    # only resume path. The core itself never reads this knob — the
    # loop lives in the IPC layer (builtins) so a block boundary stays
    # a real ``run()`` return for hooks / persistence / streaming.
    auto_continue: bool = False
    # Hard cap on blocks per send-message when auto-continue is on
    # (block 1 + N auto-continuations). Guards against a model that
    # converges never; the trailing truncated block still surfaces the
    # manual continue button as the escape hatch.
    auto_continue_max_blocks: int = 5


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
    # v1.1.0: how many intra-loop compactions ran this turn (0 when the
    # turn never crossed the threshold). Surfaces in final metadata so
    # the UI / telemetry can show "compacted N×" per message.
    compactions: int = 0


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
        self.on_ask_user: AskUserCallback | None = None
        # v1.1.1 — in-flight ask_user requests owned by this core.
        # request_id → Future the suspended ``_wait_for_ask_user`` is
        # awaiting; resolved by ``resolve_ask_user`` (agent.answer_user)
        # or woken with ``None`` on cancel so the turn aborts cleanly.
        self._pending_ask_user: dict[str, asyncio.Future[list[Any] | None]] = {}
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
        # R24 — mid-turn user interjection buffer (fuse grok
        # xai-interjection-core). Producers (IPC handlers, a future watchdog)
        # push PendingInterjection entries via queue_interjection(); the run
        # loop drains them at the safe point between tool turns (see run()),
        # framing each as a synthetic user message the model sees next.
        self._interjection_buffer: InterjectionBuffer = InterjectionBuffer()
        # R25 — lifecycle contributor registry (fuse grok
        # xai-agent-lifecycle). Optional; ``None`` ⇒ no contributors and
        # every ``_fire_turn_*`` short-circuits. Built once at host startup
        # via ExtensionRegistryBuilder and held for the process lifetime —
        # contributors carry data in, never take over the run loop.
        self.lifecycle: ExtensionRegistry | None = None
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
        # v1.1.1 — wake every suspended ask_user wait with ``None`` so
        # the tool result reports cancellation instead of hanging until
        # the 600s timeout. The route entries are dropped by each
        # waiter's ``finally`` as it wakes.
        for request_id, future in list(self._pending_ask_user.items()):
            if not future.done():
                future.set_result(None)
                logger.info("ask_user %s cancelled with the turn", request_id)

    def reset_cancel(self) -> None:
        self._cancel.clear()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # -- ask_user interactive wait (v1.1.1) ---------------------------

    def resolve_ask_user(self, request_id: str, answers: Any) -> bool:
        """Resolve a pending ask_user future (called by agent.answer_user).

        Returns ``True`` when the request existed and was answered.
        Unknown / already-timed-out ids return ``False`` so the handler
        can surface a precise error to the frontend.
        """
        future = self._pending_ask_user.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(answers)
        return True

    async def _wait_for_ask_user(
        self, questions: list[dict[str, Any]]
    ) -> ToolResult:
        """Emit ``agent.ask_user`` and suspend until the user answers.

        Called from :meth:`_execute_tool_call` when a tool result carries
        the ``ask_user`` pending marker. The wait lives *outside* the
        registry dispatch (which is bounded by ``tool_timeout``) because
        a human may take minutes to reply — bounded here by
        ``ASK_USER_TIMEOUT_S`` instead.

        Outcomes (all map to a ``success=True`` result so the model can
        decide how to proceed — the tool worked; the human is the
        variable): answered → formatted answers; timeout → explicit
        no-reply notice; ``None`` → the turn was cancelled; no callback
        wired → interactive asking unavailable in this context.
        """
        request_id = f"ask_{uuid.uuid4().hex[:12]}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[list[Any] | None] = loop.create_future()
        self._pending_ask_user[request_id] = future
        _ASK_USER_ROUTES[request_id] = self

        if self.on_ask_user is None:
            # No interactive host (e.g. a headless sub-agent core) —
            # clean up and tell the model instead of hanging.
            self._pending_ask_user.pop(request_id, None)
            _ASK_USER_ROUTES.pop(request_id, None)
            return ToolResult.ok(
                output=(
                    "ask_user unavailable: no interactive client is "
                    "attached to this agent run. Decide on your own "
                    "using the codebase, or state your assumption."
                ),
                ask_user_skipped=True,
            )

        try:
            await self.on_ask_user(
                {
                    "request_id": request_id,
                    "session_id": self._current_session_id,
                    "questions": questions,
                    "timeout_s": ASK_USER_TIMEOUT_S,
                }
            )
            answers = await asyncio.wait_for(future, timeout=ASK_USER_TIMEOUT_S)
        except TimeoutError:
            return ToolResult.ok(
                output=(
                    "The user did not answer within "
                    f"{ASK_USER_TIMEOUT_S:.0f}s. Proceed with your best "
                    "judgment and clearly state the assumptions you made."
                ),
                ask_user_timeout=True,
            )
        except Exception:  # noqa: BLE001 — emit callback must not kill the turn
            logger.exception("ask_user emit/wait failed for %s", request_id)
            return ToolResult.fail("ask_user: failed to surface the question")
        finally:
            self._pending_ask_user.pop(request_id, None)
            _ASK_USER_ROUTES.pop(request_id, None)

        if answers is None:
            return ToolResult.ok(
                output="The turn was cancelled before the user answered.",
                ask_user_cancelled=True,
            )
        return ToolResult.ok(
            output=format_answers(questions, answers),
            ask_user_answered=True,
        )

    # -- mid-turn interjection (R24) ----------------------------------

    def queue_interjection(self, text: str, attachments: list[Any] | None = None) -> None:
        """Buffer a mid-turn user message for the next safe drain point.

        Producers (e.g. an IPC handler receiving a message while the agent
        is mid-turn) call this instead of injecting straight into the
        running conversation — the run loop picks the entry up between
        tool turns via :meth:`drain_interjections`.
        """
        self._interjection_buffer.push(
            PendingInterjection(text=text, attachments=list(attachments or []))
        )

    def drain_interjections(
        self, *, sanitize_text: Callable[[str], str] | None = None
    ) -> list[FormattedInterjection]:
        """Drain + frame buffered interjections as synthetic user messages.

        Each drained entry becomes its own user message (never merged),
        wrapped in the canonical mid-turn envelope. ``sanitize_text`` runs
        on the raw text first; ``None`` ⇒ identity (the interjection flows
        through untouched). Returns ``[]`` when the buffer is empty.
        """
        return drain_formatted(
            self._interjection_buffer,
            sanitize_text if sanitize_text is not None else (lambda s: s),
        )

    # -- turn lifecycle hooks (R25) -----------------------------------

    async def _fire_turn_start(self, *, synthetic: bool = False) -> None:
        """Notify every turn-lifecycle contributor a turn is starting."""
        await self._fire_lifecycle(
            "on_turn_start", TurnStartInput(synthetic=synthetic)
        )

    async def _fire_turn_done(self, *, iterations: int) -> None:
        """Notify every contributor the turn finished with a final answer."""
        await self._fire_lifecycle(
            "on_turn_done", TurnDoneInput(iterations=iterations)
        )

    async def _fire_turn_abort(self, *, reason: TurnAbortReason) -> None:
        """Notify every contributor the turn was stopped mid-flight."""
        await self._fire_lifecycle(
            "on_turn_abort", TurnAbortInput(reason=reason)
        )

    async def _fire_turn_error(self, *, message: str) -> None:
        """Notify every contributor the turn raised an exception."""
        await self._fire_lifecycle(
            "on_turn_error", TurnErrorInput(message=message)
        )

    async def _fire_lifecycle(
        self,
        hook: str,
        inp: TurnStartInput | TurnDoneInput | TurnAbortInput | TurnErrorInput,
    ) -> None:
        """Dispatch one turn-lifecycle hook to every contributor, fail-open.

        Mirrors grok's "fire-and-forget across a Vec" — contributors run in
        registration order, and a raising one is logged + skipped, never
        propagated. One bad extension cannot abort a turn (MiniMax's
        fail-open stance; grok propagates in debug). With no registry wired
        this is a no-op, so R25 is opt-in and zero-cost when unused.
        """
        reg = self.lifecycle
        if reg is None:
            return
        for contributor in reg.turn_lifecycle:
            try:
                await getattr(contributor, hook)(inp)
            except Exception:  # noqa: BLE001 — a bad extension must not kill the turn
                logger.exception(
                    "lifecycle %s.%s raised", type(contributor).__name__, hook
                )

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
        # v1.1.0 — intra-loop compaction state. ``last_prompt_tokens`` is
        # the previous iteration's *real* usage from the LLM response
        # (``None`` before the first call — the pre-turn gate above already
        # handled pre-existing history). ``compactions`` counts how many
        # times the in-flight message list was actually shrunk this turn.
        # The policy mirrors the AgentConfig knobs: HISTORY_ONLY mode (the
        # only mode compact_history can execute) and the configured
        # threshold expressed as a percent of the context window.
        last_prompt_tokens: int | None = None
        compactions = 0
        # v1.1.1 — one context-pressure nudge per run. Repeating the note
        # every iteration trains the model to open every reply with an
        # acknowledgement ("收到，立刻收尾…"), and that acknowledgement is
        # persisted — the exact historical-pattern pollution observed on
        # long multi-turn sessions. The iteration safety valve below still
        # fires on every late iteration (it is the final handoff, ≤2
        # occurrences by construction).
        context_nudge_fired = False
        compaction_policy = IntraCompactionConfig(
            enabled=True,
            mode=IntraCompactionMode.HISTORY_ONLY,
            trigger_threshold_percent=int((self.config.compaction_threshold or 0.8) * 100),
        )

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
            # R25 — turn-start lifecycle hook (one fire per turn, before
            # the first LLM call). Mirrors grok's on_turn_start.
            await self._fire_turn_start()
            for iteration in range(self.config.max_iterations):
                iterations = iteration + 1
                if self.cancelled:
                    cancelled = True
                    break

                # v1.1.0 — per-iteration lifecycle hook (notification,
                # fail-open). Observers can watch long-running turns at
                # iteration granularity without per-tool noise.
                if self.hooks is not None:
                    try:
                        await self.hooks.fire_pre_loop_iteration(
                            self._current_session_id, iteration
                        )
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "pre_loop_iteration hooks raised (iter %d)", iteration, exc_info=True,
                        )

                await self._emit_status("thinking", {"iteration": iterations})

                # v1.1.0 — intra-loop compaction: check the previous
                # iteration's real token usage against the context window
                # before the next LLM call. Decision via should_compact
                # (the top-level compaction package's trigger — honours
                # enabled / window / min-steps / threshold gating);
                # execution via compact_history (turn-boundary summarising
                # with keep_recent=4). ``len(compacted) < len(messages)``
                # guards against compact_history's no-op path (all turns
                # recent → returns the list unchanged) so ``compactions``
                # never over-counts.
                compacted_this_iteration = False
                if (
                    last_prompt_tokens is not None
                    and self.config.context_window
                    and should_compact(
                        compaction_policy,
                        last_prompt_tokens,
                        self.config.context_window,
                        iteration,
                    )
                ):
                    from .compaction import compact_history

                    target = int(self.config.context_window * 0.5)
                    compacted = compact_history(
                        messages, max_tokens=target, keep_recent=4
                    )
                    if len(compacted) < len(messages):
                        logger.info(
                            "intra-loop compaction: %d → %d messages "
                            "(usage %d > %d%% of window %d)",
                            len(messages),
                            len(compacted),
                            last_prompt_tokens,
                            compaction_policy.trigger_threshold_percent,
                            self.config.context_window,
                        )
                        messages = compacted
                        compactions += 1
                        compacted_this_iteration = True

                # v1.1.1 — handoff nudge (rebuilt from the v1.1.0
                # convergence nudge). Two triggers, one message shape:
                # (a) context pressure — reported prompt usage is ≥90% of
                #     the window even after compaction has been firing.
                #     One-shot per run (``context_nudge_fired``) and
                #     skipped on the iteration right after a compaction
                #     (``last_prompt_tokens`` still holds the stale
                #     pre-compaction value then);
                # (b) the iteration safety valve — ≤2 iterations remain
                #     after this one (rare since the default rose to 200).
                # The note is ephemeral: appended to the LLM payload only,
                # never merged into ``messages`` nor persisted.
                # Wording contract: the note must NEVER lure the model
                # into faking a final answer. A clean text answer (no
                # tool_calls) ends the run with truncated=False, and
                # auto-continue only resumes truncated runs — the old
                # "produce a final answer now" wording was silently
                # killing continuation. The message is therefore honest
                # about what happens next and mode-aware. It also forbids
                # the model from acknowledging the note in its reply:
                # acknowledgements get persisted, and later turns then
                # imitate them ("收到，立刻收尾…") long after the note
                # itself is gone — the historical-pattern pollution seen
                # in production.
                llm_messages = messages
                remaining = self.config.max_iterations - iteration - 1
                context_pressure = (
                    not context_nudge_fired
                    and not compacted_this_iteration
                    and last_prompt_tokens is not None
                    and self.config.context_window
                    and last_prompt_tokens >= 0.9 * self.config.context_window
                )
                if context_pressure or remaining <= 2:
                    if context_pressure:
                        reason = "the context window is nearly full"
                        context_nudge_fired = True
                    else:
                        reason = (
                            "the iteration budget is almost exhausted: "
                            f"{remaining} iteration(s) will remain after this one"
                        )
                    if getattr(self.config, "auto_continue", False):
                        directive = (
                            "Keep working until the task is truly done — do "
                            "NOT wrap up early and do NOT fake completion. "
                            "When this block's budget runs out, the runtime "
                            "automatically continues with a fresh block "
                            "(history is compacted, nothing is lost)."
                        )
                    else:
                        directive = (
                            "There will be NO automatic continuation after "
                            "this block. Finish the current step only if it "
                            "is cheap, then report honestly: (1) what is "
                            "done, (2) what remains, (3) the exact next "
                            "step. Never present unfinished work as complete."
                        )
                    nudge = (
                        f"[system note] {reason}. {directive} This note is "
                        "informational: do not mention it, acknowledge it, "
                        "or announce wrapping up in your reply — just adjust "
                        "your behaviour and keep the reply about the work."
                    )
                    llm_messages = messages + [{"role": "user", "content": nudge}]

                try:
                    response = await self._call_llm_with_resilience(llm_messages)
                except LLMError as exc:
                    await self._emit_status(
                        "error",
                        {"iteration": iterations, "detail": str(exc)},
                    )
                    # R25 — turn-error lifecycle hook.
                    await self._fire_turn_error(message=str(exc))
                    raise

                _accumulate_usage(usage_total, response.usage)
                await self._maybe_emit_usage(response.usage)
                # v1.1.0: real prompt-side usage feeds the next iteration's
                # compaction check (``or None`` keeps 0/absent as "unknown",
                # which skips the check rather than firing it spuriously).
                last_prompt_tokens = (
                    int(response.usage.get("prompt_tokens") or 0) or None
                )

                # Persist the assistant message that came out of this
                # LLM call (may contain tool_calls).
                assistant_msg = response.message
                messages.append(assistant_msg)
                # v1.1.3 — persist a copy carrying the per-call usage
                # metadata (tokens_in/tokens_out/thinking_count). The
                # metadata lives on the LLMResponse, not on the message
                # dict, and merging it into ``assistant_msg`` itself would
                # leak a non-protocol key into the LLM history sent on the
                # next iteration's API call — so only the persisted copy
                # sees it. Before this, every regular-completion row hit
                # the DB with metadata=NULL / tokens=0, and the context
                # indicator fell back to 0 after any session reload.
                await self._maybe_persist(
                    session_id, {**assistant_msg, "metadata": response.metadata}
                )

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
                    # R25 — turn-done lifecycle hook.
                    await self._fire_turn_done(iterations=iterations)
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
                # R24 — safe drain point between tool turns: flush any
                # mid-turn user interjections so the model sees them on its
                # next loop iteration (mirrors grok's drain_formatted at the
                # post-tool hook). Each drained entry becomes a synthetic
                # user message, never merged.
                for inj in self.drain_interjections():
                    inj_msg = {"role": "user", "content": inj.text}
                    messages.append(inj_msg)
                    await self._maybe_persist(session_id, inj_msg)
                # v1.1.0 — per-iteration lifecycle hook: this iteration's
                # tool batch is done and the loop is about to continue.
                # The final-answer break path skips this — session_end /
                # turn_done cover the terminal iteration.
                if self.hooks is not None:
                    try:
                        await self.hooks.fire_post_loop_iteration(
                            self._current_session_id, iteration
                        )
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "post_loop_iteration hooks raised (iter %d)", iteration,
                            exc_info=True,
                        )
                # Loop back to call the LLM with the tool messages.
            else:
                # Loop exhausted without a final answer.
                truncated = True
                await self._emit_status("max_iterations", {"iterations": self.config.max_iterations})
                # R25 — turn-abort lifecycle hook (iteration limit hit).
                # v1.1.0: dedicated reason — must not share INTERRUPTED
                # with the user-cancel path below, or lifecycle observers
                # cannot distinguish "budget exhausted" from "stop button".
                await self._fire_turn_abort(reason=TurnAbortReason.MAX_ITERATIONS)
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
                        # v1.1.0: how many times the turn compacted before
                        # the budget ran out (0 when it never crossed the
                        # threshold — still emitted so the UI can tell
                        # "compacted N×" from "feature absent").
                        "compactions": compactions,
                    },
                }
                messages.append(final_message)
                await self._maybe_persist(session_id, final_message)
                await self._emit_chunk(final_text, True, final_message["metadata"])

        # R25 — turn-abort lifecycle hook (user cancel). Both in-loop
        # ``break`` paths (pre-LLM and post-tool) set cancelled=True and
        # funnel through here; the done / max_iterations / error paths
        # dispatch their own terminal event, so exactly one of
        # done/abort/error fires per turn.
        if cancelled:
            await self._fire_turn_abort(reason=TurnAbortReason.INTERRUPTED)

        return AgentRunResult(
            final_text=final_text,
            iterations=iterations,
            tool_calls=tool_calls_log,
            tool_results=tool_results_log,
            usage=usage_total,
            cancelled=cancelled,
            truncated=truncated,
            compactions=compactions,
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
            # R55: thread the configured reasoning-effort axis (the companion
            # to ``model``) into the call. The transport coerces it via
            # ``coerce_effort`` (R54) and records it on
            # ``client.last_reasoning_effort`` but emits nothing on the wire
            # yet, so ``None`` (the AgentConfig default) leaves the request
            # byte-identical to the pre-R55 path.
            reasoning_effort=self.config.reasoning_effort,
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
        emitted_call = False
        if action == "ask" and self._permission_gater is not None:
            # Emit before the (possibly long) consent wait so the UI shows
            # the pending call. When consent is granted, control falls
            # through and must NOT emit again at the bottom of this method:
            # a second emit for the same tool_call_id used to create a
            # duplicate run step that stayed "running" forever (the
            # consent-path double-emit bug on exec_* tools).
            await self._maybe_emit_tool_call(call_log, None)
            emitted_call = True
            allowed = await self._permission_gater.request_consent(
                tool=name, args=args, session_id=self._current_session_id or None
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

        if not emitted_call:
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

        # v1.1.1 — ask_user takeover. The tool returned a pending
        # marker; the interactive wait happens here, *outside* the
        # tool_timeout-bounded dispatch (a human may take minutes to
        # reply). The replacement result carries the user's answers so
        # the next LLM iteration — and the emitted tool_result event —
        # see the final payload, never the marker.
        ask_questions = (result.metadata or {}).get(ASK_USER_MARKER)
        if result.success and ask_questions is not None:
            result = await self._wait_for_ask_user(ask_questions)

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
