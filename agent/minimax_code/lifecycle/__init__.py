"""Lifecycle contributor framework (R25).

Ports grok-build's ``xai-agent-lifecycle`` crate: the four contributor
families (turn lifecycle, session lifecycle, turn input, command) plus
the frozen registry that holds them.

Pure "install-time capability injection" — contributors carry data in,
the host owns the run loop. R25 wires only the turn-lifecycle hooks
into :class:`AgentCore.run`; session-idle / turn-input / command host
dispatch is deferred (grok itself never connected those, so there is
no mature precedent to port).
"""

from __future__ import annotations

from .contributors import (
    CommandContributor,
    SessionLifecycleContributor,
    TurnInputContributor,
    TurnLifecycleContributor,
)
from .registry import ExtensionRegistry, ExtensionRegistryBuilder
from .types import (
    CommandActed,
    CommandAction,
    CommandInvocation,
    CommandRewrite,
    CommandSpec,
    SessionIdleInput,
    TurnAbortInput,
    TurnAbortReason,
    TurnDoneInput,
    TurnErrorInput,
    TurnInputContext,
    TurnInputFragment,
    TurnStartInput,
)

__all__ = [
    # contributors
    "TurnLifecycleContributor",
    "SessionLifecycleContributor",
    "TurnInputContributor",
    "CommandContributor",
    # registry
    "ExtensionRegistry",
    "ExtensionRegistryBuilder",
    # types
    "TurnAbortReason",
    "TurnStartInput",
    "TurnDoneInput",
    "TurnAbortInput",
    "TurnErrorInput",
    "SessionIdleInput",
    "TurnInputContext",
    "TurnInputFragment",
    "CommandSpec",
    "CommandInvocation",
    "CommandRewrite",
    "CommandActed",
    "CommandAction",
]
