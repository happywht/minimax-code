"""Plugin registry — indexes loaded plugins and applies their contributions.

The registry is the bridge between discovered plugin manifests and the
live subsystems (hooks, MCP, permissions). ``apply_hooks`` pours every
enabled plugin's hooks into a :class:`~minimax_code.hooks.HookRegistry`,
so a plugin's lifecycle hooks become agent-active with one call.
"""

from __future__ import annotations

import logging

from ..hooks import HookRegistry
from .loader import Plugin

logger = logging.getLogger(__name__)


class PluginRegistry:
    """In-memory index of plugins keyed by manifest name."""

    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}

    # -- mutation -----------------------------------------------------------

    def add(self, plugin: Plugin) -> bool:
        """Register a plugin. Returns False (and skips) on name conflict."""
        name = plugin.name
        if name in self._plugins:
            logger.warning("plugin %r already registered, skipping", name)
            return False
        self._plugins[name] = plugin
        return True

    def add_many(self, plugins: list[Plugin]) -> int:
        added = 0
        for plugin in plugins:
            if self.add(plugin):
                added += 1
        return added

    def remove(self, name: str) -> bool:
        return self._plugins.pop(name, None) is not None

    def clear(self) -> None:
        self._plugins.clear()

    # -- queries ------------------------------------------------------------

    def get(self, name: str) -> Plugin | None:
        return self._plugins.get(name)

    def has(self, name: str) -> bool:
        return name in self._plugins

    def list(self) -> list[Plugin]:
        return list(self._plugins.values())

    def names(self) -> list[str]:
        return list(self._plugins.keys())

    def count(self) -> int:
        return len(self._plugins)

    def failed(self) -> list[Plugin]:
        """Plugins whose manifest failed to parse (``error`` set)."""
        return [p for p in self._plugins.values() if not p.ok]

    def enabled(self) -> list[Plugin]:
        return [p for p in self._plugins.values() if p.ok and self._effective_enabled(p)]

    def _effective_enabled(self, plugin: Plugin) -> bool:
        """Resolve a plugin's enabled state (delegates to the record)."""
        return plugin.effective_enabled

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """Apply a runtime enabled override.

        Returns ``False`` (no-op) when ``name`` is unknown. The override is
        in-memory only; the manifest on disk remains the source of truth
        across restarts until persistence lands.
        """
        plugin = self._plugins.get(name)
        if plugin is None:
            return False
        plugin.runtime_enabled = enabled
        return True

    # -- application --------------------------------------------------------

    def apply_hooks(self, hook_registry: HookRegistry) -> int:
        """Pour every enabled plugin's hooks into ``hook_registry``.

        Returns the total number of hook specs loaded.
        """
        total = 0
        for plugin in self.enabled():
            hooks = plugin.manifest.normalized_hooks()
            if not hooks:
                continue
            loaded = hook_registry.load_dict({"hooks": hooks})
            if loaded:
                logger.info("plugin %s contributed %d hook(s)", plugin.name, loaded)
            total += loaded
        return total


__all__ = ["PluginRegistry"]
