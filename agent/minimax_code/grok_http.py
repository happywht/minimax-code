"""Outbound HTTP client identity + User-Agent rendering -- fusion of grok's
``xai-grok-http`` (R193, lib.rs pure-logic subset).

``xai-grok-http`` (636 lines, single ``lib.rs``) ships four concerns; this
module migrates the two pure-logic slices and records the rest as YAGNI:

1. **User-Agent rendering** (pure string logic) -- ``OriginClientInfo`` +
   ``PlatformInfo`` + ``UserAgent.render`` + the ``origin_client_info_*``
   builders + ``process/session_user_agent_string``. Migrated.
2. **Process client-mode latch** (pure) -- ``CLIENT_MODE_HEADER`` +
   ``set_process_client_mode_headless`` + ``process_client_mode``. Migrated.
3. **Transport-failure classification** (reqwest-coupled) -- only the
   ``TransportFailureKind`` *label enum* is migrated; ``TransportFailure::
   classify`` is YAGNI (consumes ``reqwest::Error`` predicates with no httpx
   equivalent at this layer).
4. **reqwest client builders + retry-escape loop** -- ``shared_client`` /
   ``shared_upload_client`` / ``fresh_http1_client`` / ``shared_blocking_client``
   / ``with_auth_retry`` / ``send_with_retry_escaping_pool`` / ``error_cause_chain``.
   Entirely YAGNI: the LLM transport already uses ``httpx.AsyncClient``
   (``agent/llm.py``) and the resilience stack (circuit breaker R17-R21, retry
   middleware R188) owns retry + pool-eviction policy. reqwest's HTTP/2-keepalive
   / pool-idle / http1-only builder knobs are reqwest-specific.

``startup_timer!`` macro YAGNI: routes to ``xai_grok_telemetry`` Chrome-trace
spans; the platform tracing crate (R127-R132) owns startup instrumentation.

Product fusion renames (grok -> MiniMax Code identity)
------------------------------------------------------

* env ``GROK_CLIENT_NAME`` / ``GROK_CLIENT_VERSION`` ->
  ``MINIMAX_CODE_CLIENT_NAME`` / ``MINIMAX_CODE_CLIENT_VERSION`` (mirrors the
  R42 ``GROK_TEST_VERSION`` -> ``MINIMAX_CODE_TEST_VERSION`` rename).
* default client identifier + ``agent_product`` ``"grok-shell"`` ->
  ``"minimax-code"`` (the UA advertises the platform, not the upstream CLI).
* header ``x-grok-client-mode`` -> ``x-minimax-code-client-mode``.

``ClientType`` YAGNI
--------------------

grok's ``origin_client_info_from_client_type`` / ``client_type_from_origin`` /
``set_client_name`` / ``CLIENT_TYPE`` consume ``xai_grok_workspace::permission::
ClientType`` -- a shell-side enum of grok client kinds with a
``user_agent_label``. The workspace-permission crate is not migrated and the
platform has no grok client-type taxonomy, so the ``clientType`` fallback
branch of ``origin_client_info_from_meta`` (which deserializes that enum) is
YAGNI: when ``clientIdentifier`` is absent the function returns ``None``. The
four ``ClientType``-bearing symbols are not migrated.

``OriginClientInfo`` source-of-truth flip
-----------------------------------------

In grok this type is owned by ``xai-grok-sampler`` and re-exported from
``xai-grok-http``. The sampler crate is not yet migrated, so this module is the
platform's source of truth; when the sampler lands it will import from here
(dependency direction inverted, recorded so the sampler round honours it).
"""

from __future__ import annotations

import os
import platform
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from minimax_code.version import VERSION

# Env-var + identity renames (grok -> MiniMax Code; see module docstring).
_CLIENT_NAME_ENV = "MINIMAX_CODE_CLIENT_NAME"
_CLIENT_VERSION_ENV = "MINIMAX_CODE_CLIENT_VERSION"
_DEFAULT_CLIENT_IDENTIFIER = "minimax-code"
_AGENT_PRODUCT = "minimax-code"
_CLIENT_MODE_HEADLESS = "headless"
_CLIENT_MODE_INTERACTIVE = "interactive"


@dataclass(frozen=True, slots=True)
class OriginClientInfo:
    """Originating-client identity for a User-Agent (grok ``OriginClientInfo``).

    ``product`` is the client's product name; ``version`` is its version string
    when known. See the module docstring's source-of-truth note: in grok this
    type is owned by the sampler crate and re-exported here; the platform
    defines it in this module until the sampler lands.
    """

    product: str
    version: str | None = None


@dataclass(frozen=True, slots=True)
class PlatformInfo:
    """Normalized OS + arch pair for the User-Agent suffix (grok ``PlatformInfo``)."""

    os: str
    arch: str

    @classmethod
    def current(cls) -> PlatformInfo:
        """The current process's platform, normalized to grok's labels.

        grok reads ``std::env::consts::{OS,ARCH}`` (already lowercase: ``macos`` /
        ``windows`` / ``linux``; ``aarch64`` / ``x86_64``). Python's
        ``platform.system()`` / ``platform.machine()`` return Title-case / mixed
        values (``Darwin`` / ``Windows``; ``arm64`` / ``AMD64`` / ``aarch64``),
        so a small map restores the grok-normalized labels.
        """
        system = platform.system()
        machine = platform.machine()
        return cls(
            os=_OS_MAP.get(system, system.lower()),
            arch=_ARCH_MAP.get(machine, machine.lower()),
        )


#: ``platform.system()`` (Title-case) -> grok ``std::env::consts::OS`` (lowercase).
_OS_MAP: Mapping[str, str] = {
    "Darwin": "macos",
    "Windows": "windows",
    "Linux": "linux",
}

#: ``platform.machine()`` -> grok ``std::env::consts::ARCH``.
_ARCH_MAP: Mapping[str, str] = {
    "arm64": "aarch64",
    "AMD64": "x86_64",
    "x86_64": "x86_64",
    "aarch64": "aarch64",
}


@dataclass(frozen=True, slots=True)
class UserAgent:
    """A renderable User-Agent value (grok ``UserAgent``).

    Carries the origin client identity, the agent product/version (the process
    doing the request), and the platform pair. ``render`` collapses the
    origin+agent pair when they are identical (no point advertising the same
    product twice); otherwise it emits both, with the origin's version when
    present.
    """

    origin: OriginClientInfo
    agent_product: str
    agent_version: str
    platform: PlatformInfo

    def render(self) -> str:
        """Format the User-Agent string (grok ``UserAgent::render``)."""
        if (
            self.origin.product == self.agent_product
            and self.origin.version == self.agent_version
        ):
            return (
                f"{self.agent_product}/{self.agent_version} "
                f"({self.platform.os}; {self.platform.arch})"
            )
        if self.origin.version is not None:
            return (
                f"{self.origin.product}/{self.origin.version} "
                f"{self.agent_product}/{self.agent_version} "
                f"({self.platform.os}; {self.platform.arch})"
            )
        return (
            f"{self.origin.product} "
            f"{self.agent_product}/{self.agent_version} "
            f"({self.platform.os}; {self.platform.arch})"
        )


class TransportFailureKind(StrEnum):
    """How a transport send failure should be treated by a retry loop.

    Migrated as a bare label enum (grok ``TransportFailureKind``); the
    ``TransportFailure::classify`` method that maps a ``reqwest::Error`` onto
    these labels is YAGNI (reqwest's ``is_connect`` / ``is_timeout`` /
    ``is_request`` / ``is_body`` predicates have no httpx-layer equivalent here
    -- the platform's httpx errors are classified inside the resilience stack,
    R17-R21).
    """

    UNREACHABLE = "unreachable"
    INTERRUPTED = "interrupted"
    PERMANENT = "permanent"


def agent_version() -> str:
    """The agent version string (grok ``agent_version`` -> ``xai_grok_version::VERSION``).

    Single source of truth: the platform's :data:`minimax_code.version.VERSION`
    (R42), resolved once from package metadata. Kept as a function (not a
    constant) to mirror grok's call shape.
    """
    return VERSION


def origin_client_info_from_env() -> OriginClientInfo | None:
    """Build an ``OriginClientInfo`` from the client-name/version env vars.

    Returns ``None`` when ``MINIMAX_CODE_CLIENT_NAME`` is unset (grok returns
    ``None`` when ``GROK_CLIENT_NAME`` is unset); the version env var is optional.
    """
    product = os.environ.get(_CLIENT_NAME_ENV)
    if product is None:
        return None
    return OriginClientInfo(product=product, version=os.environ.get(_CLIENT_VERSION_ENV))


def origin_client_info_from_meta(
    meta: Mapping[str, Any] | None,
) -> OriginClientInfo | None:
    """Extract an ``OriginClientInfo`` from a session-metadata map.

    Reads ``clientIdentifier`` (string) + ``clientVersion`` (string, optional).
    grok falls back to deserializing ``clientType`` via the ``ClientType`` enum
    when ``clientIdentifier`` is absent; that branch is YAGNI here (``ClientType``
    is not migrated -- see module docstring), so an absent ``clientIdentifier``
    yields ``None`` even if ``clientType`` is present.
    """
    if meta is None:
        return None
    identifier = meta.get("clientIdentifier")
    if not isinstance(identifier, str):
        return None
    raw_version = meta.get("clientVersion")
    version = raw_version if isinstance(raw_version, str) else None
    return OriginClientInfo(product=identifier, version=version)


def merge_origin_client_info(
    primary: OriginClientInfo | None,
    fallback: OriginClientInfo | None,
) -> OriginClientInfo | None:
    """Merge two optional origins: primary's product wins, version backfills.

    (grok ``merge_origin_client_info``.) When both are present the primary product
    is kept and its version falls back to the secondary's when the primary has
    none. Either-alone passes through; both-absent returns ``None``.
    """
    if primary is not None and fallback is not None:
        return OriginClientInfo(
            product=primary.product,
            version=primary.version if primary.version is not None else fallback.version,
        )
    if primary is not None:
        return primary
    return fallback


def process_client_identifier() -> str:
    """Process-level client identifier (grok ``process_client_identifier``).

    ``MINIMAX_CODE_CLIENT_NAME`` env var, defaulting to ``"minimax-code"``
    (grok defaulted to ``"grok-shell"``).
    """
    return os.environ.get(_CLIENT_NAME_ENV, _DEFAULT_CLIENT_IDENTIFIER)


#: Header telling a proxy whether this process is single-prompt or interactive
#: (grok ``CLIENT_MODE_HEADER = "x-grok-client-mode"``). Renamed for the platform.
CLIENT_MODE_HEADER: str = "x-minimax-code-client-mode"

# Process-level client-mode latch (grok ``CLIENT_MODE: OnceLock<&'static str>``).
# A one-way latch: ``set_process_client_mode_headless`` flips it to "headless"
# on first call and is a no-op thereafter; ``process_client_mode`` reads it back,
# defaulting to "interactive". Mirrors the Rust ``OnceLock`` semantics (set once).
_client_mode: str | None = None


def set_process_client_mode_headless() -> None:
    """Mark this process headless (single-prompt). No-op if already set.

    (grok ``set_process_client_mode_headless``; ``OnceLock::set`` is ignored on a
    second call, hence the ``is None`` guard.)
    """
    global _client_mode
    if _client_mode is None:
        _client_mode = _CLIENT_MODE_HEADLESS


def process_client_mode() -> str:
    """The mode sent in ``CLIENT_MODE_HEADER`` (grok ``process_client_mode``).

    Defaults to ``"interactive"`` until :func:`set_process_client_mode_headless` runs.
    """
    return _client_mode if _client_mode is not None else _CLIENT_MODE_INTERACTIVE


def process_user_agent_string() -> str:
    """Process-wide User-Agent for outbound requests (grok ``process_user_agent_string``).

    Origin from env when set, else the default ``"minimax-code"`` identifier
    carrying the agent version. grok falls back to ``CLIENT_TYPE`` (Generic) here;
    that path is YAGNI (``ClientType`` not migrated). Agent product is the
    platform (``"minimax-code"``, grok used ``"grok-shell"``).
    """
    version = agent_version()
    origin = origin_client_info_from_env()
    if origin is None:
        origin = OriginClientInfo(product=_DEFAULT_CLIENT_IDENTIFIER, version=version)
    return UserAgent(
        origin=origin,
        agent_product=_AGENT_PRODUCT,
        agent_version=version,
        platform=PlatformInfo.current(),
    ).render()


def session_user_agent_string(origin: OriginClientInfo) -> str:
    """Per-session User-Agent carrying the given origin (grok ``session_user_agent_string``)."""
    return UserAgent(
        origin=origin,
        agent_product=_AGENT_PRODUCT,
        agent_version=agent_version(),
        platform=PlatformInfo.current(),
    ).render()


def user_agent_string_for(origin: OriginClientInfo) -> str:
    """Alias of :func:`session_user_agent_string` (grok ``user_agent_string_for``)."""
    return session_user_agent_string(origin)


__all__ = [
    "CLIENT_MODE_HEADER",
    "OriginClientInfo",
    "PlatformInfo",
    "TransportFailureKind",
    "UserAgent",
    "agent_version",
    "merge_origin_client_info",
    "origin_client_info_from_env",
    "origin_client_info_from_meta",
    "process_client_identifier",
    "process_client_mode",
    "process_user_agent_string",
    "session_user_agent_string",
    "set_process_client_mode_headless",
    "user_agent_string_for",
]
