"""Tests for ``computer_hub_sdk.harness`` (R166, harness.rs leaf 2).

Covers :class:`LocalRegistry` (in-process tool registry) and
:class:`DynToolAdapter` (``ToolDyn`` -> ``ToolHandle`` adapter). The
``ToolHarness`` / ``ToolHarnessBuilder`` actor lands in a later leaf.

Registration model (mirrors Rust ``harness.rs``):
* ``register`` / ``register_arc`` take a **typed Tool** and erase it into an
  :class:`ErasedTool` before storage -- so ``find`` returns an ErasedTool,
  not the original tool. There is no Rust API to insert a bare ToolHandle.
* ``register_dyn`` takes a :class:`ToolDyn` and wraps it in a
  :class:`DynToolAdapter`.
* ``register_alias`` copies the stored ErasedTool reference (handle identity
  is preserved) plus any registered extractor.
"""

from __future__ import annotations

from typing import Any

from minimax_code.computer_hub_core.resolver import ErasedTool, ToolHandle
from minimax_code.computer_hub_sdk.harness import DynToolAdapter, LocalRegistry
from minimax_code.tool_protocol.capabilities import ToolCapabilities
from minimax_code.tool_protocol.ids import ToolId
from minimax_code.tool_runtime.context import ListToolsContext, ToolCallContext
from minimax_code.tool_runtime.tool import ContentBlock, default_capabilities
from minimax_code.tool_types.types import ToolDescription


# ===========================================================================
# Stubs.
# ===========================================================================
class _FakeTool:
    """Typed ``Tool`` stub; ``register`` / ``register_arc`` erase it via
    :class:`ErasedTool` before storage."""

    def __init__(self, name: str, *, should: bool = True) -> None:
        self._name = name
        self._should = should

    def id(self) -> ToolId:
        return ToolId(self._name)

    def description(self, ctx: ListToolsContext) -> ToolDescription:
        return ToolDescription.new(self._name, "typed")

    def capabilities(self) -> ToolCapabilities:
        return default_capabilities()

    def should_list(self, ctx: ListToolsContext) -> bool:
        return self._should

    async def execute(self, ctx: ToolCallContext, args: Any) -> Any:
        raise NotImplementedError("registry tests never execute")


class _FakeToolDyn:
    """Minimal ``ToolDyn`` (id/description/execute only) -- exercises the
    ``getattr`` default-body fallback in ``DynToolAdapter``."""

    def __init__(self, name: str) -> None:
        self._name = name

    def id(self) -> ToolId:
        return ToolId(self._name)

    def description(self, ctx: ListToolsContext) -> ToolDescription:
        return ToolDescription.new(self._name, "dyn")

    async def execute(self, ctx: ToolCallContext, args: Any) -> Any:
        raise NotImplementedError("adapter tests never execute")


class _FullToolDyn(_FakeToolDyn):
    """``ToolDyn`` with explicit ``capabilities`` / ``should_list`` -- exercises
    the delegation branch of ``DynToolAdapter``."""

    def __init__(
        self,
        name: str,
        *,
        caps: ToolCapabilities,
        should: bool,
    ) -> None:
        super().__init__(name)
        self._caps = caps
        self._should = should

    def capabilities(self) -> ToolCapabilities:
        return self._caps

    def should_list(self, ctx: ListToolsContext) -> bool:
        return self._should


def _stub_extractor(output: Any) -> list[ContentBlock] | None:
    """Extractor stub echoing the output as a single-block list."""
    return [{"type": "text", "text": str(output)}]  # type: ignore[list-item]


# ===========================================================================
# LocalRegistry -- registration (typed Tool -> ErasedTool).
# ===========================================================================
def test_local_registry_register_returns_displaced_handle():
    """register erases each Tool into its own ErasedTool; re-registering an id
    returns the previous ErasedTool (handle identity preserved)."""
    reg = LocalRegistry()
    assert reg.register(_FakeTool("alpha")) is None
    first_handle = reg.find(ToolId("alpha"))
    assert first_handle is not None
    displaced = reg.register(_FakeTool("alpha"))
    # The displaced entry is the very ErasedTool previously stored.
    assert displaced is first_handle
    # The live entry is a fresh ErasedTool wrapping the second tool.
    assert reg.find(ToolId("alpha")) is not displaced
    assert reg.find(ToolId("alpha")).id() == ToolId("alpha")  # type: ignore[union-attr]


def test_local_registry_register_arc_equivalent_to_register():
    """register_arc wraps a typed Tool via ErasedTool and stores it."""
    reg = LocalRegistry()
    assert reg.register_arc(_FakeTool("beta")) is None
    found = reg.find(ToolId("beta"))
    assert isinstance(found, ErasedTool)
    assert found.id() == ToolId("beta")


# ===========================================================================
# LocalRegistry -- lookup.
# ===========================================================================
def test_local_registry_find_hit_and_miss():
    reg = LocalRegistry()
    reg.register(_FakeTool("alpha"))
    found = reg.find(ToolId("alpha"))
    assert isinstance(found, ToolHandle)
    assert reg.find(ToolId("missing")) is None


def test_local_registry_contains_and_unregister():
    reg = LocalRegistry()
    reg.register(_FakeTool("alpha"))
    assert reg.contains(ToolId("alpha")) is True
    assert reg.contains(ToolId("missing")) is False
    assert reg.unregister(ToolId("alpha")) is True
    assert reg.unregister(ToolId("alpha")) is False  # already gone
    assert reg.contains(ToolId("alpha")) is False


def test_local_registry_len_and_is_empty():
    reg = LocalRegistry()
    assert reg.is_empty() is True
    assert len(reg) == 0
    reg.register(_FakeTool("alpha"))
    reg.register(_FakeTool("beta"))
    assert reg.is_empty() is False
    assert len(reg) == 2


# ===========================================================================
# LocalRegistry -- alias + extractor.
# ===========================================================================
def test_local_registry_register_alias_copies_handle_and_extractor():
    """An alias resolves to the same ErasedTool reference and inherits the
    extractor (handle identity preserved across the alias)."""
    reg = LocalRegistry()
    reg.register(_FakeTool("ns:real"))
    target_handle = reg.find(ToolId("ns:real"))
    reg.register_extractor(ToolId("ns:real"), _stub_extractor)

    created = reg.register_alias(ToolId("alias"), ToolId("ns:real"))
    assert created is True
    # Same ErasedTool object copied into the alias slot.
    assert reg.find(ToolId("alias")) is target_handle
    # The extractor is copied so model_output works through the alias.
    assert reg.model_output(ToolId("alias"), 42) == [
        {"type": "text", "text": "42"}
    ]


def test_local_registry_register_alias_missing_target_returns_false():
    reg = LocalRegistry()
    created = reg.register_alias(ToolId("alias"), ToolId("ns:absent"))
    assert created is False
    assert reg.find(ToolId("alias")) is None


def test_local_registry_register_extractor_and_model_output():
    reg = LocalRegistry()
    reg.register(_FakeTool("alpha"))
    reg.register_extractor(ToolId("alpha"), _stub_extractor)
    assert reg.model_output(ToolId("alpha"), 7) == [
        {"type": "text", "text": "7"}
    ]


def test_local_registry_model_output_no_extractor_returns_none():
    reg = LocalRegistry()
    reg.register(_FakeTool("alpha"))
    assert reg.model_output(ToolId("alpha"), 7) is None
    assert reg.model_output(ToolId("missing"), 7) is None


# ===========================================================================
# LocalRegistry -- listing.
# ===========================================================================
def test_local_registry_list_tools_filters_should_list_preserves_order():
    """list_tools drops should_list=False entries and keeps insertion order."""
    reg = LocalRegistry()
    reg.register(_FakeTool("alpha", should=True))
    reg.register(_FakeTool("beta", should=False))
    reg.register(_FakeTool("gamma", should=True))
    descs = reg.list_tools(None)  # type: ignore[arg-type]
    assert [d.name for d in descs] == ["alpha", "gamma"]


# ===========================================================================
# LocalRegistry -- register_dyn.
# ===========================================================================
def test_local_registry_register_dyn_wraps_dyn_tool_adapter():
    """register_dyn stores a DynToolAdapter and returns displaced handles."""
    reg = LocalRegistry()
    assert reg.register_dyn(
        _FullToolDyn("delta", caps=default_capabilities(), should=True)
    ) is None
    found = reg.find(ToolId("delta"))
    assert isinstance(found, DynToolAdapter)
    # Re-register returns the previous adapter.
    displaced = reg.register_dyn(
        _FullToolDyn("delta", caps=default_capabilities(), should=True)
    )
    assert isinstance(displaced, DynToolAdapter)


# ===========================================================================
# DynToolAdapter -- delegation.
# ===========================================================================
def test_dyn_tool_adapter_delegates_id_and_description():
    adapter = DynToolAdapter(_FakeToolDyn("alpha"))
    assert adapter.id() == ToolId("alpha")
    desc = adapter.description(None)  # type: ignore[arg-type]
    assert desc.name == "alpha"


def test_dyn_tool_adapter_capabilities_uses_default_when_missing():
    """A ToolDyn without capabilities falls back to default_capabilities()."""
    adapter = DynToolAdapter(_FakeToolDyn("alpha"))
    assert adapter.capabilities() == default_capabilities()


def test_dyn_tool_adapter_capabilities_delegates_when_present():
    custom = ToolCapabilities()  # non-default instance to test identity
    adapter = DynToolAdapter(_FullToolDyn("alpha", caps=custom, should=True))
    assert adapter.capabilities() is custom


def test_dyn_tool_adapter_should_list_uses_default_when_missing():
    adapter = DynToolAdapter(_FakeToolDyn("alpha"))
    assert adapter.should_list(None) is True  # type: ignore[arg-type]


def test_dyn_tool_adapter_should_list_delegates_when_present():
    adapter = DynToolAdapter(
        _FullToolDyn("alpha", caps=default_capabilities(), should=False)
    )
    assert adapter.should_list(None) is False  # type: ignore[arg-type]


def test_dyn_tool_adapter_repr_carries_id():
    adapter = DynToolAdapter(_FakeToolDyn("alpha"))
    text = repr(adapter)
    assert "DynToolAdapter" in text
    assert "alpha" in text
