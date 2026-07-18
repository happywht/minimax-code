"""Platform integration tests (R10) — the three pillars wired end-to-end.

R1-R9 built MCP, Hooks, and Plugins as separate subsystems. R10 connects
them: ``ensure_hook_manager`` builds one :class:`HookManager` and pours
every *enabled* plugin's hooks into it, so a plugin's ``plugin.json``
hooks become agent-active. The main agent (``agent.send_message``)
reuses that singleton and fires ``session_start`` / ``session_end``
around each turn.

These tests exercise the full chain with **real subprocess hooks**
(``sys.executable`` running inline Python) so we cover discovery →
manifest parse → registry apply → subprocess execution → decision parse,
not just in-memory wiring.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from minimax_code import app
from minimax_code.hooks.types import HookEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_plugin(
    root: Path, name: str, *, hooks: dict | None = None, raw: str | None = None
) -> Path:
    """Write a plugin directory with a ``plugin.json`` under ``root``."""
    pdir = root / name
    pdir.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        (pdir / "plugin.json").write_text(raw, encoding="utf-8")
        return pdir
    manifest: dict = {"name": name, "version": "1.0.0"}
    if hooks is not None:
        manifest["hooks"] = hooks
    (pdir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    return pdir


@pytest.fixture(autouse=True)
def _reset_platform_singletons():
    """Reset the process-wide hook manager + plugin registry per test.

    Both singletons cache aggressively; without reset the first test's
    plugin root would leak into the rest.
    """
    app.set_hook_manager(None)
    app.set_plugin_registry(None)
    yield
    app.set_hook_manager(None)
    app.set_plugin_registry(None)


# ---------------------------------------------------------------------------
# 1. The integration seam: plugin hooks → HookManager registry
# ---------------------------------------------------------------------------


def test_ensure_hook_manager_applies_plugin_hooks(tmp_path, monkeypatch):
    """A plugin's pre_tool_use hook lands in the manager's registry."""
    _write_plugin(
        tmp_path,
        "blocker",
        hooks={"pre_tool_use": [{"command": [sys.executable, "-c", "print('ok')"]}]},
    )
    monkeypatch.setenv("MINIMAX_CODE_PLUGINS_DIR", str(tmp_path))

    hm = app.ensure_hook_manager()

    assert hm.registry.count(HookEvent.PRE_TOOL_USE) == 1
    assert len(hm.registry.for_event(HookEvent.PRE_TOOL_USE)) == 1


# ---------------------------------------------------------------------------
# 2. End-to-end decision loop: plugin hook → subprocess → block verdict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plugin_pre_tool_use_hook_returns_block_decision(tmp_path, monkeypatch):
    """A plugin block decision propagates through fire_pre_tool_use."""
    decision = json.dumps({"block": True, "block_reason": "plugin blocked"})
    _write_plugin(
        tmp_path,
        "blocker",
        hooks={
            "pre_tool_use": [
                {
                    "command": [sys.executable, "-c", f"print({decision!r})"],
                    "timeout": 5.0,
                }
            ]
        },
    )
    monkeypatch.setenv("MINIMAX_CODE_PLUGINS_DIR", str(tmp_path))

    hm = app.ensure_hook_manager()
    outcome = await hm.fire_pre_tool_use("sess-1", "read_file", {"path": "/x"})

    assert outcome.blocked is True
    assert outcome.block_reason == "plugin blocked"
    assert outcome.should_run is False
    assert len(outcome.results) == 1
    assert outcome.results[0].ok is True


# ---------------------------------------------------------------------------
# 3. Session lifecycle: session_start hook fires a real subprocess
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_start_hook_fires_from_plugin(tmp_path, monkeypatch):
    """A plugin session_start hook runs and its side effect is observable."""
    marker = tmp_path / "started.marker"
    _write_plugin(
        tmp_path,
        "lifecycle",
        hooks={
            "session_start": [
                {
                    "command": [
                        sys.executable,
                        "-c",
                        "import os; open(os.environ['R10_MARKER'], 'w').write('started')",
                    ],
                    "env": {"R10_MARKER": str(marker)},
                    "timeout": 5.0,
                }
            ]
        },
    )
    monkeypatch.setenv("MINIMAX_CODE_PLUGINS_DIR", str(tmp_path))

    hm = app.ensure_hook_manager()
    results = await hm.fire_session_start("sess-1")

    assert len(results) == 1
    assert results[0].ok is True
    assert marker.read_text(encoding="utf-8") == "started"


# ---------------------------------------------------------------------------
# 4. Fail-open: a broken manifest never breaks the manager
# ---------------------------------------------------------------------------


def test_ensure_hook_manager_failopen_on_broken_plugin(tmp_path, monkeypatch):
    """An unreadable manifest yields an error-flagged plugin, not a crash."""
    _write_plugin(tmp_path, "broken", raw="{ not valid json")
    monkeypatch.setenv("MINIMAX_CODE_PLUGINS_DIR", str(tmp_path))

    hm = app.ensure_hook_manager()

    # Manager is healthy; the broken plugin simply contributed nothing.
    assert hm is not None
    assert hm.registry.count() == 0
