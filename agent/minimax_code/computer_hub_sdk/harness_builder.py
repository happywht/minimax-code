"""ToolHarnessBuilder setter layer (R167, SDK module harness.rs leaf 3).

Forward-port of grok-build ``xai-computer-hub-sdk/src/harness.rs`` lines
324-458: the ``ToolHarnessBuilder`` struct + its 15 fluent setter methods.
This is the pure configuration-accumulation layer -- every setter stores
its argument and returns ``self`` for chaining. ``build`` (lines 459-542)
is a later leaf: it is a large async method that resolves the pool entry,
refcount-binds the session, opens the server session, and assembles a
``ToolHarnessInner`` (which depends on ``arc_swap`` / ``parking_lot`` /
``ConnectionBorrow`` / ``HubConnection`` live state). The actor + its
``build`` driver land together once ``ToolHarness`` / ``ToolHarnessInner``
(564-1663) ports.

tokio -> asyncio / Rust -> Python adaptations (no behavior change):

* ``#[derive(Default)] pub struct ToolHarnessBuilder { ... }`` -> a plain
  class whose ``__init__`` reproduces ``Default`` (``Option<T>`` fields ->
  ``None``; ``bool`` fields -> ``False``; the non-``Option``
  ``local_registry: LocalRegistry`` -> a fresh ``LocalRegistry()``,
  mirroring ``LocalRegistry::default()``).
* ``pub fn x(mut self, v: T) -> Self`` fluent setters ->
  ``def x(self, v: T) -> Self: self._x = v; return self``. The
  ``mut self`` (move + reassign) is plain attribute assignment in Python;
  the returned ``self`` preserves the chain.
* ``Arc<HubConnectionPool>`` / ``Arc<dyn AuthProvider>`` /
  ``Arc<ReconnectCallback>`` -> the raw value. Python references are
  shared, so the ``Arc`` wrapping is implicit; ``auth`` stores the
  ``AuthCredential`` directly because :class:`AuthCredential` is already
  an :class:`AuthProvider` (structural subtyping), collapsing Rust's
  ``Arc::new(cred) as Arc<dyn AuthProvider>`` upcast.
* ``url: Option<Url>`` -> ``url: str | None``. The builder only stores
  the URL string; parsing/validation happens at ``build`` time (later
  leaf), matching Rust where ``Url`` is parsed at the call site before
  ``.url(url)``.
* ``local_tool<T: Tool + Debug + 'static>`` -> ``local_tool(tool: Tool)``.
  Python has no static generics; the ``Debug + 'static`` bounds have no
  runtime role. The body delegates to
  :meth:`LocalRegistry.register` (R166).
* ``trace_context_provider<F: Fn() -> Option<String>>`` /
  ``on_reconnect<F: Fn(ReconnectEvent)>`` ->
  ``trace_context_provider(provider: TraceContextProvider)`` /
  ``on_reconnect(cb: ReconnectCallback)`` (R165 / R150 type-aliases).
* ``alpha_test_key(impl Into<String>)`` / ``sampler(impl Into<String>)``
  -> plain ``str`` parameters (Python has no ``Into``; callers pass a
  ``str`` directly).

YAGNI boundary: ``build(self) -> Result<ToolHarness, ClientError>`` is
not ported here -- it depends on the unported ``ToolHarnessInner`` /
``ToolHarness`` actor plus live connection state. It lands with that
actor in a later leaf. The 15 setters are independently testable as a
configuration-accumulation surface.
"""

from __future__ import annotations

from typing import Self

from minimax_code.computer_hub_sdk.auth import AuthCredential, AuthProvider
from minimax_code.computer_hub_sdk.connection_types import ReconnectCallback
from minimax_code.computer_hub_sdk.harness import LocalRegistry
from minimax_code.computer_hub_sdk.harness_types import TraceContextProvider
from minimax_code.computer_hub_sdk.pool import HubConnectionPool
from minimax_code.tool_protocol import LastSeq
from minimax_code.tool_protocol.ids import SessionId
from minimax_code.tool_runtime.context import TypedExtensions
from minimax_code.tool_runtime.tool import Tool

__all__ = ["ToolHarnessBuilder"]


class ToolHarnessBuilder:
    """Fluent builder for :class:`ToolHarness` (Rust ``ToolHarnessBuilder``).

    Mirrors ``pub struct ToolHarnessBuilder`` (``#[derive(Default)]``) -- 13
    configuration fields, all defaulted in :meth:`__init__` to reproduce
    ``Default``. The 15 fluent setters store their argument and return
    ``self``. :meth:`build` (Rust lines 459-542) lands with the
    :class:`ToolHarness` actor in a later leaf.

    Field storage uses a leading underscore so the public surface is the
    fluent API only; callers never touch attributes directly (matching
    Rust's private fields + public setters).
    """

    def __init__(self) -> None:
        # Connection pool to attach to. Required at build time.
        self._pool: HubConnectionPool | None = None
        # Server URL string (ws:// / wss://). Required at build time.
        self._url: str | None = None
        # Auth provider (a credential or a custom provider). Required at
        # build time. AuthCredential is itself an AuthProvider, so both
        # .auth() and .auth_provider() feed this slot.
        self._auth: AuthProvider | None = None
        # Session id to bind on the underlying connection.
        self._session: SessionId | None = None
        # In-process tool registry. Non-Option in Rust (Default = empty);
        # additive .local_tool() calls populate it, .local_registry()
        # replaces it wholesale.
        self._local_registry: LocalRegistry = LocalRegistry()
        # Default extensions merged into every ToolCallContext at dispatch.
        self._default_extensions: TypedExtensions | None = None
        # Host-supplied W3C traceparent source () -> str | None.
        self._trace_context_provider: TraceContextProvider | None = None
        # Optional once-per-reconnect callback.
        self._on_reconnect: ReconnectCallback | None = None
        # Sampler label for hub_harness_connect_total ("chat" / "shell").
        # Defaults to "unknown" at build time.
        self._sampler: str | None = None
        # Extra access header attached on every (re)connect.
        self._alpha_test_key: str | None = None
        # Permit plaintext ws:// to non-loopback hosts. Default False.
        self._allow_insecure_ws: bool = False
        # Resume the build-time session.open. Default False. Does not
        # affect the transport auto-reconnect loop (always resume=False).
        self._resume: bool = False
        # Last-seen (connection_id, seq) paired with .resume() for replay
        # dedup.
        self._last_seq: LastSeq | None = None

    def __repr__(self) -> str:
        # Mirrors Rust Debug: surface the identity-bearing config (url +
        # session + sampler) without leaking auth material.
        return (
            f"ToolHarnessBuilder(url={self._url!r}, "
            f"session={self._session!r}, sampler={self._sampler!r}, "
            f"local_tools={len(self._local_registry)})"
        )

    # -- connection / auth ------------------------------------------------

    def pool(self, pool: HubConnectionPool) -> Self:
        """Connection pool to attach to (required)."""
        self._pool = pool
        return self

    def url(self, url: str) -> Self:
        """Server URL string (``ws://`` / ``wss://``); required.

        Rust takes a parsed ``Url``; Python stores the raw string and
        defers parsing to :meth:`build` (later leaf), matching the
        Rust call site where the ``Url`` is parsed before ``.url(url)``.
        """
        self._url = url
        return self

    def auth(self, cred: AuthCredential) -> Self:
        """Auth credential (an :class:`AuthProvider`); stored directly.

        Collapses Rust's ``Arc::new(cred) as Arc<dyn AuthProvider>``
        upcast -- :class:`AuthCredential` is structurally an
        :class:`AuthProvider`, so no wrapper is needed.
        """
        self._auth = cred
        return self

    def auth_provider(self, provider: AuthProvider) -> Self:
        """Custom :class:`AuthProvider`; stored directly."""
        self._auth = provider
        return self

    def session(self, session_id: SessionId) -> Self:
        """Session id to bind on the connection; replaces any prior binding."""
        self._session = session_id
        return self

    # -- in-process tools -------------------------------------------------

    def local_tool(self, tool: Tool) -> Self:
        """Register an in-process tool (additive; Rust ``local_tool<T>``).

        Delegates to :meth:`LocalRegistry.register`. Subsequent calls add
        more tools to the same :class:`LocalRegistry`.
        """
        self._local_registry.register(tool)
        return self

    def local_registry(self, registry: LocalRegistry) -> Self:
        """Replace the in-process tool registry wholesale."""
        self._local_registry = registry
        return self

    # -- dispatch / tracing / callbacks -----------------------------------

    def default_extensions(self, extensions: TypedExtensions) -> Self:
        """Default extensions merged into every ``ToolCallContext``."""
        self._default_extensions = extensions
        return self

    def trace_context_provider(self, provider: TraceContextProvider) -> Self:
        """Host W3C traceparent source (``() -> str | None``)."""
        self._trace_context_provider = provider
        return self

    def on_reconnect(self, cb: ReconnectCallback) -> Self:
        """Callback fired once per successful reconnect cycle."""
        self._on_reconnect = cb
        return self

    # -- transport / sampling knobs ---------------------------------------

    def sampler(self, sampler: str) -> Self:
        """Sampler label for the ``hub_harness_connect_total`` metric."""
        self._sampler = sampler
        return self

    def alpha_test_key(self, key: str) -> Self:
        """Extra access header attached on every (re)connect."""
        self._alpha_test_key = key
        return self

    def allow_insecure_ws(self, allow: bool) -> Self:
        """Permit plaintext ``ws://`` to a non-loopback host (default False).

        Only enable when the transport is otherwise secured (private
        network or TLS-terminating proxy) -- the bearer would otherwise
        cross the wire in cleartext.
        """
        self._allow_insecure_ws = allow
        return self

    def resume(self, resume: bool) -> Self:
        """Resume the build-time ``session.open`` (default False).

        Does not affect the transport auto-reconnect loop, which always
        uses ``resume: false``.
        """
        self._resume = resume
        return self

    def last_seq(self, last_seq: LastSeq) -> Self:
        """Last-seen ``(connection_id, seq)`` paired with :meth:`resume`."""
        self._last_seq = last_seq
        return self
