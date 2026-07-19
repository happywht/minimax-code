"""MCP (Model Context Protocol) server config value types (R66).

Fusion of grok-build's ``xai-grok-config-types::mcp`` — the leaf types for
the ``mcpServers`` section of config: per-server transport (stdio or
streamable-HTTP), enablement, OAuth block, per-tool timeouts, plus the
relay-sync toggle and the top-level ``McpConfig`` map.

Pure types + pure logic (the only side-effect is :class:`RelaySyncConfig`
and :func:`resolve_oauth_client_secret` reading ``os.environ``, which is
their whole purpose). Part of R66's runtime config type contract.

Forward-migrated from Rust to pydantic v2. This is the most wire-intricate
file in R66 — three serde patterns compose:

1. **untagged enum** ``McpServerTransportConfig`` (``Stdio | StreamableHttp``)
   → two flat pydantic models (:class:`StdioTransport` /
   :class:`StreamableHttpTransport`) plus a dispatch in
   :class:`McpServerConfig`'s ``model_validator(mode="before")``.
2. **flatten** of the transport into the parent ``McpServerConfig`` (the
   transport fields appear at the top level alongside ``enabled`` / ``oauth``
   / …) → the same ``model_validator`` splits transport keys from config
   keys on read; :meth:`McpServerConfig.to_wire` re-merges them on write.
3. **rename_all = "camelCase"** on :class:`McpJsonOAuthBlock`.

Cross-crate notes
-----------------

* ``to_acp_mcp_server(name)`` depends on the ``agent-client-protocol``
  crate — **YAGNI** here (no ACP consumer in this project yet).
* ``oauth_config() -> Option<McpOAuthConfig>`` depends on
  ``xai_grok_mcp::oauth_config`` — **YAGNI** here (the OAuth runtime lives
  in the MCP client layer, not the type layer).
* ``tracing::warn!`` calls in the source are omitted (this is a pure-type
  module; logging belongs to the consuming layer).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "StdioTransport",
    "StreamableHttpTransport",
    "McpJsonOAuthBlock",
    "McpServerConfig",
    "RelaySyncConfig",
    "McpConfig",
    "resolve_oauth_client_secret",
]


# ---------------------------------------------------------------------------
# Transports (the two arms of the untagged McpServerTransportConfig enum)
# ---------------------------------------------------------------------------

#: Stdio transport wire keys (flattened into the parent config).
_STDIO_KEYS = frozenset({"command", "args", "env", "cwd"})

#: Streamable-HTTP transport wire keys (flattened into the parent config).
#: ``"type"`` is the wire alias of ``transport_type``.
_HTTP_KEYS = frozenset(
    {
        "url",
        "type",
        "bearer_token_env_var",
        "headers",
        "oauth_client_id",
        "oauth_client_secret_env_var",
        "oauth_scopes",
    }
)


class StdioTransport(BaseModel):
    """The ``Stdio`` arm: launch a local subprocess and talk MCP over stdin/stdout.

    Wire shape: ``command`` always present; ``args`` always present (defaults
    to ``[]``); ``env`` / ``cwd`` omitted when ``None``.
    """

    model_config = ConfigDict(populate_by_name=True)

    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None


class StreamableHttpTransport(BaseModel):
    """The ``StreamableHttp`` arm: talk MCP over HTTP/SSE to a remote server.

    Wire shape: ``url`` always present; the optional ``type`` field is the
    wire alias of ``transport_type``; everything else omitted when ``None``.
    """

    model_config = ConfigDict(populate_by_name=True)

    url: str
    transport_type: str | None = Field(default=None, alias="type")
    bearer_token_env_var: str | None = None
    headers: dict[str, str] | None = None
    oauth_client_id: str | None = None
    oauth_client_secret_env_var: str | None = None
    oauth_scopes: list[str] | None = None


# ---------------------------------------------------------------------------
# OAuth block (camelCase wire)
# ---------------------------------------------------------------------------


class McpJsonOAuthBlock(BaseModel):
    """Per-server OAuth block, wire-named in camelCase (``clientId`` etc.).

    Every field is ``Option`` and ``skip_serializing_if = "Option::is_none"``
    in Rust, so :meth:`to_wire` omits all absent fields.
    """

    model_config = ConfigDict(populate_by_name=True)

    client_id: str | None = Field(default=None, alias="clientId")
    client_secret_env_var: str | None = Field(default=None, alias="clientSecretEnvVar")
    scopes: list[str] | None = None
    callback_port: int | None = Field(default=None, alias="callbackPort")

    def to_wire(self) -> dict[str, Any]:
        """Emit the camelCase wire shape, omitting absent fields."""
        out: dict[str, Any] = {}
        if self.client_id is not None:
            out["clientId"] = self.client_id
        if self.client_secret_env_var is not None:
            out["clientSecretEnvVar"] = self.client_secret_env_var
        if self.scopes is not None:
            out["scopes"] = self.scopes
        if self.callback_port is not None:
            out["callbackPort"] = self.callback_port
        return out


# ---------------------------------------------------------------------------
# McpServerConfig — flatten(transport) + config fields
# ---------------------------------------------------------------------------


class McpServerConfig(BaseModel):
    """One MCP server's full config: flattened transport + server options.

    On the wire the transport fields are **flattened to the top level** and
    the untagged transport kind is inferred from which keys are present
    (``command`` → Stdio, ``url`` → StreamableHttp). Internally we hold the
    parsed transport in :attr:`transport` and the server options as flat
    fields; :meth:`to_wire` re-flattens for serialisation.

    Methods dropped as YAGNI (cross-crate deps, no consumer here):

    * ``to_acp_mcp_server(name)`` — needs ``agent-client-protocol``.
    * ``oauth_config()`` — needs ``xai_grok_mcp::oauth_config::McpOAuthConfig``.
    """

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    transport: StdioTransport | StreamableHttpTransport
    enabled: bool = True
    oauth: McpJsonOAuthBlock | None = None
    startup_timeout_sec: int | None = None
    tool_timeout_sec: int | None = None
    tool_timeouts: dict[str, int] | None = None
    expose_image_base64: bool | None = None

    # -- flatten(transport): split on read ---------------------------------

    @model_validator(mode="before")
    @classmethod
    def _split_transport(cls, data: Any) -> Any:
        """Split the flattened wire form into ``transport`` + config fields.

        Accepts either the flattened wire form (transport keys at the top
        level) or the already-split internal form (an explicit ``transport``
        key). Dispatch is by key presence, mirroring serde untagged + flatten:
        ``command`` present → Stdio, else ``url`` present → StreamableHttp,
        else leave ``transport`` absent so pydantic reports the missing
        required field.
        """
        if not isinstance(data, dict):
            return data

        # Already-split internal form — accept as-is.
        existing = data.get("transport")
        if isinstance(existing, (StdioTransport, StreamableHttpTransport)):
            return data

        if "command" in data:
            trans_dict = {k: data[k] for k in _STDIO_KEYS if k in data}
            transport: StdioTransport | StreamableHttpTransport = StdioTransport.model_validate(
                trans_dict
            )
        elif "url" in data:
            trans_dict = {k: data[k] for k in _HTTP_KEYS if k in data}
            transport = StreamableHttpTransport.model_validate(trans_dict)
        else:
            # No transport keys — let pydantic flag the missing required field.
            return {k: v for k, v in data.items() if k not in _STDIO_KEYS and k not in _HTTP_KEYS}

        result = {k: v for k, v in data.items() if k not in _STDIO_KEYS and k not in _HTTP_KEYS}
        result["transport"] = transport
        return result

    # -- flatten(transport): re-merge on write -----------------------------

    def to_wire(self) -> dict[str, Any]:
        """Re-flatten transport fields to the top level + emit config fields.

        Reproduces serde ``flatten`` + per-field ``skip_serializing_if``:
        transport keys come first, then ``enabled`` (always), then the
        optional config fields (omitted when ``None``).
        """
        out: dict[str, Any] = {}
        t = self.transport
        if isinstance(t, StdioTransport):
            out["command"] = t.command
            out["args"] = list(t.args)
            if t.env is not None:
                out["env"] = t.env
            if t.cwd is not None:
                out["cwd"] = t.cwd
        else:  # StreamableHttpTransport
            out["url"] = t.url
            if t.transport_type is not None:
                out["type"] = t.transport_type
            if t.bearer_token_env_var is not None:
                out["bearer_token_env_var"] = t.bearer_token_env_var
            if t.headers is not None:
                out["headers"] = t.headers
            if t.oauth_client_id is not None:
                out["oauth_client_id"] = t.oauth_client_id
            if t.oauth_client_secret_env_var is not None:
                out["oauth_client_secret_env_var"] = t.oauth_client_secret_env_var
            if t.oauth_scopes is not None:
                out["oauth_scopes"] = t.oauth_scopes

        out["enabled"] = self.enabled
        if self.oauth is not None:
            out["oauth"] = self.oauth.to_wire()
        if self.startup_timeout_sec is not None:
            out["startup_timeout_sec"] = self.startup_timeout_sec
        if self.tool_timeout_sec is not None:
            out["tool_timeout_sec"] = self.tool_timeout_sec
        if self.tool_timeouts is not None:
            out["tool_timeouts"] = self.tool_timeouts
        if self.expose_image_base64 is not None:
            out["expose_image_base64"] = self.expose_image_base64
        return out

    # -- env-var / placeholder expansion -----------------------------------

    def expand_strings(self, sub: Callable[[str], str]) -> None:
        """Apply ``sub`` to every user-supplied string in the transport.

        Mirrors Rust ``expand_strings``: for Stdio it rewrites ``command``,
        each ``arg``, each env-map **value** (keys untouched), and ``cwd``;
        for StreamableHttp it rewrites ``url`` and each header **value**.
        """
        t = self.transport
        if isinstance(t, StdioTransport):
            t.command = sub(t.command)
            t.args = [sub(a) for a in t.args]
            if t.env is not None:
                t.env = {k: sub(v) for k, v in t.env.items()}
            if t.cwd is not None:
                t.cwd = sub(t.cwd)
        else:  # StreamableHttpTransport
            t.url = sub(t.url)
            if t.headers is not None:
                t.headers = {k: sub(v) for k, v in t.headers.items()}


# ---------------------------------------------------------------------------
# Relay sync + top-level McpConfig
# ---------------------------------------------------------------------------


class RelaySyncConfig(BaseModel):
    """Relay-sync toggle (``[relay_sync]``).

    :meth:`is_enabled` honours the ``GROK_RELAY_SYNC_ENABLED`` env var first
    (``"true"``/``"1"`` → on, any other value → off), falling back to the
    configured :attr:`enabled`, then to ``False``.
    """

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool | None = None

    def is_enabled(self) -> bool:
        """Env var (if set) overrides config; config (if set) overrides False."""
        env_val = os.environ.get("GROK_RELAY_SYNC_ENABLED")
        if env_val is not None:
            return env_val in ("true", "1")
        return self.enabled if self.enabled is not None else False


def resolve_oauth_client_secret(env_var: str | None) -> str | None:
    """Resolve an OAuth client secret from its env-var name.

    Returns the env var's value when set and non-empty, otherwise ``None``
    (the source logs a ``tracing::warn!`` in both empty/missing cases; that
    logging is omitted here as this is a pure-type helper).
    """
    if env_var is None:
        return None
    secret = os.environ.get(env_var)
    if secret is not None and secret != "":
        return secret
    return None


class McpConfig(BaseModel):
    """The top-level ``mcpServers`` map (``McpConfig`` in Rust).

    The Rust ``IndexMap<String, McpServerConfig>`` preserves insertion
    order; a plain ``dict`` does the same in Python 3.7+. The wire key is
    ``mcpServers``.
    """

    model_config = ConfigDict(populate_by_name=True)

    mcp_servers: dict[str, McpServerConfig] = Field(
        default_factory=dict, alias="mcpServers"
    )

    @classmethod
    def default(cls) -> McpConfig:
        """Empty server map (the all-default MCP config)."""
        return cls()
