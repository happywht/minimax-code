"""Barrel reconciliation tests for ``mcp_adapter.__init__`` (R186).

R186 mirrors grok-build ``xai-computer-hub-mcp-adapter/src/lib.rs``'s three
``pub use`` lines at the package root. The crate is a 4-module leaf (types /
transport / bridge / metrics); R179-R185 landed every leaf, and this round pins
the barrel contract. Three invariants:

1. **Coverage** -- the barrel exposes exactly 17 symbols: the 10 ``lib.rs``
   ``pub use`` symbols (the core crate contract) plus 7 Python ergonomics
   extras. The extras are the 3 :class:`McpContent` union variants and the 4
   :class:`McpError` subclasses that Rust exposes only via ``pub mod types``
   (module-path access, ``types::McpTextContent``) but Python re-exports flat
   as construct / raise targets (R179 decision: pydantic union variants and
   exception hierarchies are top-level citizens in Python's flat namespace).
2. **Fidelity** -- each barrel symbol *is* the same object its leaf submodule
   exports (identity via ``is``), so the barrel cannot drift into a shadow or
   a stale re-binding.
3. **YAGNI boundary** -- ``metrics`` (Rust ``pub(crate) mod metrics``) stays
   absent from the barrel: not in ``__all__``, yet still reachable as a
   submodule for crate-internal callers (the Python equivalent of
   ``crate::metrics::...``).

The identity checks are the load-bearing ones: a ``getattr``/count test only
proves *something* is bound to the name; the ``is`` check proves the barrel
honours the same single source the leaf established.
"""

from __future__ import annotations

import minimax_code.mcp_adapter as mcp
from minimax_code.mcp_adapter import (
    bridge,
    metrics,
    transport,
    types,
)

#: The lib.rs ``pub use`` surface (10 symbols) -- the core barrel contract.
#: Mirrors::
#:
#:     pub use bridge::{McpBridge, McpBridgeConfig, McpBridgeHandle, McpToolHandler};
#:     pub use transport::McpTransport;
#:     pub use types::{McpCallResult, McpContent, McpError, McpServerInfo, McpToolDefinition};
#:
#: Asserted as a set so the test fails on *any* drift -- a dropped rename, an
#: accidental extra export, or a missing leaf all surface here.
EXPECTED_PUB_USE: set[str] = {
    # bridge.rs barrel (R181 McpBridgeConfig, R182 translate, R183 handler,
    # R184 handle_call, R185 actor + handle) -- 4 lib.rs pub use symbols.
    "McpBridge",
    "McpBridgeConfig",
    "McpBridgeHandle",
    "McpToolHandler",
    # transport.rs barrel (R180) -- the McpTransport async trait.
    "McpTransport",
    # types.rs barrel (R179) -- 5 lib.rs pub use symbols.
    "McpServerInfo",
    "McpToolDefinition",
    "McpCallResult",
    "McpContent",
    "McpError",
}

#: Python ergonomics extras (7 symbols) beyond the Rust ``lib.rs`` ``pub use``.
#: Rust exposes these via ``pub mod types`` (module-path access); Python
#: re-exports them flat as construct / raise targets. 3 McpContent union
#: variants + 4 McpError subclasses. Documented in ``__init__.py``'s barrel
#: reconciliation note (R186) so the divergence from the Rust surface is
#: intentional and pinned, not accidental drift.
EXPECTED_ERGONOMICS: set[str] = {
    # McpContent union variants (construct targets for the tagged content enum).
    "McpTextContent",
    "McpImageContent",
    "McpResourceContent",
    # McpError subclasses (raise targets for the four thiserror variants).
    "McpTransportError",
    "McpProtocolError",
    "McpTimeoutError",
    "McpDecodeError",
}

#: The complete barrel surface: 10 lib.rs ``pub use`` + 7 Python ergonomics.
EXPECTED_BARREL: set[str] = EXPECTED_PUB_USE | EXPECTED_ERGONOMICS

#: ``lib.rs`` ``pub(crate) mod metrics`` -- crate-private, never re-exported
#: at the barrel. Reachable as a submodule (``mcp_adapter.metrics``) for
#: crate-internal callers, mirroring Rust's ``crate::metrics::...`` path, but
#: deliberately absent from ``__all__`` so the public surface matches the
#: Rust ``pub use`` contract.
YAGNI_SUBMODULE_NOT_EXPORTED: set[str] = {"metrics"}


# ---------------------------------------------------------------------------
# Coverage: the barrel surface is exactly the 17-symbol contract.
# ---------------------------------------------------------------------------

def test_barrel_matches_expected_surface_exactly() -> None:
    """The package __all__ is exactly EXPECTED_BARREL -- no drift."""
    assert set(mcp.__all__) == EXPECTED_BARREL


def test_barrel_has_no_duplicates() -> None:
    """__all__ lists each export once (a duplicate would mask a rename bug)."""
    assert len(mcp.__all__) == len(set(mcp.__all__))


def test_barrel_count_is_17() -> None:
    """Sanity guard against a silent add/drop when EXPECTED_BARREL is edited."""
    assert len(mcp.__all__) == 17
    assert len(EXPECTED_BARREL) == 17


def test_pub_use_subset_is_10() -> None:
    """The lib.rs pub use core is exactly 10 symbols (the crate contract)."""
    assert len(EXPECTED_PUB_USE) == 10
    assert EXPECTED_PUB_USE <= EXPECTED_BARREL


def test_ergonomics_subset_is_7() -> None:
    """The Python ergonomics extras are exactly 7 (beyond the Rust pub use)."""
    assert len(EXPECTED_ERGONOMICS) == 7
    assert EXPECTED_ERGONOMICS <= EXPECTED_BARREL
    # The two halves are disjoint: no symbol is both a pub use core entry and
    # an ergonomics extra (a classification slip would inflate the union).
    assert EXPECTED_PUB_USE.isdisjoint(EXPECTED_ERGONOMICS)


def test_every_export_resolves_on_the_package() -> None:
    """Each __all__ name is getattr-able on the package and is not None."""
    for name in mcp.__all__:
        assert hasattr(mcp, name), f"barrel lists {name!r} but package lacks it"
        assert getattr(mcp, name) is not None, f"{name!r} resolves to None"


# ---------------------------------------------------------------------------
# Fidelity: each barrel symbol *is* its leaf-submodule source (identity).
# One test per lib.rs pub use group + one for the ergonomics extras; the
# group-level identity implies the symbols came through the same import.
# ---------------------------------------------------------------------------

def test_bridge_surface_identity() -> None:
    """lib.rs ``pub use bridge::{4 symbols}`` -- each binds to bridge.py."""
    assert mcp.McpBridge is bridge.McpBridge
    assert mcp.McpBridgeConfig is bridge.McpBridgeConfig
    assert mcp.McpBridgeHandle is bridge.McpBridgeHandle
    assert mcp.McpToolHandler is bridge.McpToolHandler


def test_transport_surface_identity() -> None:
    """lib.rs ``pub use transport::McpTransport`` -- binds to transport.py."""
    assert mcp.McpTransport is transport.McpTransport


def test_types_surface_identity() -> None:
    """lib.rs ``pub use types::{5 symbols}`` -- each binds to types.py."""
    assert mcp.McpServerInfo is types.McpServerInfo
    assert mcp.McpToolDefinition is types.McpToolDefinition
    assert mcp.McpCallResult is types.McpCallResult
    assert mcp.McpContent is types.McpContent
    assert mcp.McpError is types.McpError


def test_types_ergonomics_identity() -> None:
    """The 7 ergonomics extras bind to their types.py sources (not shadows).

    Rust leaves these at module-path access (``types::McpTextContent``); the
    Python barrel re-exports them flat, but each must still be the very same
    object ``types.py`` defines -- otherwise the barrel is a shadow.
    """
    assert mcp.McpTextContent is types.McpTextContent
    assert mcp.McpImageContent is types.McpImageContent
    assert mcp.McpResourceContent is types.McpResourceContent
    assert mcp.McpTransportError is types.McpTransportError
    assert mcp.McpProtocolError is types.McpProtocolError
    assert mcp.McpTimeoutError is types.McpTimeoutError
    assert mcp.McpDecodeError is types.McpDecodeError


# ---------------------------------------------------------------------------
# YAGNI boundary: metrics (pub(crate)) stays out of the barrel.
# ---------------------------------------------------------------------------

def test_yagni_metrics_absent_from_all() -> None:
    """``metrics`` is ``pub(crate)`` in Rust; it must not leak into __all__."""
    leaked = YAGNI_SUBMODULE_NOT_EXPORTED & set(mcp.__all__)
    assert not leaked, f"pub(crate) submodule leaked into barrel: {leaked}"


def test_yagni_metrics_submodule_reachable_but_not_exported() -> None:
    """``metrics`` is crate-private: reachable as a submodule (the Python
    equivalent of ``crate::metrics::...``) but deliberately not re-exported.

    This mirrors Rust's ``pub(crate) mod metrics``: the three helpers are
    call sites the bridge records against (R184 handle_call + R185 actor),
    reached via ``crate::metrics::...`` internally -- never part of the
    public ``pub use`` surface. Python grants the same reachability
    (``mcp_adapter.metrics``) without elevating the submodule to the barrel.
    """
    # Submodule is importable (crate-internal path).
    assert hasattr(metrics, "mcp_error")
    assert hasattr(metrics, "mcp_tools_bridged_set")
    assert hasattr(metrics, "mcp_call_duration_observe")
    # The three helpers are the documented metrics stub surface (R184).
    assert sorted(metrics.__all__) == [
        "mcp_call_duration_observe",
        "mcp_error",
        "mcp_tools_bridged_set",
    ]
    # But the submodule name is not elevated to the package barrel.
    assert "metrics" not in mcp.__all__
