"""Tests for ``computer_hub_sdk.harness_builder`` (R167, harness.rs leaf 3).

Covers the :class:`ToolHarnessBuilder` setter layer (15 fluent setters +
defaults + chaining). :meth:`build` lands with the ``ToolHarness`` actor
in a later leaf and is not exercised here.

Setters that only store their argument are exercised with a generic
``_Marker`` sentinel (Python does not enforce Protocol shape at runtime,
so the storage contract is what matters). ``local_tool`` delegates to
:class:`LocalRegistry.register` and is exercised with a typed ``_FakeTool``.
"""

from __future__ import annotations

from typing import Any

from minimax_code.computer_hub_sdk.harness import LocalRegistry
from minimax_code.computer_hub_sdk.harness_builder import ToolHarnessBuilder
from minimax_code.tool_protocol.ids import SessionId, ToolId


# ===========================================================================
# Stubs.
# ===========================================================================
class _Marker:
    """Arbitrary sentinel for setters that only store their argument."""


class _FakeTool:
    """Typed ``Tool`` stub; ``local_tool`` registers it via LocalRegistry."""

    def __init__(self, name: str) -> None:
        self._name = name

    def id(self) -> ToolId:
        return ToolId(self._name)

    def description(self, ctx: Any) -> Any:
        raise NotImplementedError

    def capabilities(self) -> Any:
        raise NotImplementedError

    def should_list(self, ctx: Any) -> bool:
        return True

    async def execute(self, ctx: Any, args: Any) -> Any:
        raise NotImplementedError


def _reconnect_cb(event: Any) -> None:
    """Stand-in satisfying the ReconnectCallback shape."""


def _trace_provider() -> str | None:
    """Stand-in satisfying the TraceContextProvider () -> str | None shape."""
    return None


# ===========================================================================
# Defaults (Rust #[derive(Default)]).
# ===========================================================================
def test_builder_defaults_match_rust_default():
    """All 13 fields reproduce Rust Default (None / False / empty registry)."""
    builder = ToolHarnessBuilder()
    assert builder._pool is None
    assert builder._url is None
    assert builder._auth is None
    assert builder._session is None
    assert isinstance(builder._local_registry, LocalRegistry)
    assert builder._default_extensions is None
    assert builder._trace_context_provider is None
    assert builder._on_reconnect is None
    assert builder._sampler is None
    assert builder._alpha_test_key is None
    assert builder._allow_insecure_ws is False
    assert builder._resume is False
    assert builder._last_seq is None


def test_builder_local_registry_default_is_empty_and_per_instance():
    """The default registry is empty and each builder gets its own."""
    a = ToolHarnessBuilder()
    b = ToolHarnessBuilder()
    assert len(a._local_registry) == 0
    assert a._local_registry is not b._local_registry


# ===========================================================================
# Connection / auth setters.
# ===========================================================================
def test_builder_pool_setter():
    pool = _Marker()
    builder = ToolHarnessBuilder().pool(pool)  # type: ignore[arg-type]
    assert builder._pool is pool


def test_builder_url_setter():
    builder = ToolHarnessBuilder().url("wss://hub.example.com")
    assert builder._url == "wss://hub.example.com"


def test_builder_auth_setter_stores_credential():
    cred = _Marker()
    builder = ToolHarnessBuilder().auth(cred)  # type: ignore[arg-type]
    assert builder._auth is cred


def test_builder_auth_provider_setter_stores_provider():
    provider = _Marker()
    builder = ToolHarnessBuilder().auth_provider(provider)  # type: ignore[arg-type]
    assert builder._auth is provider


def test_builder_auth_provider_replaces_auth():
    """auth then auth_provider (and vice versa) -- last write wins."""
    cred = _Marker()
    provider = _Marker()
    builder = (
        ToolHarnessBuilder()
        .auth(cred)  # type: ignore[arg-type]
        .auth_provider(provider)  # type: ignore[arg-type]
    )
    assert builder._auth is provider


def test_builder_session_setter():
    sid = SessionId("sess-1")
    builder = ToolHarnessBuilder().session(sid)
    assert builder._session == sid


# ===========================================================================
# In-process tool setters.
# ===========================================================================
def test_builder_local_tool_registers_into_default_registry():
    builder = ToolHarnessBuilder().local_tool(_FakeTool("ns:alpha"))
    assert len(builder._local_registry) == 1
    found = builder._local_registry.find(ToolId("ns:alpha"))
    assert found is not None
    assert found.id() == ToolId("ns:alpha")


def test_builder_local_tool_is_additive():
    builder = (
        ToolHarnessBuilder()
        .local_tool(_FakeTool("ns:alpha"))
        .local_tool(_FakeTool("ns:beta"))
    )
    assert len(builder._local_registry) == 2
    assert builder._local_registry.contains(ToolId("ns:alpha"))
    assert builder._local_registry.contains(ToolId("ns:beta"))


def test_builder_local_registry_setter_replaces_default():
    """local_registry() swaps the whole registry (does not merge)."""
    seeded = LocalRegistry()
    builder = ToolHarnessBuilder().local_registry(seeded)
    assert builder._local_registry is seeded


# ===========================================================================
# Dispatch / tracing / callback setters.
# ===========================================================================
def test_builder_default_extensions_setter():
    ext = _Marker()
    builder = ToolHarnessBuilder().default_extensions(ext)  # type: ignore[arg-type]
    assert builder._default_extensions is ext


def test_builder_trace_context_provider_setter():
    builder = ToolHarnessBuilder().trace_context_provider(_trace_provider)
    assert builder._trace_context_provider is _trace_provider


def test_builder_on_reconnect_setter():
    builder = ToolHarnessBuilder().on_reconnect(_reconnect_cb)
    assert builder._on_reconnect is _reconnect_cb


# ===========================================================================
# Transport / sampling knobs.
# ===========================================================================
def test_builder_sampler_setter():
    builder = ToolHarnessBuilder().sampler("chat")
    assert builder._sampler == "chat"


def test_builder_alpha_test_key_setter():
    builder = ToolHarnessBuilder().alpha_test_key("key-abc")
    assert builder._alpha_test_key == "key-abc"


def test_builder_allow_insecure_ws_setter():
    builder = ToolHarnessBuilder().allow_insecure_ws(True)
    assert builder._allow_insecure_ws is True


def test_builder_resume_setter():
    builder = ToolHarnessBuilder().resume(True)
    assert builder._resume is True


def test_builder_last_seq_setter():
    seq = _Marker()
    builder = ToolHarnessBuilder().last_seq(seq)  # type: ignore[arg-type]
    assert builder._last_seq is seq


# ===========================================================================
# Fluent chaining + repr.
# ===========================================================================
def test_builder_setters_are_fluent_return_self():
    """Every setter returns the same builder instance (chainable)."""
    builder = ToolHarnessBuilder()
    chain = (
        builder.url("wss://hub")
        .sampler("shell")
        .allow_insecure_ws(True)
        .resume(False)
        .session(SessionId("s"))
    )
    assert chain is builder
    assert builder._url == "wss://hub"
    assert builder._sampler == "shell"


def test_builder_repr_carries_url_session_sampler_and_tool_count():
    builder = (
        ToolHarnessBuilder()
        .url("wss://hub.example.com")
        .sampler("chat")
        .session(SessionId("s-1"))
        .local_tool(_FakeTool("ns:alpha"))
    )
    text = repr(builder)
    assert "ToolHarnessBuilder" in text
    assert "wss://hub.example.com" in text
    assert "chat" in text
    assert "local_tools=1" in text
