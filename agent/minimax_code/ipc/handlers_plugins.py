"""JSON-RPC handlers for the ``plugins.*`` namespace.

The plugin registry is the third platform pillar (after MCP and Hooks).
These handlers expose it to the UI:

* ``plugins.list``    — list every discovered plugin (with load status).
* ``plugins.info``    — fetch one plugin's full info record.
* ``plugins.enable``  — flip a plugin on at runtime (in-memory override).
* ``plugins.disable`` — flip a plugin off at runtime.
* ``plugins.reload``  — re-scan the plugin roots and rebuild the index.

The registry is a synchronous object (disk discovery + in-memory index),
so the handlers resolve it via a plain callable rather than an async
factory. ``register_plugin_handlers`` accepts an optional pre-built
registry so unit tests can inject a hand-crafted one and skip discovery.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .handler_utils import HandlerError, check_params
from .protocol import INTERNAL_ERROR, NOT_FOUND
from .server import Context

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _to_info(plugin: Any) -> dict[str, Any]:
    """Flatten a :class:`Plugin` record into the JSON wire shape.

    The shape mirrors :class:`PluginInfo` on the TypeScript side and is
    forward-compatible — unknown manifest keys (kept by the permissive
    manifest model) are simply not surfaced here.
    """
    manifest = plugin.manifest
    return {
        "name": plugin.name,
        "version": manifest.version,
        "description": manifest.description,
        "author": manifest.author,
        "homepage": manifest.homepage,
        "enabled": plugin.effective_enabled,
        "enabled_on_disk": manifest.enabled,
        "ok": plugin.ok,
        "error": plugin.error,
        "path": str(plugin.path),
        "loaded_at": plugin.loaded_at,
        "has_hooks": bool(manifest.hooks),
        "has_mcp": bool(manifest.mcp_servers),
        "has_permissions": bool(manifest.permissions),
        "entry": manifest.entry,
    }


def _make_registry_factory(
    registry: Any,
) -> Callable[[], Any]:
    """Bind a registry getter into a closure.

    When ``registry`` is ``None`` the getter builds it lazily via
    :func:`minimax_code.app.ensure_plugin_registry` on first call and
    caches the result so subsequent handlers reuse the same index.
    """
    cached: list[Any] = [registry]

    def factory() -> Any:
        if cached[0] is None:
            # Imported lazily to avoid a top-level circular dependency
            # (app.py imports this module inside register_app_handlers).
            from ..app import ensure_plugin_registry

            cached[0] = ensure_plugin_registry()
        return cached[0]

    return factory


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_plugin_handlers(server: Any, *, registry: Any = None) -> None:
    """Register the ``plugins.*`` handlers on ``server``."""
    registry_getter = _make_registry_factory(registry)

    async def handle_plugins_list(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys=set())
            reg = registry_getter()
            include_failed = bool((params or {}).get("include_failed", True))
            enabled_only = bool((params or {}).get("enabled_only", False))
            items = [_to_info(p) for p in reg.list()]
            if not include_failed:
                items = [i for i in items if i["ok"]]
            if enabled_only:
                # Match registry.enabled(): a broken plugin (ok=False) is
                # never effectively active even if its manifest flag is True.
                items = [i for i in items if i["enabled"] and i["ok"]]
            await ctx.reply({"plugins": items, "total": len(items)})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("plugins.list failed")
            await ctx.reply_error(INTERNAL_ERROR, "plugins.list failed")

    async def handle_plugins_info(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys={"name"})
            reg = registry_getter()
            plugin = reg.get(params["name"])
            if plugin is None:
                await ctx.reply_error(
                    NOT_FOUND, f"plugin not found: {params['name']}"
                )
                return
            await ctx.reply({"plugin": _to_info(plugin)})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("plugins.info failed")
            await ctx.reply_error(INTERNAL_ERROR, "plugins.info failed")

    async def handle_plugins_enable(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys={"name"})
            reg = registry_getter()
            name = params["name"]
            if not reg.has(name):
                await ctx.reply_error(NOT_FOUND, f"plugin not found: {name}")
                return
            reg.set_enabled(name, True)
            await ctx.reply({"ok": True, "name": name, "enabled": True})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("plugins.enable failed")
            await ctx.reply_error(INTERNAL_ERROR, "plugins.enable failed")

    async def handle_plugins_disable(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys={"name"})
            reg = registry_getter()
            name = params["name"]
            if not reg.has(name):
                await ctx.reply_error(NOT_FOUND, f"plugin not found: {name}")
                return
            reg.set_enabled(name, False)
            await ctx.reply({"ok": True, "name": name, "enabled": False})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("plugins.disable failed")
            await ctx.reply_error(INTERNAL_ERROR, "plugins.disable failed")

    async def handle_plugins_reload(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys=set())
            reg = registry_getter()
            # Re-discover from the configured roots and rebuild the index.
            # Runtime enabled overrides are reset by design — reload means
            # "return to the on-disk truth". Fail-open discovery means a
            # broken manifest becomes an error-flagged entry, not a crash.
            from ..app import discover_plugins

            discovered = discover_plugins()
            reg.clear()
            added = reg.add_many(discovered)
            infos = [_to_info(p) for p in reg.list()]
            await ctx.reply(
                {
                    "ok": True,
                    "total": len(infos),
                    "reloaded": added,
                    "failed": sum(1 for i in infos if not i["ok"]),
                    "plugins": infos,
                }
            )
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("plugins.reload failed")
            await ctx.reply_error(INTERNAL_ERROR, "plugins.reload failed")

    server.register("plugins.list", handle_plugins_list)
    server.register("plugins.info", handle_plugins_info)
    server.register("plugins.enable", handle_plugins_enable)
    server.register("plugins.disable", handle_plugins_disable)
    server.register("plugins.reload", handle_plugins_reload)


__all__ = ["register_plugin_handlers"]
