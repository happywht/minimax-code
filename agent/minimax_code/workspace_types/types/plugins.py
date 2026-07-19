"""Plugin / hook discovery shapes (R67).

Fusion of grok-build's ``xai-grok-workspace-types::types::plugins``.

Surfaced by ``OpsChunk::Plugins`` / ``OpsChunk::Plugin`` and the
``WorkspaceEvent::PluginsChanged`` / ``HooksChanged`` events.

``source`` is a free-form string (``"global"`` / ``"workspace"`` /
``"marketplace"``) and ``HookInfo.event`` is likewise a free-form string
(``"PreToolUse"``) — both are placeholder strings in the source crate,
not typed enums (TODO markers flag the future alignment with
``xai_hooks_plugins_types::HookEvent``).
"""

from __future__ import annotations

from minimax_code.workspace_types._wire import WireModel

__all__ = ["PluginInfo", "HookInfo"]


class PluginInfo(WireModel):
    """Plugin metadata.

    ``source`` is a free-form bucket label (``"global"`` /
    ``"workspace"`` / ``"marketplace"``).
    """

    id: str
    name: str = ""
    version: str = ""
    path: str = ""
    source: str = ""
    enabled: bool = False

    @classmethod
    def default(cls) -> PluginInfo:
        return cls(id="")


class HookInfo(WireModel):
    """Hook metadata.

    ``event`` is a free-form event label (``"PreToolUse"``);
    ``plugin_id`` is ``None`` for non-plugin hooks.
    """

    id: str
    name: str = ""
    event: str = ""
    plugin_id: str | None = None
    enabled: bool = False

    @classmethod
    def default(cls) -> HookInfo:
        return cls(id="")
