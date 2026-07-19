"""Backend environment presets — fusion of grok's ``xai-grok-env`` (R43).

Endpoint URL defaults, environment selection, and env-var test support for the
MiniMax Code agent. Mirrors grok's ``xai-grok-env`` (lib.rs, 197 lines): "Backend
environment presets for the Grok CLI crate family: endpoint URL defaults and
env-var test support."

Mapping
-------
* ``GrokBuildEndpoints`` (struct of ``&'static str``, ``Debug+Clone+Copy+
  PartialEq+Eq``, no ``Serialize``) → :class:`BuildEndpoints`, a frozen dataclass
  (branch 2 of the payload decision tree — pure value equality, no wire surface,
  same as R40 ``QueueEntryMeta`` / R42 ``Version``). ``&'static str`` → plain
  ``str`` (Python strings are immutable; there is no lifetime to pin).
* ``GrokBuildEnvironment`` (single-variant enum ``Production``, ``Copy+PartialEq+Eq``
  + ``#[default]`` + methods) → :class:`BuildEnvironment`, a ``@unique`` single-
  member ``enum.Enum`` (branch 3 — pure unit enum, same as R41 ``PowerState``).
  ``#[default]`` → the sole member is the implicit default; ``from_flags`` is a
  public-build no-op that always returns it (Dev/Staging are compiled out).
* ``unsafe { std::env::set_var / remove_var }`` (Rust 2024 marks env mutation
  ``unsafe`` because ``std::env`` is process-global) → :func:`os.environ.__setitem__`
  / :func:`os.environ.pop` (Python has no ``unsafe`` marker, but the process-global
  hazard is identical — hence the :data:`_ENV_LOCK` serialising guard).
* ``EnvVarGuard`` (RAII, ``#[cfg(test)]``, ``Drop`` restores the snapshot under
  ``ENV_LOCK``) → :class:`EnvVarGuard` context manager (``__enter__``/``__exit__``
  + ``close``/``__del__``), serialised by a module-level :class:`threading.Lock`.
* ``url`` / ``tracing`` crate deps → unused in grok's ``lib.rs``; dropped (zero
  non-stdlib deps — only ``os`` / ``threading`` / ``dataclasses`` / ``enum``).

Product fusion
--------------
grok's endpoints point at ``grok.com`` (cli-chat-proxy / assets / relay ws /
gateway ws / ws origin) — a cloud-native multi-service backend. MiniMax Code's
agent talks to the MiniMax chat-completions API today, so ``api_base_url`` (the
``cli_chat_proxy_base_url`` slot) is the one actively consumed (the ``llm.py``
wiring that reads it is a future round; this module is the vocabulary). The
asset / relay / gateway / ws-origin URLs preserve grok's full multi-endpoint
structure as extension slots — the platform *shape* is kept even where MiniMax
Code does not yet populate every slot, so a future multi-service backend slots
in without re-architecting. The env-prefix is rebranded ``MINIMAX_PRODUCTION``
(grok ``GROK_PRODUCTION``); the per-endpoint suffixes (``_CLI_CHAT_PROXY_BASE_URL``
etc.) are an operator interface and are kept verbatim.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from enum import Enum, unique


@dataclass(frozen=True, slots=True)
class BuildEndpoints:
    """The endpoint set for one backend environment (grok ``GrokBuildEndpoints``).

    A frozen bundle of endpoint URL strings (branch 2: pure value equality, no
    wire surface). ``cli_chat_proxy_base_url`` is the slot MiniMax Code actively
    consumes (MiniMax chat completions); the remainder preserve grok's
    multi-service structure as extension slots the platform can populate later.
    """

    cli_chat_proxy_base_url: str
    asset_server_url: str
    relay_ws_url: str
    gateway_ws_url: str
    ws_origin: str


#: Production endpoint defaults for MiniMax Code (grok ``PRODUCTION_ENDPOINTS``).
#:
#: ``cli_chat_proxy_base_url`` is the actively-consumed MiniMax chat-completions
#: base; the rest are extension slots preserving grok's cloud multi-service shape
#: (relay/gateway WS, asset server, CORS origin). Override any per-endpoint via
#: ``MINIMAX_PRODUCTION_*`` env vars — see :meth:`BuildEnvironment._resolve`.
PRODUCTION_ENDPOINTS: BuildEndpoints = BuildEndpoints(
    cli_chat_proxy_base_url="https://api.minimax.chat/v1",
    asset_server_url="https://api.minimax.chat/assets",
    relay_ws_url="wss://api.minimax.chat/ws/code-agent",
    gateway_ws_url="wss://api.minimax.chat/ws/gateway",
    ws_origin="https://api.minimax.chat",
)

#: Compiled production endpoint aliases (grok ``PROD_*``) — stable handles for the
#: single source of truth in :data:`PRODUCTION_ENDPOINTS`.
PROD_CLI_CHAT_PROXY_BASE_URL: str = PRODUCTION_ENDPOINTS.cli_chat_proxy_base_url
PROD_ASSET_SERVER_URL: str = PRODUCTION_ENDPOINTS.asset_server_url
PROD_RELAY_WS_URL: str = PRODUCTION_ENDPOINTS.relay_ws_url
PROD_GATEWAY_WS_URL: str = PRODUCTION_ENDPOINTS.gateway_ws_url
PROD_WS_ORIGIN: str = PRODUCTION_ENDPOINTS.ws_origin


@unique
class BuildEnvironment(Enum):
    """A backend environment selection (grok ``GrokBuildEnvironment``).

    Single member ``Production`` today (grok parity — Dev/Staging are compiled
    out in public builds, so ``from_flags`` is a no-op always returning
    Production). The env-prefix is ``MINIMAX_PRODUCTION`` (grok ``GROK_PRODUCTION``);
    per-endpoint overrides are read from ``{prefix}_{suffix}`` env vars, else the
    compiled default (grok ``resolve``).
    """

    Production = "production"

    @classmethod
    def from_flags(cls, _dev: bool, _staging: bool) -> BuildEnvironment:
        """Resolve from CLI flags (grok ``from_flags``). Public builds: always Production."""
        return cls.Production

    def indicator(self) -> str | None:
        """Display marker; ``None`` for Production (grok ``indicator``)."""
        match self:
            case BuildEnvironment.Production:
                return None

    def is_production(self) -> bool:
        """Whether this is the Production environment (grok ``is_production``)."""
        return self is BuildEnvironment.Production

    def env_prefix(self) -> str:
        """Env-var prefix for per-endpoint overrides (grok ``env_prefix``).

        An operator interface — do not rename. ``MINIMAX_PRODUCTION`` for
        Production (grok ``GROK_PRODUCTION``); future environments get their own
        arm here.
        """
        match self:
            case BuildEnvironment.Production:
                return "MINIMAX_PRODUCTION"

    def endpoints(self) -> BuildEndpoints:
        """Compiled endpoint set for this environment (grok ``endpoints``)."""
        match self:
            case BuildEnvironment.Production:
                return PRODUCTION_ENDPOINTS

    def _resolve(self, var_suffix: str, compiled: str) -> str:
        """Env-var override when set, else the compiled endpoint (grok ``resolve``)."""
        return os.environ.get(f"{self.env_prefix()}{var_suffix}", compiled)

    def cli_chat_proxy_base_url(self) -> str:
        """The MiniMax chat-completions base URL (actively consumed; grok ``cli_chat_proxy_base_url``)."""
        return self._resolve("_CLI_CHAT_PROXY_BASE_URL", self.endpoints().cli_chat_proxy_base_url)

    def ws_origin(self) -> str:
        """The WebSocket / fetch origin (grok ``ws_origin``)."""
        return self._resolve("_WS_ORIGIN", self.endpoints().ws_origin)

    def asset_server_url(self) -> str:
        """The asset server URL (extension slot; grok ``asset_server_url``)."""
        return self._resolve("_ASSET_SERVER_URL", self.endpoints().asset_server_url)

    def relay_ws_url(self) -> str:
        """The relay WebSocket URL (grok ``relay_ws_url``).

        Distinct from :meth:`gateway_ws_url` — the two speak different protocols
        (a relay loop must never connect to the gateway URL).
        """
        return self._resolve("_WS_URL", self.endpoints().relay_ws_url)

    def gateway_ws_url(self) -> str:
        """The gateway WebSocket URL (grok ``gateway_ws_url``)."""
        return self._resolve("_GATEWAY_WS_URL", self.endpoints().gateway_ws_url)

    def __str__(self) -> str:
        return self.value


# --- EnvVarGuard (test RAII, grok #[cfg(test)] EnvVarGuard) -----------------

#: Serialises env-var mutation across threads; ``os.environ`` is process-global
#: (mirrors grok's ``ENV_LOCK: std::sync::Mutex<()>``). Held from a guard's
#: construction until its ``close``/``__exit__``/``__del__``.
_ENV_LOCK = threading.Lock()


class EnvVarGuard:
    """RAII env-var override for tests (grok ``EnvVarGuard``, ``#[cfg(test)]``).

    Constructors (:meth:`set` / :meth:`remove`) snapshot the prior value under
    :data:`_ENV_LOCK`; ``close`` / ``__exit__`` / ``__del__`` restores it
    (panics/exceptions included — ``__exit__`` always runs). Mirrors grok's
    ``set`` / ``remove`` / ``set_value`` + ``Drop``.

    Use as a context manager (preferred) or call ``close()`` explicitly::

        with EnvVarGuard.set("MINIMAX_PRODUCTION_WS_ORIGIN", "https://x"):
            ...  # env overridden, lock held
        # restored + lock released on exit

    Non-``with`` usage relies on ``close()`` (or best-effort ``__del__``) to
    release the lock — prefer ``with``.
    """

    __slots__ = ("_key", "_prev", "_closed")

    def __init__(self, key: str, prev: str | None) -> None:
        # ``_ENV_LOCK`` must already be held by the caller (set/remove).
        self._key = key
        self._prev = prev
        self._closed = False

    # context-manager + RAII surface
    def __enter__(self) -> EnvVarGuard:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Restore the snapshot + release the env lock (idempotent; grok ``Drop``)."""
        if self._closed:
            return
        self._closed = True
        try:
            if self._prev is None:
                os.environ.pop(self._key, None)
            else:
                os.environ[self._key] = self._prev
        finally:
            _ENV_LOCK.release()

    def __del__(self) -> None:
        # Best-effort cleanup; never raise from __del__.
        try:
            self.close()
        except Exception:
            pass

    @classmethod
    def set(cls, key: str, value: str) -> EnvVarGuard:
        """Set ``key`` to ``value``, snapshotting the prior value (grok ``set``)."""
        _ENV_LOCK.acquire()
        prev = os.environ.get(key)
        os.environ[key] = value
        return cls(key, prev)

    @classmethod
    def remove(cls, key: str) -> EnvVarGuard:
        """Remove ``key``, snapshotting the prior value (grok ``remove``)."""
        _ENV_LOCK.acquire()
        prev = os.environ.get(key)
        os.environ.pop(key, None)
        return cls(key, prev)

    def set_value(self, value: str) -> None:
        """Update the value while still holding the env lock (grok ``set_value``)."""
        if self._closed:
            raise RuntimeError("EnvVarGuard already closed")
        os.environ[self._key] = value


__all__ = [
    "BuildEndpoints",
    "BuildEnvironment",
    "EnvVarGuard",
    "PRODUCTION_ENDPOINTS",
    "PROD_CLI_CHAT_PROXY_BASE_URL",
    "PROD_ASSET_SERVER_URL",
    "PROD_RELAY_WS_URL",
    "PROD_GATEWAY_WS_URL",
    "PROD_WS_ORIGIN",
]
