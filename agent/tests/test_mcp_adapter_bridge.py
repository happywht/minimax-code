"""Contract tests for ``mcp_adapter.bridge`` McpBridgeConfig (R181).

R181 ports grok-build's ``xai-computer-hub-mcp-adapter/src/bridge.rs`` type
layer's first leaf -- the configuration value object. These tests pin four
invariants the Rust ``#[derive(Debug, Clone)] pub struct McpBridgeConfig``
guarantees:

1. **Frozen value object** -- a ``@dataclass(frozen=True)`` so it is immutable
   and hashable (the Python ``Clone`` equivalent for config shared by reference
   into the bridge actor).
2. **Field shape** -- exactly ``session_id`` (a
   :class:`~minimax_code.tool_protocol.ids.SessionId`) and ``namespace`` (an
   ``Option<String>`` -> ``str | None``), in Rust declaration order.
3. **Value semantics** -- equal field values are equal; distinct
   ``SessionId`` values are distinct; the object is hashable.
4. **Debug repr** -- the dataclass auto ``__repr__`` carries the class name and
   the field values (the Rust ``Debug`` derive).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass

import pytest

from minimax_code.mcp_adapter import McpBridgeConfig
from minimax_code.tool_protocol.ids import SessionId

# ---------------------------------------------------------------------------
# Frozen value object
# ---------------------------------------------------------------------------


def test_config_is_a_frozen_dataclass() -> None:
    """``#[derive(Debug, Clone)]`` -> ``@dataclass(frozen=True)``."""
    assert is_dataclass(McpBridgeConfig)
    assert McpBridgeConfig.__dataclass_params__.frozen is True


def test_config_field_assignment_raises_frozen_instance_error() -> None:
    """Frozen -> accidental reassignment is rejected at runtime."""
    cfg = McpBridgeConfig(session_id=SessionId("s-1"), namespace=None)
    with pytest.raises(FrozenInstanceError):
        cfg.namespace = "x"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Field shape
# ---------------------------------------------------------------------------


def test_config_has_exactly_two_fields_in_rust_declaration_order() -> None:
    """``session_id`` then ``namespace`` -- Rust field order preserved."""
    names = [f.name for f in fields(McpBridgeConfig)]
    assert names == ["session_id", "namespace"]


def test_config_namespace_accepts_str_and_none() -> None:
    """``Option<String>`` -> ``str | None``; both Some and None accepted.

    There is no ``#[serde(default)]`` (the struct is constructed in code, not
    deserialised), so ``namespace`` is required -- a caller passes ``None``
    explicitly when no namespace is wanted.
    """
    sid = SessionId("s-1")
    assert McpBridgeConfig(session_id=sid, namespace="my-server").namespace == "my-server"
    assert McpBridgeConfig(session_id=sid, namespace=None).namespace is None


def test_config_requires_both_fields_no_defaults() -> None:
    """Neither field has a default -> omitting either is a TypeError."""
    with pytest.raises(TypeError):
        McpBridgeConfig(namespace=None)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        McpBridgeConfig(session_id=SessionId("s-1"))  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Value semantics + Debug repr
# ---------------------------------------------------------------------------


def test_config_equality_and_hash_are_value_based() -> None:
    """``Clone`` + ``PartialEq`` + ``Hash`` -> same fields are equal + hashable."""
    a = McpBridgeConfig(session_id=SessionId("s-1"), namespace="ns")
    b = McpBridgeConfig(session_id=SessionId("s-1"), namespace="ns")
    assert a == b
    assert hash(a) == hash(b)
    # Distinct SessionId value -> distinct config (SessionId is opaque/valued).
    assert a != McpBridgeConfig(session_id=SessionId("s-2"), namespace="ns")
    # Distinct namespace -> distinct config.
    assert a != McpBridgeConfig(session_id=SessionId("s-1"), namespace=None)
    # Hashable -> usable as a dict key (frozen dataclass contract).
    mapping = {a: "ok"}
    assert mapping[b] == "ok"


def test_config_repr_carries_class_name_and_field_values() -> None:
    """``Debug`` -> dataclass auto ``__repr__`` includes class + fields."""
    cfg = McpBridgeConfig(session_id=SessionId("s-1"), namespace=None)
    text = repr(cfg)
    assert "McpBridgeConfig" in text
    assert "s-1" in text
