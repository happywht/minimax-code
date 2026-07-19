"""Sub-agent configuration resolution layer (R22).

Port of grok-build ``xai-grok-subagent-resolution`` — the *pure-logic*
"resolve" stage that turns a sub-agent's stored config (system prompt +
tool allowlist + preferred model) into an :class:`EffectiveRuntimeConfig`
the runtime can consume. The grok crate's ``lib.rs`` manifesto is the
charter here:

    Extract the pure-logic 'resolve' stage [...] into a reusable library.

This module is the MiniMax equivalent of grok's planned
``resolve_subagent_spec()`` composition API (the crate lists it as
future work in ``lib.rs`` lines 17-26). We complete that design intent.

Why pure-logic, why now?
------------------------

Before R22, :meth:`SubAgentRuntime.build` accepted a
:class:`SubAgentConfig` carrying a ``tool_allowlist`` and then *ignored*
it — the core was built against the global registry, so a sub-agent
claiming "read-only" capability still saw every tool. The death of the
allowlist was the single largest dead-code surface in the orchestrator.

R22 introduces a tiny resolution layer that:

1. Computes the effective model (explicit config > parent default).
2. Computes the effective capability mode (allowlist when granted, else
   "all tools" — the parent-inheritance fallback).
3. Produces a :class:`FilteredToolRegistry` read-only view that hides
   disallowed tools, so the agent loop genuinely cannot dispatch them.

The resolution is **side-effect free** — it reads config and an
"available tool names" snapshot, returns a :class:`ResolvedSpec`. No I/O,
no globals, trivially testable. The runtime then maps the spec onto a
real registry.

Priority model (simplified from grok)
-------------------------------------

grok walks a four-dimensional chain per field::

    explicit override > role default > persona default > None (parent)

MiniMax has no personas, no roles, no isolation modes (YAGNI — those are
grok-specific concepts with no MiniMax equivalent yet). So the chain
collapses to::

    explicit config field > parent default

* model: ``config.model`` if set, else the parent's model
  (:func:`~minimax_code.models.default_model` by default — ``MiniMax-M3``,
  wired in R49 so the resolver shares the default-model vocabulary).
* capability: ``ALLOWLIST`` when ``config.tool_allowlist`` is non-empty,
  else ``ALL`` (inherit every tool the parent exposes).

When the allowlist names a tool the parent doesn't actually have, that
name is **silently dropped** (fail-soft for typos / version skew). This
mirrors grok's non-fatal misconfiguration policy — a typo in an
allowlist must not abort the spawn.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from ..models import default_model

if TYPE_CHECKING:  # pragma: no cover — only for type hints
    from .subagent import SubAgentConfig


# ---------------------------------------------------------------------------
# Capability mode
# ---------------------------------------------------------------------------


class CapabilityMode(Enum):
    """How a sub-agent's tool surface relates to its parent's registry.

    Mirrors grok's ``SubagentCapabilityMode`` (e.g. ``ReadOnly`` /
    ``All``), collapsed to the two modes MiniMax actually needs today:

    * ``ALL`` — the sub-agent inherits every tool the parent exposes.
      This is the *parent-inheritance* fallback: no allowlist was set,
      so the sub-agent is trusted with the full surface.
    * ``ALLOWLIST`` — the sub-agent may only call the tools named in
      its config's ``tool_allowlist``. Everything else is hidden by the
      :class:`FilteredToolRegistry` view.
    """

    ALL = "all"
    ALLOWLIST = "allowlist"


# ---------------------------------------------------------------------------
# Resolved spec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedSpec:
    """The fully-resolved runtime configuration for one sub-agent spawn.

    Attributes
    ----------
    model:
        The effective model id (``config.model`` or the parent default).
    capability_mode:
        :attr:`CapabilityMode.ALL` or :attr:`CapabilityMode.ALLOWLIST`.
    allowed_tools:
        The concrete list of tool names the sub-agent may call. For
        ``ALL`` this is every name in the parent snapshot; for
        ``ALLOWLIST`` it is the allowlist intersected with what the
        parent actually exposes (typos / unknown names dropped).
    """

    model: str
    capability_mode: CapabilityMode
    allowed_tools: list[str]

    @property
    def is_restricted(self) -> bool:
        """``True`` when the spec hides any parent tool (ALLOWLIST mode)."""
        return self.capability_mode is CapabilityMode.ALLOWLIST


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_subagent_spec(
    config: SubAgentConfig,
    *,
    available_tool_names: Iterable[str],
    parent_model: str = default_model(),  # R49: was "MiniMax-M3" literal, now from vocabulary
) -> ResolvedSpec:
    """Resolve a :class:`SubAgentConfig` into a :class:`ResolvedSpec`.

    Pure function — no I/O, no globals. The caller supplies the snapshot
    of tool names the parent registry exposes (so the resolver can both
    populate ``ALL`` mode and intersect ``ALLOWLIST`` mode without
    holding a registry reference itself).

    Parameters
    ----------
    config:
        The sub-agent's stored config. Only ``model`` and
        ``tool_allowlist`` are read here — ``system_prompt`` is applied
        by the runtime, not the resolver.
    available_tool_names:
        Names of every tool the parent registry currently exposes.
        Snapshotted once by the caller; the resolver never reaches back
        into the live registry.
    parent_model:
        Model id to inherit when ``config.model`` is unset. Defaults to
        :func:`~minimax_code.models.default_model` (``MiniMax-M3``, the
        default-model vocabulary's flagship — wired in R49 for single-source).

    Returns
    -------
    ResolvedSpec
        The effective model + capability mode + concrete allowed-tools
        list. See :class:`ResolvedSpec` for the priority model.
    """

    # model: explicit config > parent default
    model = config.model or parent_model

    # available set, de-duplicated, order-preserving
    seen: set[str] = set()
    available: list[str] = []
    for name in available_tool_names:
        if name not in seen:
            seen.add(name)
            available.append(name)

    allowlist = config.tool_allowlist
    if allowlist:
        # ALLOWLIST mode: intersect with available, drop unknown names.
        # Order follows the allowlist (caller intent), not the registry.
        available_set = set(available)
        allowed = [n for n in allowlist if n in available_set]
        mode = CapabilityMode.ALLOWLIST
    else:
        # ALL mode: inherit the parent's full surface.
        allowed = list(available)
        mode = CapabilityMode.ALL

    return ResolvedSpec(model=model, capability_mode=mode, allowed_tools=allowed)


# ---------------------------------------------------------------------------
# Filtered registry view
# ---------------------------------------------------------------------------


class FilteredToolRegistry:
    """Read-only, capability-restricted view over a base :class:`ToolRegistry`.

    Hides every tool not in ``allowed`` so an agent loop wired to this
    view literally cannot see or dispatch the restricted tools —
    ``get`` raises :class:`KeyError`, ``has`` returns ``False``, the
    LLM-facing schema (``to_llm_functions``) omits them, and
    :meth:`dispatch` short-circuits to a failure result.

    This is the MiniMax analogue of grok's
    ``SubagentCapabilityMode::filter_tool_config()`` — instead of
    filtering a static config struct we filter the live registry
    object the agent loop holds a reference to.

    Read-only contract
    ------------------

    Mutating operations (``register`` / ``unregister`` / ``clear``)
    raise :class:`NotImplementedError`. This is a *view*, not a copy:
    writes would either silently no-op (confusing) or leak through to
    the parent registry (unsafe). Sub-agent cores never mutate their
    registry, so the read-only surface is complete for the agent loop.

    Duck-typed against :class:`~minimax_code.agent.tools.base.ToolRegistry`
    so it can be passed anywhere the base registry is accepted (the
    agent loop only ever calls the read methods).
    """

    def __init__(self, base: Any, allowed: Iterable[str]) -> None:
        self._base = base
        self._allowed: frozenset[str] = frozenset(allowed)

    @property
    def base(self) -> Any:
        """The underlying registry this view filters (exposed for tests/logs)."""
        return self._base

    @property
    def allowed(self) -> frozenset[str]:
        """The frozen allowlist this view enforces."""
        return self._allowed

    # -- read interface (filtered) -----------------------------------------

    def get(self, name: str) -> Any:
        """Return the tool ``name``, or raise :class:`KeyError`.

        Raises :class:`KeyError` for any tool not in the allowlist —
        the restricted tool is, as far as the caller can tell, simply
        not registered.
        """
        if name not in self._allowed:
            raise KeyError(name)
        return self._base.get(name)

    def has(self, name: str) -> bool:
        """``True`` only when ``name`` is both allowed *and* present in the base."""
        return name in self._allowed and self._base.has(name)

    def list(self) -> list[Any]:
        """The allowed tools, in base-registry order."""
        return [t for t in self._base.list() if t.name in self._allowed]

    def names(self) -> list[str]:
        """Allowed tool names, in base-registry order."""
        return [n for n in self._base.names() if n in self._allowed]

    def to_llm_functions(self) -> list[dict[str, Any]]:
        """LLM function-calling schema for the allowed tools only.

        Filters the base registry's schema output so the model never
        even *sees* a restricted tool's signature — the surest way to
        prevent it from attempting a call.
        """
        return [
            f
            for f in self._base.to_llm_functions()
            if f.get("function", {}).get("name") in self._allowed
        ]

    def to_openai_tools(self) -> list[dict[str, Any]]:
        """Alias for :meth:`to_llm_functions` (identical wire format)."""
        return self.to_llm_functions()

    async def dispatch(self, name: str, args: dict[str, Any]) -> Any:
        """Dispatch ``name`` if allowed; else return a failure :class:`ToolResult`.

        The allowlist gate is enforced here too (not just at ``get``)
        so a caller that hand-constructs a tool-call payload can't
        bypass the view by going straight to ``dispatch``.
        """
        if name not in self._allowed:
            # Lazy import keeps the resolver free of the tools package at
            # module load (the rest of this class never touches ToolResult).
            from ..agent.tools import ToolResult

            return ToolResult.fail(f"tool {name!r} is not in the sub-agent allowlist")
        return await self._base.dispatch(name, args)

    # -- write interface (read-only — raises) ------------------------------

    def register(self, tool: Any) -> Any:
        raise NotImplementedError(
            "FilteredToolRegistry is a read-only view; "
            "register is not supported on a capability-restricted sub-agent registry"
        )

    def unregister(self, name: str) -> None:
        raise NotImplementedError(
            "FilteredToolRegistry is a read-only view; "
            "unregister is not supported on a capability-restricted sub-agent registry"
        )

    def clear(self) -> None:
        raise NotImplementedError(
            "FilteredToolRegistry is a read-only view; "
            "clear is not supported on a capability-restricted sub-agent registry"
        )


__all__ = [
    "CapabilityMode",
    "FilteredToolRegistry",
    "ResolvedSpec",
    "resolve_subagent_spec",
]
