"""Plugin manifest model.

A plugin is a self-contained bundle (directory) shipping a JSON
manifest that declares what it contributes: lifecycle hooks, MCP
servers, required permissions, and (later) a Python entry module.

The manifest shape reuses existing subsystem formats so a plugin is a
thin declarative wrapper:

* ``hooks``      — Claude-style ``{<event>: [spec, ...]}`` (same as
  :class:`~minimax_code.hooks.registry.HookRegistry` consumes).
* ``mcp_servers``— list of :class:`~minimax_code.mcp.registry.MCPServerConfig`
  dicts.
* ``permissions``— list of permission rule strings the plugin needs.

Models are permissive (``extra="allow"``) so unknown keys survive and
forward-compatible manifests don't break loading.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class PluginManifest(_Base):
    """One plugin's declaration, as parsed from ``plugin.json``."""

    name: str
    version: str = "0.0.0"
    description: str = ""
    author: str | None = None
    homepage: str | None = None
    enabled: bool = True

    # Contributions — each optional.
    hooks: dict[str, list[dict[str, Any]]] | None = None
    mcp_servers: list[dict[str, Any]] | None = None
    permissions: list[str] | None = None

    # Python entry module (e.g. "mypkg.plugin"); loaded by the runtime
    # to register tools/skills dynamically. R8 leaves this declarative.
    entry: str | None = None

    def normalized_hooks(self) -> dict[str, list[dict[str, Any]]]:
        """Hooks in the shape ``HookRegistry.load_dict`` expects."""
        return self.hooks or {}


__all__ = ["PluginManifest"]
