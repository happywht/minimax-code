"""Tests for ``sampler.conversation_hosted_tools`` (R223,
``xai-grok-sampling-types`` ``conversation.rs``).

Covers the conversation-layer backend-hosted tool union -- the first
**in-program union** in the package to carry a **method** (:meth:`wire_name`).
``#[derive(Debug, Clone)]`` only (NO serde), so :class:`HostedTool` is a pure
in-program enum: it never crosses the wire itself and has no
``from_payload`` / ``as_payload`` (the wire JSON for each variant is emitted by
the :class:`ConversationRequest` serializer, not here). Two variants:
:class:`WebSearch` (a server-side web search with an optional domain
allowlist) + :class:`XSearch` (a server-side X/Twitter search, field-less).

Two patterns land together here, each already established by a prior slice:

- the **in-program-union shape** -- frozen+slots union base + field-less /
  data-carrying subclass variants, no wire round-trip -- first landed as the
  R217 :class:`DanglingToolCallReason`.
- the **base-class single-point-of-truth dispatch** -- a method on the union
  base that branches by ``isinstance`` rather than per-variant overrides --
  the same shape the R221 :class:`ContentPart.as_payload` uses. R223's
  milestone is that an in-program union carries such a method for the first
  time (:meth:`wire_name` mirrors grok's single ``match self`` in
  ``impl HostedTool``).

Distinct from the R222 :class:`ToolCall` / :class:`ToolSpec` (client-side
tool definitions -- Serialize/Deserialize flat structs the model offers /
emits); this is the *backend-side* peer (executed server-side during
inference). No barrel collision -> no ``Conversation`` prefix.
"""

from __future__ import annotations

import pytest

import minimax_code.sampler.conversation_hosted_tools as _ht
from minimax_code.sampler import HostedTool, WebSearch, XSearch


class TestBarrelReExport:
    """The sampler barrel re-exports every conversation_hosted_tools symbol by
    identity (no accidental shadowing / re-wrapping at the package surface)."""

    def test_barrel_symbols_are_direct_module_references(self) -> None:
        assert HostedTool is _ht.HostedTool
        assert WebSearch is _ht.WebSearch
        assert XSearch is _ht.XSearch


# ---------------------------------------------------------------------------
# wire_name: single-point-of-truth isinstance dispatch (mirrors grok match self).
# ---------------------------------------------------------------------------


class TestWireName:
    """``wire_name`` lives on the base and dispatches by the concrete instance
    type -- a single point of truth (mirrors grok ``match self``)."""

    def test_web_search_returns_web_search(self) -> None:
        assert WebSearch().wire_name() == "web_search"
        # The wire name is invariant under the allowlist payload.
        assert WebSearch(allowed_domains=("a.com",)).wire_name() == "web_search"

    def test_x_search_returns_x_search(self) -> None:
        assert XSearch().wire_name() == "x_search"

    def test_dispatch_is_polymorphic_through_base(self) -> None:
        # Calling wire_name through the base-class annotation dispatches by the
        # concrete instance type -- the union behaves as one polymorphic family.
        tools: list[HostedTool] = [WebSearch(), XSearch(), WebSearch(allowed_domains=())]
        assert [t.wire_name() for t in tools] == ["web_search", "x_search", "web_search"]

    def test_bare_base_hosted_tool_raises_type_error(self) -> None:
        # The base itself carries no variant information (abstract-in-practice);
        # a bare HostedTool has no wire name.
        with pytest.raises(TypeError):
            HostedTool().wire_name()


# ---------------------------------------------------------------------------
# WebSearch: allowed_domains (Option<Vec<String>> -> tuple[str, ...] | None).
# ---------------------------------------------------------------------------


class TestWebSearchAllowedDomains:
    """``allowed_domains`` defaults to ``None`` (grok ``Option::None``); a tuple
    of domain strings restricts results to those domains. ``Vec<String>`` is
    ordered so a tuple (not frozenset) preserves order."""

    def test_default_allowed_domains_is_none(self) -> None:
        assert WebSearch().allowed_domains is None

    def test_explicit_none_allowed_domains(self) -> None:
        assert WebSearch(allowed_domains=None).allowed_domains is None

    def test_tuple_allowed_domains_preserved(self) -> None:
        domains = ("example.com", "docs.example.com")
        assert WebSearch(allowed_domains=domains).allowed_domains == domains

    def test_single_domain_tuple_preserved(self) -> None:
        assert WebSearch(allowed_domains=("a.com",)).allowed_domains == ("a.com",)

    def test_empty_tuple_allowed(self) -> None:
        # An empty allowlist is a legal (if useless) value -- mirrors grok
        # Some(vec![]) (an explicit empty restriction, distinct from None).
        assert WebSearch(allowed_domains=()).allowed_domains == ()

    def test_allowed_domains_is_a_tuple_not_list(self) -> None:
        # frozen+slots -> immutable; Option<Vec<String>> -> tuple (hashable,
        # order-preserving), never a mutable list.
        result = WebSearch(allowed_domains=("a.com",)).allowed_domains
        assert isinstance(result, tuple)


# ---------------------------------------------------------------------------
# XSearch: field-less unit variant.
# ---------------------------------------------------------------------------


class TestXSearchFields:
    """``XSearch`` carries no data (grok unit variant)."""

    def test_constructible_with_no_args(self) -> None:
        assert XSearch() is not None

    def test_x_search_has_no_allowed_domains(self) -> None:
        # No data attribute beyond the (empty) base.
        assert not hasattr(XSearch(), "allowed_domains")


# ---------------------------------------------------------------------------
# Value semantics: frozen+slots -> structural equality.
# ---------------------------------------------------------------------------


class TestValueSemantics:
    """frozen dataclass -> structural equality (not identity); same variant +
    same fields -> equal; any differing field (or a different variant) -> not
    equal."""

    def test_equal_default_web_searches_equal(self) -> None:
        assert WebSearch() == WebSearch()

    def test_equal_web_searches_with_domains_equal(self) -> None:
        assert WebSearch(allowed_domains=("a",)) == WebSearch(allowed_domains=("a",))

    def test_differing_allowed_domains_not_equal(self) -> None:
        assert WebSearch(allowed_domains=("a",)) != WebSearch(allowed_domains=("b",))

    def test_none_vs_tuple_allowed_domains_not_equal(self) -> None:
        assert WebSearch(allowed_domains=("a",)) != WebSearch(allowed_domains=None)

    def test_equal_x_searches_equal(self) -> None:
        assert XSearch() == XSearch()

    def test_web_search_not_equal_to_x_search(self) -> None:
        # Different variants are never equal even with no payload.
        assert WebSearch() != XSearch()
        assert XSearch() != WebSearch()

    def test_tuple_order_matters(self) -> None:
        # Vec<String> is ordered -> a tuple (not frozenset) preserves order, so
        # reordered allowlists are distinct values.
        assert WebSearch(allowed_domains=("a.com", "b.com")) != WebSearch(
            allowed_domains=("b.com", "a.com")
        )


class TestSlotsAndImmutability:
    """frozen+slots -> no ``__dict__``; each variant carries exactly its fields
    and is immutable after construction."""

    def test_hosted_tool_slots_empty(self) -> None:
        assert set(HostedTool.__slots__) == set()

    def test_web_search_slots(self) -> None:
        assert set(WebSearch.__slots__) == {"allowed_domains"}

    def test_x_search_slots_empty(self) -> None:
        assert set(XSearch.__slots__) == set()

    def test_instances_have_no_dict(self) -> None:
        for tool in (HostedTool(), WebSearch(), XSearch()):
            assert not hasattr(tool, "__dict__")

    def test_web_search_is_frozen(self) -> None:
        # Variable attribute name keeps the B010 check honest across renames;
        # frozen rejects the assignment regardless of which field.
        ws = WebSearch(allowed_domains=("a",))
        attr = "allowed_domains"
        with pytest.raises(AttributeError):
            setattr(ws, attr, ("b",))

    def test_x_search_is_frozen(self) -> None:
        # XSearch is field-less, so we verify frozen via the dataclass params
        # rather than a setattr probe: on a field-less frozen+slots subclass
        # CPython's dataclass-generated __setattr__ hits a slots edge case for
        # an unknown attribute name, so the declarative frozen flag is the
        # honest check (frozen is inherited from HostedTool regardless).
        assert XSearch.__dataclass_params__.frozen is True


# ---------------------------------------------------------------------------
# Subclass hierarchy: variants are HostedTool instances (the union contract).
# ---------------------------------------------------------------------------


class TestSubclassHierarchy:
    """Both variants are :class:`HostedTool` instances; the base is not an
    instance of either variant."""

    def test_web_search_is_hosted_tool(self) -> None:
        assert isinstance(WebSearch(), HostedTool)

    def test_x_search_is_hosted_tool(self) -> None:
        assert isinstance(XSearch(), HostedTool)

    def test_bare_base_is_not_a_variant(self) -> None:
        base = HostedTool()
        assert not isinstance(base, WebSearch)
        assert not isinstance(base, XSearch)

    def test_variants_are_distinct_types(self) -> None:
        assert WebSearch is not XSearch
        assert not isinstance(WebSearch(), XSearch)
        assert not isinstance(XSearch(), WebSearch)
