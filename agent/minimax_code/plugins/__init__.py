"""Plugins — the third platform pillar (after MCP and Hooks).

A plugin is a directory with a ``plugin.json`` manifest declaring
contributions: lifecycle hooks, MCP servers, required permissions.
The loader discovers them on disk; the registry indexes them and
applies their contributions to the live subsystems.

Package layout
--------------

* :mod:`.manifest` — :class:`PluginManifest` model.
* :mod:`.loader`   — :class:`PluginLoader` + :class:`Plugin` record.
* :mod:`.registry` — :class:`PluginRegistry` (index + apply).
"""

from __future__ import annotations

from .loader import DEFAULT_MANIFEST_NAME, Plugin, PluginLoader
from .manifest import PluginManifest
from .registry import PluginRegistry

__all__ = [
    "DEFAULT_MANIFEST_NAME",
    "Plugin",
    "PluginLoader",
    "PluginManifest",
    "PluginRegistry",
]
