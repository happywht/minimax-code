"""Unit tests for the plugins subsystem (R8): manifest parsing, directory
discovery (fail-open), registry indexing, and hook contribution.
"""

from __future__ import annotations

import json
from pathlib import Path

from minimax_code.hooks import HookEvent, HookRegistry
from minimax_code.plugins import (
    Plugin,
    PluginLoader,
    PluginManifest,
    PluginRegistry,
)


def _write_manifest(dir_path: Path, data: dict, name: str = "plugin.json") -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    manifest = dir_path / name
    manifest.write_text(json.dumps(data), encoding="utf-8")
    return manifest


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------


def test_manifest_minimal_defaults() -> None:
    m = PluginManifest(name="p")
    assert m.version == "0.0.0"
    assert m.enabled is True
    assert m.hooks is None
    assert m.normalized_hooks() == {}


def test_manifest_extra_keys_allowed() -> None:
    # Forward-compat: unknown keys must not break parsing.
    m = PluginManifest.model_validate({"name": "p", "license": "MIT", "x": 1})
    assert m.name == "p"


# ---------------------------------------------------------------------------
# loader
# ---------------------------------------------------------------------------


def test_loader_discovers_single_plugin(tmp_path: Path) -> None:
    _write_manifest(tmp_path / "alpha", {"name": "alpha", "version": "1.0.0"})
    plugins = PluginLoader().discover(tmp_path)
    assert len(plugins) == 1
    assert plugins[0].name == "alpha"
    assert plugins[0].manifest.version == "1.0.0"
    assert plugins[0].ok


def test_loader_discovers_nested_and_sorted(tmp_path: Path) -> None:
    _write_manifest(tmp_path / "zeta", {"name": "zeta"})
    _write_manifest(tmp_path / "alpha", {"name": "alpha"})
    _write_manifest(tmp_path / "sub" / "mid", {"name": "mid"})
    plugins = PluginLoader().discover(tmp_path)
    assert [p.name for p in plugins] == ["alpha", "mid", "zeta"]


def test_loader_invalid_json_is_fail_open(tmp_path: Path) -> None:
    bad = tmp_path / "broken" / "plugin.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("{ not valid json", encoding="utf-8")
    plugins = PluginLoader().discover(tmp_path)
    assert len(plugins) == 1
    assert plugins[0].ok is False
    assert plugins[0].error is not None
    # Falls back to directory name when name can't be read.
    assert plugins[0].name == "broken"


def test_loader_invalid_schema_records_error(tmp_path: Path) -> None:
    # Missing required `name`.
    _write_manifest(tmp_path / "noname", {"version": "1.0.0"})
    plugins = PluginLoader().discover(tmp_path)
    assert len(plugins) == 1
    assert plugins[0].ok is False


def test_loader_missing_root_returns_empty(tmp_path: Path) -> None:
    assert PluginLoader().discover(tmp_path / "does-not-exist") == []


def test_loader_load_dir_directly(tmp_path: Path) -> None:
    _write_manifest(tmp_path / "solo", {"name": "solo"})
    plugin = PluginLoader().load_dir(tmp_path / "solo")
    assert plugin is not None
    assert plugin.name == "solo"
    assert PluginLoader().load_dir(tmp_path / "empty") is None


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def _plugin(name: str, **manifest_kw: object) -> Plugin:
    return Plugin(manifest=PluginManifest(name=name, **manifest_kw), path=Path("/x"))  # type: ignore[arg-type]


def test_registry_add_dedup_and_remove() -> None:
    reg = PluginRegistry()
    assert reg.add(_plugin("a")) is True
    assert reg.add(_plugin("a")) is False  # duplicate name skipped
    assert reg.count() == 1
    assert reg.has("a")
    assert reg.remove("a")
    assert not reg.has("a")


def test_registry_add_many_returns_count() -> None:
    reg = PluginRegistry()
    n = reg.add_many([_plugin("a"), _plugin("b"), _plugin("a")])
    assert n == 2


def test_registry_failed_and_enabled_filters() -> None:
    reg = PluginRegistry()
    good = _plugin("good")
    disabled = _plugin("off", enabled=False)
    broken = Plugin(
        manifest=PluginManifest(name="broken"), path=Path("/x"), error="bad"
    )
    reg.add_many([good, disabled, broken])
    assert [p.name for p in reg.enabled()] == ["good"]
    assert [p.name for p in reg.failed()] == ["broken"]


def test_registry_apply_hooks_pours_into_hook_registry() -> None:
    reg = PluginRegistry()
    plugin = Plugin(
        manifest=PluginManifest(
            name="linter",
            hooks={
                "pre_tool_use": [
                    {"command": ["./lint.sh"], "matcher": {"tool_name": "edit_*"}}
                ],
                "session_start": [{"command": ["./hi.sh"]}],
            },
        ),
        path=Path("/x"),
    )
    reg.add(plugin)
    hooks = HookRegistry()
    loaded = reg.apply_hooks(hooks)
    assert loaded == 2
    assert hooks.count(HookEvent.PRE_TOOL_USE) == 1
    assert hooks.count(HookEvent.SESSION_START) == 1


def test_registry_apply_hooks_skips_disabled_and_empty() -> None:
    reg = PluginRegistry()
    reg.add(_plugin("empty"))  # no hooks
    reg.add(_plugin("off", enabled=True, hooks={"session_start": [{"command": ["x"]}]}))
    reg.add(_plugin("disabled", enabled=False, hooks={"session_start": [{"command": ["y"]}]}))
    hooks = HookRegistry()
    loaded = reg.apply_hooks(hooks)
    assert loaded == 1  # only "off" (enabled) contributed one hook
    assert hooks.count(HookEvent.SESSION_START) == 1
