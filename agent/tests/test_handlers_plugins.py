"""Tests for the ``plugins.*`` IPC handlers (platform pillar #3).

A hand-crafted :class:`PluginRegistry` is injected so the handlers can
be exercised without touching the disk. We assert:

* ``plugins.list`` returns every record (with ``ok`` / ``enabled`` flags)
  and honors ``include_failed`` / ``enabled_only`` filters.
* ``plugins.info`` returns one plugin, or ``NOT_FOUND`` when unknown.
* ``plugins.enable`` / ``plugins.disable`` flip the runtime override
  (``effective_enabled``), and return ``NOT_FOUND`` for unknown names.
* ``plugins.reload`` re-discovers via :func:`discover_plugins` and
  rebuilds the in-memory index.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from minimax_code.ipc.handlers_plugins import register_plugin_handlers
from minimax_code.ipc.protocol import NOT_FOUND
from minimax_code.ipc.server import IPCServer
from minimax_code.plugins import Plugin, PluginManifest, PluginRegistry

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _CapturedReply:
    """Stand-in for :class:`Context` — captures reply / error for assertion."""

    def __init__(self) -> None:
        self.reply_value: dict[str, Any] | None = None
        self.error_value: dict[str, Any] | None = None

    async def reply(self, value: Any) -> None:
        self.reply_value = value

    async def reply_error(self, code: int, message: str, data: Any = None) -> None:
        self.error_value = {"code": code, "message": message, "data": data}

    async def emit(self, event: str, data: Any) -> None:  # pragma: no cover — unused
        return None


def _build_registry() -> PluginRegistry:
    """Three plugins: one healthy+enabled, one healthy+disabled, one broken."""
    reg = PluginRegistry()
    reg.add(
        Plugin(
            manifest=PluginManifest(
                name="alpha", version="1.0.0", description="d", author="a"
            ),
            path=Path("/x/alpha"),
        )
    )
    reg.add(
        Plugin(
            manifest=PluginManifest(name="off", version="2.0.0", enabled=False),
            path=Path("/x/off"),
        )
    )
    reg.add(
        Plugin(
            manifest=PluginManifest(name="broken"),
            path=Path("/x/broken"),
            error="invalid manifest",
        )
    )
    return reg


@pytest.fixture
def handlers() -> dict[str, Any]:
    """Register the plugin handlers with an injected registry."""
    from minimax_code.config import Config

    reg = _build_registry()
    server = IPCServer(
        config=Config.from_env(),
        stdin=io.StringIO(),
        stdout=io.StringIO(),
    )
    register_plugin_handlers(server, registry=reg)
    captured: dict[str, Any] = {
        "registry": reg,
        "list": server._handlers.get("plugins.list"),
        "info": server._handlers.get("plugins.info"),
        "enable": server._handlers.get("plugins.enable"),
        "disable": server._handlers.get("plugins.disable"),
        "reload": server._handlers.get("plugins.reload"),
    }
    for name in ("list", "info", "enable", "disable", "reload"):
        assert captured[name] is not None, f"plugins.{name} not registered"
    return captured


# ---------------------------------------------------------------------------
# plugins.list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_returns_all_with_status(handlers: dict[str, Any]) -> None:
    ctx = _CapturedReply()
    await handlers["list"]({}, ctx)
    assert ctx.error_value is None
    result = ctx.reply_value
    names = [p["name"] for p in result["plugins"]]
    assert names == ["alpha", "off", "broken"]
    assert result["total"] == 3
    by_name = {p["name"]: p for p in result["plugins"]}
    assert by_name["alpha"]["enabled"] is True
    assert by_name["alpha"]["enabled_on_disk"] is True
    assert by_name["off"]["enabled"] is False
    assert by_name["broken"]["ok"] is False
    assert by_name["broken"]["error"] == "invalid manifest"


@pytest.mark.asyncio
async def test_list_filters_failed_and_enabled(handlers: dict[str, Any]) -> None:
    ctx = _CapturedReply()
    await handlers["list"]({"include_failed": False}, ctx)
    assert [p["name"] for p in ctx.reply_value["plugins"]] == ["alpha", "off"]

    ctx2 = _CapturedReply()
    await handlers["list"]({"enabled_only": True}, ctx2)
    assert [p["name"] for p in ctx2.reply_value["plugins"]] == ["alpha"]


# ---------------------------------------------------------------------------
# plugins.info
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_info_known_returns_plugin(handlers: dict[str, Any]) -> None:
    ctx = _CapturedReply()
    await handlers["info"]({"name": "alpha"}, ctx)
    assert ctx.reply_value["plugin"]["name"] == "alpha"
    assert ctx.reply_value["plugin"]["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_info_unknown_returns_not_found(handlers: dict[str, Any]) -> None:
    ctx = _CapturedReply()
    await handlers["info"]({"name": "ghost"}, ctx)
    assert ctx.error_value["code"] == NOT_FOUND


# ---------------------------------------------------------------------------
# plugins.enable / plugins.disable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enable_disable_toggles_runtime_override(
    handlers: dict[str, Any],
) -> None:
    reg: PluginRegistry = handlers["registry"]
    # alpha starts enabled (manifest default); disable it at runtime.
    ctx = _CapturedReply()
    await handlers["disable"]({"name": "alpha"}, ctx)
    assert ctx.reply_value == {"ok": True, "name": "alpha", "enabled": False}
    assert reg.get("alpha").effective_enabled is False
    # on-disk flag untouched — override is in-memory only
    assert reg.get("alpha").manifest.enabled is True

    # off starts disabled; enabling flips the override to True.
    ctx2 = _CapturedReply()
    await handlers["enable"]({"name": "off"}, ctx2)
    assert ctx2.reply_value == {"ok": True, "name": "off", "enabled": True}
    assert reg.get("off").effective_enabled is True
    assert reg.get("off").manifest.enabled is False


@pytest.mark.asyncio
async def test_enable_unknown_returns_not_found(handlers: dict[str, Any]) -> None:
    ctx = _CapturedReply()
    await handlers["enable"]({"name": "ghost"}, ctx)
    assert ctx.error_value["code"] == NOT_FOUND


# ---------------------------------------------------------------------------
# plugins.reload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reload_rebuilds_index_from_discover(
    handlers: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from minimax_code import app

    def fake_discover() -> list[Any]:
        return [
            Plugin(manifest=PluginManifest(name="fresh"), path=Path("/x/fresh"))
        ]

    monkeypatch.setattr(app, "discover_plugins", fake_discover)

    ctx = _CapturedReply()
    await handlers["reload"]({}, ctx)
    result = ctx.reply_value
    assert result["ok"] is True
    assert result["total"] == 1
    assert result["reloaded"] == 1
    assert [p["name"] for p in result["plugins"]] == ["fresh"]
    # The in-memory index was rebuilt: old names gone, new name present.
    reg: PluginRegistry = handlers["registry"]
    assert reg.has("fresh")
    assert not reg.has("alpha")
