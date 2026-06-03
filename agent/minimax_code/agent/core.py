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
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .llm import LLMError, LLMResponse, MiniMaxClient, StreamChunk
from .prompts import build_system_prompt
from .tools import ToolRegistry, ToolResult, get_default_registry

logger = logging.getLogger(__name__)


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
    ) -> None:
        self.llm = llm or MiniMaxClient()
        self.registry = registry or get_default_registry()
        self.config = config or AgentConfig()
        self._history_provider = history_provider
        self._persist_message = persist_message
        self._cancel = asyncio.Event()
        self._permission_store = permission_store
        self._permission_gater = permission_gater
        self.on_chunk: ChunkCallback | None = None
        self.on_tool_call: ToolCallCallback | None = None
        self.on_tool_result: ToolResultCallback | None = None
        self.on_status: StatusCallback | None = None
        self.on_usage: UsageCallback | None = None

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

        history: list[Message] = []
        if self._history_provider is not None:
            history = list(await self._history_provider(session_id) or [])

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

        for iteration in range(self.config.max_iterations):
            iterations = iteration + 1
            if self.cancelled:
                cancelled = True
                break

            await self._emit_status("thinking", {"iteration": iterations})

            try:
                response = await self._stream_turn(messages)
            except LLMError as exc:
                await self._emit_status("error", {"iteration": iterations, "error": str(exc)})
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
            for call in tool_calls:
                if self.cancelled:
                    cancelled = True
                    break
                result = await self._dispatch_tool(call)
                tool_calls_log.append(call)
                tool_results_log.append(result)
                tool_msg = _tool_message(call, result)
                messages.append(tool_msg)
                tool_messages.append(tool_msg)
                await self._maybe_persist(session_id, tool_msg)
            if cancelled:
                break
            # Loop back to call the LLM with the tool messages.
        else:
            # Loop exhausted without a final answer.
            truncated = True
            await self._emit_status("max_iterations", {"iterations": self.config.max_iterations})
            final_text = (
                (final_message.get("content") if final_message else "")
                or "I hit the iteration cap before producing a final answer."
            )

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

    async def _stream_turn(self, messages: list[Message]) -> LLMResponse:
        """Stream one LLM call and stitch the chunks into a response.

        The streaming path is used uniformly — :meth:`MiniMaxClient.chat`
        is just ``stream_chat`` with all chunks buffered. The
        ``on_chunk`` callback fires after every chunk so the
        frontend sees the text appearing live.
        """
        tools_payload = self.registry.to_llm_functions()
        chunks: list[StreamChunk] = []
        async for chunk in self.llm.stream_chat(
            messages,
            model=self.config.model,
            tools=tools_payload or None,
            tool_choice="auto" if tools_payload else None,
            temperature=self.config.temperature,
        ):
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
        """Execute one tool call and emit status / result events.

        Permission gating
        -----------------

        Before invoking the tool, the dispatcher consults
        :attr:`_permission_store` (if set) for a matching rule. The
        resolved action drives three branches:

        * ``"allow"`` — proceed.
        * ``"deny"`` — return a failed :class:`ToolResult` without
          running the tool.
        * ``"ask"`` (or no rule when a gater is wired) — block on
          :attr:`_permission_gater.request_consent`, which emits
          ``permission.request`` and waits for ``permission.resolve``.
        * no rule, no gater — proceed (the default-allow path
          preserves the behaviour every non-opted-in test sees).
        """
        name = call.get("name") or (call.get("function") or {}).get("name", "")
        raw_args = call.get("arguments")
        if raw_args is None and isinstance(call.get("function"), dict):
            raw_args = call["function"].get("arguments")
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError as exc:
                err = ToolResult.fail(f"tool '{name}' got malformed JSON args: {exc}")
                await self._maybe_emit_tool_call(call, err)
                return err
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            args = {}

        tool_call_id = call.get("id") or f"call_{uuid.uuid4().hex[:8]}"
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
            return denied
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
                return denied

        await self._maybe_emit_tool_call(call_log, None)
        await self._emit_status("tool_running", {"tool": name})

        try:
            result = await asyncio.wait_for(
                self.registry.dispatch(name, args),
                timeout=self.config.tool_timeout,
            )
        except TimeoutError:
            result = ToolResult.fail(
                f"tool '{name}' exceeded {self.config.tool_timeout:.0f}s timeout"
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("tool %s crashed", name)
            result = ToolResult.fail(f"{type(exc).__name__}: {exc}")

        # Truncate pathological output to keep the context window sane.
        result = _truncate_result(result, self.config.max_tool_output_bytes)

        await self._maybe_emit_tool_result(call_log, result)
        return result

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
    out: list[dict[str, Any]] = []
    for call in raw:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        name = call.get("name") or fn.get("name", "")
        args = call.get("arguments")
        if args is None:
            args = fn.get("arguments", "")
        out.append(
            {
                "id": call.get("id", ""),
                "type": call.get("type", "function"),
                "name": name,
                "arguments": args,
            }
        )
    return out


def _tool_message(call: dict[str, Any], result: ToolResult) -> Message:
    """Format a tool result as the ``tool`` role message OpenAI expects."""
    payload = json.dumps(result.to_dict(), ensure_ascii=False, default=str)
    return {
        "role": "tool",
        "tool_call_id": call.get("id", ""),
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
