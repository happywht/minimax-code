"""MCP adapter metrics stubs (R184).

Fusion of grok-build's ``xai-computer-hub-mcp-adapter/src/metrics.rs`` --
the default (``cfg(not(feature = "metrics"))``) subset. The Rust crate
ships two ``cfg`` modes:

* ``#[cfg(feature = "metrics")]`` -- a real Prometheus observer/histogram
  wired through ``prometheus-client``. Enabled only when a downstream
  consumer links the ``metrics`` cargo feature.
* ``#[cfg(not(feature = "metrics"))]`` -- **the default** -- three no-op
  free functions with empty bodies, so the adapter compiles and runs
  without a metrics backend.

MiniMax has no Prometheus consumer for the MCP adapter (the platform's
observability flows through :mod:`minimax_code.tracing` /
:class:`~minimax_code.computer_hub_sdk.ObservabilityBridge`, not a
Prometheus scrape endpoint), so the default no-op subset is the
YAGNI-correct port. The three helpers land as module-level functions
whose bodies are empty -- they are call sites the bridge records
against, not data sinks. If a future consumer wires Prometheus, this
module swaps to real observers without touching :mod:`bridge`'s call
sites: the bridge imports the module by name and reaches each helper as
``metrics.<fn>``, mirroring Rust's ``crate::metrics::...`` path, so a
real-observer swap is a one-file edit here (plus the ``metrics`` cargo
feature equivalent -- wiring a scrape endpoint -- which is out of scope
for this leaf).

Why a separate module (not inlined in ``bridge.py``)
----------------------------------------------------

Rust colocates the helpers in ``metrics.rs`` and reaches them via
``crate::metrics::...``; the bridge calls
``crate::metrics::mcp_call_duration_observe(...)`` etc. Mirroring that,
the helpers live in their own module and the bridge imports the module
by name -- the call sites read identically to the Rust source, and a
future real-observer swap is one-file.

Why ``mcp_tools_bridged_set`` lands now (not deferred to R185+)
--------------------------------------------------------------

``metrics.rs`` is a single leaf with three free functions. Only
``mcp_call_duration_observe`` and ``mcp_error`` are reached from
``handle_call`` (R184); ``mcp_tools_bridged_set`` is reached from the
bridge actor's ``connect`` path (R185+). Landing all three in one round
keeps the leaf intact -- splitting a three-function stub across two
rounds would fracture ``metrics.rs``'s unity for no dependency reason
(the function bodies are independent and trivial). R185+ will simply
*call* ``mcp_tools_bridged_set``; the symbol already exists.
"""

from __future__ import annotations

__all__ = [
    "mcp_call_duration_observe",
    "mcp_error",
    "mcp_tools_bridged_set",
]


def mcp_call_duration_observe(_secs: float) -> None:
    """Observe an MCP ``tools/call`` round-trip duration (no-op stub).

    Mirrors ``metrics::mcp_call_duration_observe(_secs: f64)`` under
    ``cfg(not(feature = "metrics"))`` -- the body is empty. The leading
    underscore on ``_secs`` marks it intentionally unused (the real
    observer would record it on a histogram; the stub discards it).
    """


def mcp_error() -> None:
    """Increment the MCP error counter (no-op stub).

    Mirrors ``metrics::mcp_error()`` under
    ``cfg(not(feature = "metrics"))`` -- empty body.
    """


def mcp_tools_bridged_set(_count: int) -> None:
    """Set the count of tools bridged from MCP (no-op stub).

    Mirrors ``metrics::mcp_tools_bridged_set(_count: i64)`` under
    ``cfg(not(feature = "metrics"))`` -- empty body. The bridge actor's
    ``connect`` path (R185+) calls this after discovering tools; it lands
    here now so the stub surface is complete in one round.
    """
