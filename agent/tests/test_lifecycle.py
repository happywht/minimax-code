"""Tests for the R25 lifecycle contributor framework.

Pins the four contracts ported from grok-build's ``xai-agent-lifecycle``:

* :class:`ExtensionRegistryBuilder` / :class:`ExtensionRegistry` — the
  four-family registry. Families collect independently; tuples are
  frozen at ``build()``; registration order is preserved; the builder
  methods chain.
* Command-name policy — first registrant owns a name; a duplicate
  advert is dropped; :meth:`all_advertised_commands` is de-duped (first
  spec wins) so it agrees with :meth:`command_owner`.
* ABC defaults — every contributor hook has a no-op default so
  registration is opt-in per boundary; ``CommandContributor.handle_command``
  raises ``NotImplementedError`` so a route table is never left dangling.
* :class:`AgentCore` run-loop integration — the host fires exactly one
  terminal event (done / abort / error) per turn, fail-open so a raising
  contributor cannot kill the turn. With ``lifecycle=None`` (the default)
  the run loop is unchanged from before R25.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from minimax_code.agent.core import AgentConfig, AgentCore
from minimax_code.agent.llm import LLMError, StreamChunk
from minimax_code.agent.reliability import RetryPolicy
from minimax_code.agent.tools import Tool, ToolRegistry, ToolResult
from minimax_code.lifecycle import (
    CommandActed,
    CommandAction,
    CommandContributor,
    CommandInvocation,
    CommandRewrite,
    CommandSpec,
    ExtensionRegistry,
    ExtensionRegistryBuilder,
    SessionIdleInput,
    SessionLifecycleContributor,
    TurnAbortInput,
    TurnAbortReason,
    TurnDoneInput,
    TurnErrorInput,
    TurnInputContext,
    TurnInputContributor,
    TurnLifecycleContributor,
    TurnStartInput,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _Recorder(TurnLifecycleContributor):
    """Records every turn-lifecycle call into ``events`` for assertion."""

    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    async def on_turn_start(self, inp: TurnStartInput) -> None:
        self.events.append(("start", inp.synthetic))

    async def on_turn_done(self, inp: TurnDoneInput) -> None:
        self.events.append(("done", inp.iterations))

    async def on_turn_abort(self, inp: TurnAbortInput) -> None:
        self.events.append(("abort", inp.reason.value))

    async def on_turn_error(self, inp: TurnErrorInput) -> None:
        self.events.append(("error", inp.message))


class _Idle(SessionLifecycleContributor):
    """Trivial session-lifecycle contributor placeholder."""


class _NoInput(TurnInputContributor):
    """Trivial turn-input contributor placeholder."""


class _CmdA(CommandContributor):
    def advertised_commands(self) -> list[CommandSpec]:
        return [
            CommandSpec(name="/run", description="A's run"),
            CommandSpec(name="/stop", description="A's stop"),
        ]

    async def handle_command(self, inv: CommandInvocation) -> CommandAction:
        return CommandActed(note=f"A:{inv.name}")


class _CmdB(CommandContributor):
    def advertised_commands(self) -> list[CommandSpec]:
        return [
            CommandSpec(name="/run", description="B duplicate"),  # clashes with A
            CommandSpec(name="/go", description="B's go"),
        ]

    async def handle_command(self, inv: CommandInvocation) -> CommandAction:
        return CommandActed(note=f"B:{inv.name}")


class _ScriptedLLM:
    """Replays fixed chunk-lists in order, one per ``stream_chat`` call."""

    def __init__(self, scripts: list[list[StreamChunk]]) -> None:
        self._scripts = list(scripts)
        self.thinking_count = 0

    async def stream_chat(self, messages: list[dict[str, Any]], **_: Any):
        chunks = self._scripts.pop(0)
        for c in chunks:
            yield c


class _RaisingLLM:
    """Always raises on the first ``__anext__`` — drives the error path."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.thinking_count = 0

    async def stream_chat(self, messages: list[dict[str, Any]], **_: Any):
        raise self._exc
        yield  # pragma: no cover — marks this an async generator


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


def _tool_call(
    name: str, args: dict[str, Any], call_id: str = "call_1"
) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


# ---------------------------------------------------------------------------
# Registry builder — four families, frozen, ordered
# ---------------------------------------------------------------------------


def test_builder_collects_four_families_independently() -> None:
    tl, sl, ti, cc = _Recorder(), _Idle(), _NoInput(), _CmdA()
    reg = (
        ExtensionRegistryBuilder()
        .add_turn_lifecycle(tl)
        .add_session_lifecycle(sl)
        .add_turn_input(ti)
        .add_command(cc)
        .build()
    )
    assert isinstance(reg, ExtensionRegistry)
    assert reg.turn_lifecycle == (tl,)
    assert reg.session_lifecycle == (sl,)
    assert reg.turn_input == (ti,)
    assert reg.commands == (cc,)


def test_empty_builder_builds_empty_registry() -> None:
    reg = ExtensionRegistryBuilder().build()
    assert isinstance(reg, ExtensionRegistry)
    assert reg.turn_lifecycle == ()
    assert reg.session_lifecycle == ()
    assert reg.turn_input == ()
    assert reg.commands == ()
    assert reg.all_advertised_commands() == []
    assert reg.command_owner("/anything") is None


def test_builder_methods_are_chainable() -> None:
    """Each add_* returns the builder so registrations chain."""
    b = ExtensionRegistryBuilder()
    assert b.add_turn_lifecycle(_Recorder()) is b
    assert b.add_session_lifecycle(_Idle()) is b
    assert b.add_turn_input(_NoInput()) is b
    assert b.add_command(_CmdA()) is b


def test_turn_lifecycle_tuple_preserves_registration_order() -> None:
    a, b, c = _Recorder(), _Recorder(), _Recorder()
    reg = (
        ExtensionRegistryBuilder()
        .add_turn_lifecycle(a)
        .add_turn_lifecycle(b)
        .add_turn_lifecycle(c)
        .build()
    )
    assert reg.turn_lifecycle == (a, b, c)


# ---------------------------------------------------------------------------
# Command policy — first registrant wins
# ---------------------------------------------------------------------------


def test_command_first_registrant_owns_each_name() -> None:
    reg = ExtensionRegistryBuilder().add_command(_CmdA()).add_command(_CmdB()).build()
    assert isinstance(reg.command_owner("/run"), _CmdA)
    assert isinstance(reg.command_owner("/stop"), _CmdA)
    assert isinstance(reg.command_owner("/go"), _CmdB)  # B's exclusive name
    assert reg.command_owner("/missing") is None


def test_duplicate_command_spec_is_dropped() -> None:
    """B's ``/run`` clashes with A's; A keeps it, B's duplicate advert is
    dropped — but B itself stays registered (``/go`` still resolves)."""
    reg = ExtensionRegistryBuilder().add_command(_CmdA()).add_command(_CmdB()).build()
    assert isinstance(reg.command_owner("/run"), _CmdA)
    names = [s.name for s in reg.all_advertised_commands()]
    # /run appears exactly once (A's), agreeing with command_owner.
    assert names.count("/run") == 1
    assert "/go" in names
    assert "/stop" in names


def test_all_advertised_commands_aggregates_across_contributors() -> None:
    reg = ExtensionRegistryBuilder().add_command(_CmdA()).add_command(_CmdB()).build()
    specs = reg.all_advertised_commands()
    assert {s.name for s in specs} == {"/run", "/stop", "/go"}


# ---------------------------------------------------------------------------
# Contributor ABC defaults — opt-in per hook
# ---------------------------------------------------------------------------


def test_turn_lifecycle_defaults_are_noop() -> None:
    """An empty subclass inherits no-op defaults for all four hooks —
    registration is opt-in per boundary."""

    class _Empty(TurnLifecycleContributor):
        pass

    e = _Empty()
    asyncio.run(e.on_turn_start(TurnStartInput()))
    asyncio.run(e.on_turn_done(TurnDoneInput()))
    asyncio.run(e.on_turn_abort(TurnAbortInput(TurnAbortReason.INTERRUPTED)))
    asyncio.run(e.on_turn_error(TurnErrorInput("x")))


def test_turn_input_default_returns_empty_list() -> None:
    class _Empty(TurnInputContributor):
        pass

    frags = asyncio.run(
        _Empty().contribute_turn_input(TurnInputContext(turn_id="t1"))
    )
    assert frags == []


def test_session_lifecycle_default_is_noop() -> None:
    class _Empty(SessionLifecycleContributor):
        pass

    asyncio.run(_Empty().on_session_idle(SessionIdleInput()))


def test_command_default_handle_raises_not_implemented() -> None:
    """A contributor that advertises a command must override handle_command;
    the default raises so the host's route table is never left dangling."""

    class _Empty(CommandContributor):
        pass

    with pytest.raises(NotImplementedError):
        asyncio.run(_Empty().handle_command(CommandInvocation(name="/x")))


# ---------------------------------------------------------------------------
# Data types — frozen value objects + CommandAction union
# ---------------------------------------------------------------------------


def test_turn_inputs_are_frozen() -> None:
    start = TurnStartInput(synthetic=True)
    with pytest.raises(AttributeError):
        start.synthetic = False  # type: ignore[misc]
    abort = TurnAbortInput(reason=TurnAbortReason.INTERRUPTED)
    with pytest.raises(AttributeError):
        abort.reason = TurnAbortReason.DISCONNECTED  # type: ignore[misc]


def test_command_action_union_carries_both_variants() -> None:
    rewrite: CommandAction = CommandRewrite(model_text="new text")
    assert isinstance(rewrite, CommandRewrite)
    assert rewrite.model_text == "new text"

    acted: CommandAction = CommandActed(note="did it")
    assert isinstance(acted, CommandActed)
    assert acted.note == "did it"


def test_command_spec_and_invocation_are_frozen() -> None:
    spec = CommandSpec(name="/x", description="d", arg_hint="h")
    with pytest.raises(AttributeError):
        spec.name = "/y"  # type: ignore[misc]
    inv = CommandInvocation(name="/x", args={"a": "1"})
    with pytest.raises(AttributeError):
        inv.name = "/y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# AgentCore run-loop integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_without_registry_is_noop() -> None:
    """lifecycle=None (the default): run behaves exactly as before R25.
    Pins the ``_fire_lifecycle`` early-return when no registry is wired."""
    llm = _ScriptedLLM([[StreamChunk(delta="hi", finish_reason="stop")]])
    core = AgentCore(llm=llm, registry=ToolRegistry(), config=AgentConfig())
    assert core.lifecycle is None  # default
    result = await core.run(session_id="s1", user_message="hi")
    assert result.final_text == "hi"


@pytest.mark.asyncio
async def test_run_dispatches_start_then_done_on_clean_turn() -> None:
    llm = _ScriptedLLM([[StreamChunk(delta="hi", finish_reason="stop")]])
    core = AgentCore(llm=llm, registry=ToolRegistry(), config=AgentConfig())
    rec = _Recorder()
    core.lifecycle = ExtensionRegistryBuilder().add_turn_lifecycle(rec).build()

    result = await core.run(session_id="s1", user_message="hi")

    assert result.final_text == "hi"
    # start fires before the LLM call; done fires on the clean final answer.
    assert rec.events == [("start", False), ("done", 1)]
    # 唯一终态：abort/error 未泄漏。
    assert not any(e[0] in ("abort", "error") for e in rec.events)


@pytest.mark.asyncio
async def test_run_dispatches_abort_on_max_iterations() -> None:
    # LLM always returns an echo tool_call, never a final answer → the loop
    # exhausts max_iterations and lands in the else-branch abort path.
    llm = _ScriptedLLM(
        [
            [
                StreamChunk(
                    tool_call_deltas=[_tool_call("echo", {"text": "a"})],
                    finish_reason="tool_calls",
                )
            ],
            [
                StreamChunk(
                    tool_call_deltas=[_tool_call("echo", {"text": "b"})],
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    reg = ToolRegistry()
    reg.register(_EchoTool())
    core = AgentCore(llm=llm, registry=reg, config=AgentConfig(max_iterations=2))
    rec = _Recorder()
    core.lifecycle = ExtensionRegistryBuilder().add_turn_lifecycle(rec).build()

    result = await core.run(session_id="s1", user_message="loop")

    assert result.truncated is True
    assert result.cancelled is False
    assert rec.events == [("start", False), ("abort", "interrupted")]
    assert not any(e[0] in ("done", "error") for e in rec.events)


@pytest.mark.asyncio
async def test_run_dispatches_abort_on_user_cancel() -> None:
    """A contributor that cancels in ``on_turn_start`` makes the loop break
    on its first-iteration cancel check; the return-path abort hook fires."""
    llm = _ScriptedLLM([[StreamChunk(delta="hi", finish_reason="stop")]])
    core = AgentCore(llm=llm, registry=ToolRegistry(), config=AgentConfig())

    class _CancelOnStart(TurnLifecycleContributor):
        async def on_turn_start(self, inp: TurnStartInput) -> None:
            core.cancel()

    rec = _Recorder()
    core.lifecycle = (
        ExtensionRegistryBuilder()
        .add_turn_lifecycle(rec)
        .add_turn_lifecycle(_CancelOnStart())
        .build()
    )

    result = await core.run(session_id="s1", user_message="hi")

    assert result.cancelled is True
    # recorder saw start (registered before the canceller), then abort.
    assert rec.events == [("start", False), ("abort", "interrupted")]
    assert not any(e[0] in ("done", "error") for e in rec.events)


@pytest.mark.asyncio
async def test_run_dispatches_error_on_llm_failure() -> None:
    llm = _RaisingLLM(LLMError("boom"))
    core = AgentCore(
        llm=llm,
        registry=ToolRegistry(),
        # max_attempts=1 ⇒ no retry/backoff, fails fast on the first raise.
        config=AgentConfig(llm_retry_policy=RetryPolicy(max_attempts=1)),
    )
    rec = _Recorder()
    core.lifecycle = ExtensionRegistryBuilder().add_turn_lifecycle(rec).build()

    with pytest.raises(LLMError, match="boom"):
        await core.run(session_id="s1", user_message="hi")

    assert rec.events == [("start", False), ("error", "boom")]
    assert not any(e[0] in ("done", "abort") for e in rec.events)


@pytest.mark.asyncio
async def test_run_fail_open_when_contributor_raises() -> None:
    """A contributor raising ``on_turn_done`` is logged + skipped; the turn
    still completes normally (MiniMax fail-open stance vs grok's propagate)."""
    llm = _ScriptedLLM([[StreamChunk(delta="hi", finish_reason="stop")]])
    core = AgentCore(llm=llm, registry=ToolRegistry(), config=AgentConfig())

    class _Boom(TurnLifecycleContributor):
        async def on_turn_done(self, inp: TurnDoneInput) -> None:
            raise RuntimeError("contributor exploded")

    rec = _Recorder()
    core.lifecycle = (
        ExtensionRegistryBuilder()
        .add_turn_lifecycle(rec)
        .add_turn_lifecycle(_Boom())
        .build()
    )

    result = await core.run(session_id="s1", user_message="hi")

    # Turn completed normally despite the boomer.
    assert result.final_text == "hi"
    # rec recorded start + done (registered before the boomer, so its own
    # on_turn_done fired before the boom).
    assert rec.events == [("start", False), ("done", 1)]


@pytest.mark.asyncio
async def test_multiple_contributors_dispatched_in_order() -> None:
    """Two recorders both see the same hooks, in registration order."""
    llm = _ScriptedLLM([[StreamChunk(delta="hi", finish_reason="stop")]])
    core = AgentCore(llm=llm, registry=ToolRegistry(), config=AgentConfig())
    rec1, rec2 = _Recorder(), _Recorder()
    core.lifecycle = (
        ExtensionRegistryBuilder()
        .add_turn_lifecycle(rec1)
        .add_turn_lifecycle(rec2)
        .build()
    )

    await core.run(session_id="s1", user_message="hi")

    assert rec1.events == [("start", False), ("done", 1)]
    assert rec2.events == [("start", False), ("done", 1)]
