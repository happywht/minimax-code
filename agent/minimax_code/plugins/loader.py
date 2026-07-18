"""Plugin loader — discovers plugin manifests on disk.

A plugin is any directory containing a manifest file (default
``plugin.json``). The loader walks a root recursively, parses each
manifest, and returns :class:`Plugin` records. Loading is fail-open:
an unreadable or invalid manifest yields a :class:`Plugin` with its
``error`` field set rather than raising, so one broken plugin never
blocks discovery of the rest.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .manifest import PluginManifest

logger = logging.getLogger(__name__)

DEFAULT_MANIFEST_NAME = "plugin.json"


@dataclass
class Plugin:
    """A discovered plugin: its manifest, disk path, and load status."""

    manifest: PluginManifest
    path: Path
    loaded_at: str = ""
    error: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def name(self) -> str:
        return self.manifest.name


class PluginLoader:
    """Discovers plugins under one or more roots."""

    def __init__(self, manifest_name: str = DEFAULT_MANIFEST_NAME) -> None:
        self.manifest_name = manifest_name

    def discover(self, root: str | Path) -> list[Plugin]:
        """Recursively find all manifests under ``root`` (sorted, fail-open)."""
        root_path = Path(root)
        if not root_path.exists():
            return []
        found: list[Plugin] = []
        for path in sorted(root_path.rglob(self.manifest_name)):
            found.append(self._load_one(path))
        return found

    def discover_many(self, roots: list[str | Path]) -> list[Plugin]:
        plugins: list[Plugin] = []
        seen: set[str] = set()
        for root in roots:
            for plugin in self.discover(root):
                key = str(plugin.path.resolve())
                if key in seen:
                    continue
                seen.add(key)
                plugins.append(plugin)
        return plugins

    def load_dir(self, directory: str | Path) -> Plugin | None:
        """Load the single manifest directly inside ``directory``."""
        path = Path(directory) / self.manifest_name
        if not path.exists():
            return None
        return self._load_one(path)

    def load_file(self, path: str | Path) -> Plugin:
        return self._load_one(Path(path))

    # -- internal -----------------------------------------------------------

    def _load_one(self, path: Path) -> Plugin:
        dir_name = path.parent.name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("plugin manifest %s unreadable: %s", path, exc)
            return Plugin(
                manifest=PluginManifest(name=dir_name),
                path=path,
                error=f"unreadable manifest: {exc}",
            )
        if not isinstance(data, dict):
            return Plugin(
                manifest=PluginManifest(name=dir_name),
                path=path,
                error="manifest root is not an object",
            )
        name = str(data.get("name") or dir_name)
        try:
            manifest = PluginManifest.model_validate(data)
        except Exception as exc:  # noqa: BLE001 — fail-open per spec
            logger.warning("plugin manifest %s invalid: %s", path, exc)
            return Plugin(
                manifest=PluginManifest(name=name),
                path=path,
                error=f"invalid manifest: {exc}",
            )
        return Plugin(
            manifest=manifest,
            path=path,
            loaded_at=datetime.now().isoformat(timespec="seconds"),
        )


__all__ = ["DEFAULT_MANIFEST_NAME", "Plugin", "PluginLoader"]
