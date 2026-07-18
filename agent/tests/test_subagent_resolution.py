"""Tests for the R22 sub-agent resolution layer + filtered registry.

Pins the three contracts the runtime depends on:

* :func:`resolve_subagent_spec` — the pure-logic priority model
  (explicit model > parent default; allowlist > ALL when granted) and
  its fail-soft intersection with the parent's actual surface;
* :class:`FilteredToolRegistry` — the read-only capability view that
  hides disallowed tools from ``get`` / ``has`` / the LLM schema and
  short-circuits :meth:`dispatch`, while refusing all writes;
* :meth:`SubAgentRuntime.build` — the integration point: a config with
  an allowlist produces a core whose registry *is* a filtered view, and
  a config without one inherits the parent registry unchanged.
"""

from __future__ import annotations

import pytest

from minimax_code.agent.tools import ToolResult
from minimax_code.orchestrator.resolution import (
    CapabilityMode,
    FilteredToolRegistry,
    ResolvedSpec,
    resolve_subagent_spec,
)
from minimax_code.orchestrator.subagent import SubAgentConfig, SubAgentRuntime

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeTool:
    """Minimal tool stand-in — only ``name`` is read by the view."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"fake {name}"
        self.parameters: dict[str, object] = {}


class _FakeRegistry:
    """Duck-typed ToolRegistry — every read method the view calls."""

    def __init__(self, names: list[str]) -> None:
        self._names = list(names)

    def get(self, name: str) -> _FakeTool:
        if name not in self._names:
            raise KeyError(name)
        return _FakeTool(name)

    def has(self, name: str) -> bool:
        return name in self._names

    def list(self) -> list[_FakeTool]:
        return [_FakeTool(n) for n in self._names]

    def names(self) -> list[str]:
        return list(self._names)

    def to_llm_functions(self) -> list[dict[str, object]]:
        return [
            {"type": "function", "function": {"name": n, "description": "", "parameters": {}}}
            for n in self._names
        ]

    def to_openai_tools(self) -> list[dict[str, object]]:
        return self.to_llm_functions()

    async def dispatch(self, name: str, args: dict[str, object]) -> ToolResult:
        if name not in self._names:
            return ToolResult.fail(f"unknown tool {name!r}")
        return ToolResult.ok(f"ran {name}")

    def register(self, tool: _FakeTool) -> _FakeTool:
        self._names.append(tool.name)
        return tool

    def unregister(self, name: str) -> None:
        self._names.remove(name)

    def clear(self) -> None:
        self._names.clear()


# ---------------------------------------------------------------------------
# resolve_subagent_spec — priority model
# ---------------------------------------------------------------------------


def test_no_allowlist_inherits_all_tools_and_parent_model() -> None:
    """An unset allowlist means ALL mode: every parent tool is allowed."""

    config = SubAgentConfig(name="ra", system_prompt="")
    spec = resolve_subagent_spec(
        config, available_tool_names=["read", "edit", "search"]
    )
    assert spec.capability_mode is CapabilityMode.ALL
    assert spec.is_restricted is False
    assert spec.allowed_tools == ["read", "edit", "search"]
    # model falls back to the parent default.
    assert spec.model == "MiniMax-M3"


def test_explicit_model_overrides_parent_default() -> None:
    """config.model wins over the parent_model argument."""

    config = SubAgentConfig(name="ra", system_prompt="", model="MiniMax-M2")
    spec = resolve_subagent_spec(
        config, available_tool_names=["read"], parent_model="MiniMax-M3"
    )
    assert spec.model == "MiniMax-M2"


def test_allowlist_switches_to_allowlist_mode() -> None:
    """A granted allowlist => ALLOWLIST mode + intersection with the surface."""

    config = SubAgentConfig(name="ra", system_prompt="", tool_allowlist=["read"])
    spec = resolve_subagent_spec(
        config, available_tool_names=["read", "edit", "search"]
    )
    assert spec.capability_mode is CapabilityMode.ALLOWLIST
    assert spec.is_restricted is True
    assert spec.allowed_tools == ["read"]


def test_allowlist_drops_unknown_names_fail_soft() -> None:
    """A typo'd / version-skewed allowlist entry is silently dropped, not fatal.

    Mirrors grok's non-fatal misconfiguration policy — the resolver never
    aborts the spawn over an unknown tool name; it just narrows the surface
    to what actually exists.
    """

    config = SubAgentConfig(
        name="ra", system_prompt="", tool_allowlist=["read", "ghost", "typo_tool"]
    )
    spec = resolve_subagent_spec(
        config, available_tool_names=["read", "edit"]
    )
    assert spec.capability_mode is CapabilityMode.ALLOWLIST
    assert spec.allowed_tools == ["read"]  # ghost + typo_tool dropped


def test_allowlist_preserves_caller_order_not_registry_order() -> None:
    """allowed_tools follows the allowlist order (caller intent)."""

    config = SubAgentConfig(
        name="ra", system_prompt="", tool_allowlist=["search", "read"]
    )
    spec = resolve_subagent_spec(
        config, available_tool_names=["read", "edit", "search"]
    )
    assert spec.allowed_tools == ["search", "read"]


def test_resolved_spec_is_frozen() -> None:
    """ResolvedSpec is hashable/immutable so it can't be mutated mid-spawn."""

    spec = resolve_subagent_spec(
        SubAgentConfig(name="ra"), available_tool_names=["read"]
    )
    assert isinstance(spec, ResolvedSpec)
    # FrozenInstanceError subclasses AttributeError on every Python version,
    # so the narrower base type is the correct assertion target.
    with pytest.raises(AttributeError):  # frozen dataclass rejects mutation
        spec.model = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# FilteredToolRegistry — read filtering
# ---------------------------------------------------------------------------


def test_filtered_get_returns_allowed_and_hides_restricted() -> None:
    """get() on a restricted tool raises KeyError — it "doesn't exist"."""

    base = _FakeRegistry(["read", "edit", "search"])
    view = FilteredToolRegistry(base, ["read"])
    assert view.get("read").name == "read"
    with pytest.raises(KeyError):
        view.get("edit")
    with pytest.raises(KeyError):
        view.get("never-existed")


def test_filtered_has_only_true_for_allowed_and_present() -> None:
    base = _FakeRegistry(["read", "edit"])
    view = FilteredToolRegistry(base, ["read", "ghost"])
    assert view.has("read") is True
    # allowed in the view but missing from base → False
    assert view.has("ghost") is False
    # present in base but not allowed → False
    assert view.has("edit") is False


def test_filtered_list_and_names_exclude_restricted() -> None:
    base = _FakeRegistry(["read", "edit", "search", "terminal"])
    view = FilteredToolRegistry(base, ["read", "search"])
    assert [t.name for t in view.list()] == ["read", "search"]
    assert view.names() == ["read", "search"]


def test_filtered_llm_schema_omits_restricted_signatures() -> None:
    """The model never sees a restricted tool's signature — the surest gate."""

    base = _FakeRegistry(["read", "edit", "terminal"])
    view = FilteredToolRegistry(base, ["read"])
    schema = view.to_llm_functions()
    assert [f["function"]["name"] for f in schema] == ["read"]
    # Alias agrees.
    assert view.to_openai_tools() == schema


@pytest.mark.asyncio
async def test_filtered_dispatch_blocks_restricted_tool() -> None:
    """A hand-constructed call to a restricted tool is short-circuited to fail."""

    base = _FakeRegistry(["read", "edit"])
    view = FilteredToolRegistry(base, ["read"])
    blocked = await view.dispatch("edit", {"path": "/etc/passwd"})
    assert blocked.success is False
    assert "allowlist" in blocked.error
    # Allowed tool delegates to the base.
    ok = await view.dispatch("read", {"path": "x"})
    assert ok.success is True


@pytest.mark.asyncio
async def test_filtered_dispatch_delegates_to_base_for_allowed() -> None:
    base = _FakeRegistry(["read"])
    view = FilteredToolRegistry(base, ["read"])
    res = await view.dispatch("read", {})
    assert res.success is True
    assert res.output == "ran read"


# ---------------------------------------------------------------------------
# FilteredToolRegistry — read-only contract
# ---------------------------------------------------------------------------


def test_filtered_register_raises_not_implemented() -> None:
    view = FilteredToolRegistry(_FakeRegistry(["read"]), ["read"])
    with pytest.raises(NotImplementedError):
        view.register(_FakeTool("edit"))


def test_filtered_unregister_raises_not_implemented() -> None:
    view = FilteredToolRegistry(_FakeRegistry(["read"]), ["read"])
    with pytest.raises(NotImplementedError):
        view.unregister("read")


def test_filtered_clear_raises_not_implemented() -> None:
    view = FilteredToolRegistry(_FakeRegistry(["read"]), ["read"])
    with pytest.raises(NotImplementedError):
        view.clear()


def test_filtered_base_and_allowed_exposed() -> None:
    """base + allowed are exposed for tests / operator introspection."""

    base = _FakeRegistry(["read", "edit"])
    view = FilteredToolRegistry(base, ["read"])
    assert view.base is base
    assert view.allowed == frozenset({"read"})


# ---------------------------------------------------------------------------
# SubAgentRuntime.build — integration
# ---------------------------------------------------------------------------


def test_build_with_allowlist_wraps_registry_in_filtered_view() -> None:
    """R22 end-to-end: a config with an allowlist yields a filtered-registry core."""

    runtime = SubAgentRuntime()
    base = _FakeRegistry(["read", "edit", "search", "terminal"])
    config = SubAgentConfig(
        name="readonly",
        system_prompt="you are read-only",
        tool_allowlist=["read", "search"],
        model="MiniMax-M2",
    )
    handle = runtime.build(config, registry=base)

    # The core's registry is the filtered view, not the bare base.
    reg = handle.core.registry
    assert isinstance(reg, FilteredToolRegistry)
    assert reg.base is base
    assert reg.allowed == frozenset({"read", "search"})
    # Restricted tool is hidden.
    assert reg.has("terminal") is False
    assert reg.names() == ["read", "search"]
    # Effective model came through the resolver.
    assert handle.core.config.model == "MiniMax-M2"


def test_build_without_allowlist_inherits_parent_registry_unchanged() -> None:
    """No allowlist => ALL mode => the base registry passes through verbatim."""

    runtime = SubAgentRuntime()
    base = _FakeRegistry(["read", "edit", "search"])
    config = SubAgentConfig(name="full", system_prompt="")
    handle = runtime.build(config, registry=base)

    reg = handle.core.registry
    # Not wrapped — the sub-agent sees the parent's full surface.
    assert reg is base
    assert reg.names() == ["read", "edit", "search"]
    # Parent default model applied.
    assert handle.core.config.model == "MiniMax-M3"


def test_build_registry_none_uses_global_default_as_parent() -> None:
    """When the caller passes no registry, the global default is the parent surface.

    This is the path every handler takes today (they call ``runtime.build(config)``
    with no registry kwarg). The resolver must still see a real surface to
    intersect against, so build() falls back to ``get_default_registry()``.
    """

    runtime = SubAgentRuntime()
    config = SubAgentConfig(name="x", system_prompt="")
    handle = runtime.build(config)  # registry=None
    # No allowlist → ALL mode → whatever the global default is passes through.
    # We don't assert specific names (global is process-shared) — only that a
    # working registry was wired and the core was built.
    assert handle.core is not None
    assert handle.core.registry is not None
    assert handle.core.config.model == "MiniMax-M3"


def test_build_empty_allowlist_treated_as_all_mode() -> None:
    """An empty allowlist (None or []) is ALL mode, not 'zero tools'.

    Prevents a footgun where a config with ``tool_allowlist=[]`` would
    otherwise lock the sub-agent out of every tool.
    """

    runtime = SubAgentRuntime()
    base = _FakeRegistry(["read", "edit"])
    config = SubAgentConfig(name="x", system_prompt="", tool_allowlist=[])
    handle = runtime.build(config, registry=base)
    # Empty allowlist → ALL → base inherited unchanged.
    assert handle.core.registry is base
