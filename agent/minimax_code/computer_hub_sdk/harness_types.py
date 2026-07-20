"""Harness type-aliases + pure-data leaves (R165, SDK module harness.rs leaf 1).

Forward-port of grok-build ``xai-computer-hub-sdk/src/harness.rs`` lines
60-100 + 544-554: the dependency-free vocabulary the harness actor
(``ToolHarness`` / ``ToolHarnessBuilder`` / ``LocalRegistry``) builds on --
two well-known-kind constants, three callback type-aliases, the
``CancelOnDrop`` opt-in flag newtype, and the ``SessionBindReport`` typed
bind-contract report.

The actor itself -- ``LocalRegistry``/``LocalRegistryInner`` (lines
119-289), ``DynToolAdapter`` (289-323), ``ToolHarnessBuilder`` (324-542),
``ToolHarness``/``ToolHarnessInner`` (564-1663), the lazy/eager
deferred-bind state machine (568-625), ``ObservedToolStream``/
``EmissionState`` (1748+), and the inbound-hook / permission /
notification helpers (1664+) -- is a later leaf (R166+). This module has
no async / actor dependency, so it ports cleanly as pure data +
type-aliases, mirroring the R150 ``connection_types.py`` split.

tokio -> asyncio / Rust -> Python adaptations (no behavior change):

* ``Arc<dyn Fn() -> Option<String> + Send + Sync>`` ->
  :data:`TraceContextProvider` (a plain callable; the GIL removes the
  ``Send + Sync`` constraint).
* ``Arc<dyn Fn(&Value) -> Option<Vec<ContentBlock>>>`` ->
  :data:`ModelOutputExtractor` (``serde_json::Value`` -> ``Any``; the
  extractor probes the dict shape at runtime).
* ``Arc<dyn Fn(HookFrame)>`` -> :data:`HookRequestHandler`.
* ``pub struct CancelOnDrop(pub bool)`` (tuple struct,
  ``#[derive(Clone, Copy, Debug)]``) -> ``@dataclass(frozen=True)`` with a
  named ``value`` field (bool is immutable; ``frozen`` mirrors the
  ``Clone + Copy`` semantics). Consumers construct ``CancelOnDrop(True)``
  positionally as in Rust.
* ``pub struct SessionBindReport`` (``#[derive(Debug, Clone, Default)]``)
  -> ``@dataclass`` with field defaults reproducing ``Default``
  (``binary_version=None``, ``unserved_tool_ids=[]`` via
  :func:`field` ``(default_factory=list)``, ``resolve_error=None``).

YAGNI boundary: ``extractor_for<T>()`` (lines 91-100) is a generic
``T: ToolOutput + DeserializeOwned`` factory returning a
``Value -> Option<Vec<ContentBlock>>`` closure. Python has no static
generics and no serde ``DeserializeOwned`` equivalent; the function only
gains a consumer once ``LocalRegistry::register_with_model_output`` ports
(a later leaf), so it lands with that consumer rather than as dead code
here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from minimax_code.tool_protocol.frames import HookFrame
from minimax_code.tool_runtime.tool import ContentBlock

__all__ = [
    # Constants.
    "PERMISSION_REQUEST_KIND",
    "PROGRESS_BUFFER",
    # Callback type-aliases.
    "TraceContextProvider",
    "HookRequestHandler",
    "ModelOutputExtractor",
    # Opt-in flag newtype.
    "CancelOnDrop",
    # Typed bind-contract report.
    "SessionBindReport",
]


# ===========================================================================
# Constants (lines 72, 78).
# ===========================================================================
# Well-known HookEvent::Custom kind for a server -> harness permission
# request. Sibling of tool_protocol.turn_hook.TURN_HOOK_KIND.
PERMISSION_REQUEST_KIND = "permission_request"

# Buffer size for the per-call progress channel. Picked to absorb a brief
# consumer pause without blocking the connection actor's inbound dispatch
# loop. A slow stream consumer surfaces as RouteOutcome::ProgressFull and
# the dropped frame is logged.
PROGRESS_BUFFER = 64


# ===========================================================================
# Callback type-aliases (lines 61, 67, 88-89).
# ===========================================================================
# Host-supplied source of the current W3C traceparent; () -> Option<String>.
TraceContextProvider = Callable[[], str | None]

# Host-registered sink for inbound reverse-direction hook requests
# (server -> harness); invoked by the inbox loop with the decoded HookFrame,
# answered via ToolHarness::send_hook_reply. Crate-private in Rust.
HookRequestHandler = Callable[[HookFrame], None]

# Per-tool client-side model-output extractor: &Value -> Option<Vec<ContentBlock>>.
# Captured at registration time (register_with_model_output) and applied to
# each call result to surface structured model output.
ModelOutputExtractor = Callable[[Any], list[ContentBlock] | None]


# ===========================================================================
# Opt-in per-call flag (lines 85-86).
# ===========================================================================
@dataclass(frozen=True)
class CancelOnDrop:
    """Opt-in per-call flag (a ``ToolCallContext`` extension).

    When ``value`` is ``True``, the remote call's ``ToolStream`` emits one
    best-effort call-scoped cancel hook on drop so the workspace
    hard-cancels the in-flight call. Absent or ``False`` (the default) ->
    drop emits nothing. No effect on local-dispatch calls. Mirrors
    ``pub struct CancelOnDrop(pub bool)`` (``#[derive(Clone, Copy, Debug)]``)
    as a frozen dataclass -- bool is immutable and ``frozen`` carries the
    ``Clone + Copy`` semantics (hashable, value-equal).
    """

    value: bool


# ===========================================================================
# Typed bind-contract report (lines 544-554).
# ===========================================================================
@dataclass
class SessionBindReport:
    """Typed bind-contract report from a ``session.bind`` response.

    Mirrors ``SessionBindReport`` (``#[derive(Debug, Clone, Default)]``) --
    three optional fields plus one ``Vec<String>``, all defaulted to
    reproduce ``Default``: ``binary_version=None``,
    ``unserved_tool_ids=[]`` (via :func:`field` ``default_factory=list``),
    ``resolve_error=None``.
    """

    binary_version: str | None = None
    unserved_tool_ids: list[str] = field(default_factory=list)
    resolve_error: str | None = None
