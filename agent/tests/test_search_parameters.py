"""Tests for sampler.search_parameters (R209, ``xai-grok-sampling-types`` ``types.rs``).

Covers the fourth ``types.rs`` slice -- the realtime-data search knobs:

- :class:`SearchParameters` (struct) -- the ``search_parameters`` request body
  (``mode`` / ``sources`` / ``from_date`` / ``to_date`` / ``return_citations`` /
  ``max_search_results``, all optional). grok ships NO ``#[derive(Default)]``
  here, so no ``default()`` classmethod is exposed -- the all-``None`` instance
  is reached only via ``from_payload``.
- :class:`SearchSource` (4-variant tagged union, ``tag="type"``) -- ``x`` /
  ``web`` / ``news`` / ``rss``, each a per-variant ``#[serde(rename="...")]``
  wire tag. The ``x`` variant carries a DEPRECATED ``x_handles`` field (kept for
  backward wire-compat); the ``rss`` variant's ``links`` is the only
  NON-optional field.

The slice closes against the std scalars alone (no R206/R207 leaf, no
``crate::rs``, no ``serde_helpers``). Migration invariants tested below:

- ``Option<Vec<String>>`` -> ``tuple[str, ...] | None`` (null / absent / non-list
  -> ``None``); ``Vec<String>`` non-optional (``rss.links``) -> ``tuple[str, ...]``
  (null / absent / non-list -> empty tuple -- the ``Option::None`` vs
  ``Some(vec![])`` distinction is the critical semantic tested here).
- strict tagged-union dispatch on the wire ``type`` tag (unknown tag raises
  ``ValueError`` -- no catch-all, same posture as the R207 :class:`ChatContentBlock`).
- the union-base dispatcher rejects a non-dict payload (``ValueError``), while
  each variant constructor tolerates it to the all-default instance.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code.sampler.search_parameters import (
    SearchParameters,
    SearchSource,
    SearchSourceNews,
    SearchSourceRss,
    SearchSourceWeb,
    SearchSourceX,
)

# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_module_barrel_exposes_six_symbols() -> None:
    """1 struct + 1 union base + 4 variant subclasses = 6 re-exported symbols."""
    import minimax_code.sampler.search_parameters as search_parameters

    assert len(search_parameters.__all__) == 6
    assert set(search_parameters.__all__) == {
        "SearchParameters",
        "SearchSource",
        "SearchSourceNews",
        "SearchSourceRss",
        "SearchSourceWeb",
        "SearchSourceX",
    }


def test_package_barrel_re_exports_search_symbols() -> None:
    """The package barrel flattens the 6 search-parameter symbols."""
    import minimax_code.sampler as sampler

    for name in (
        "SearchParameters",
        "SearchSource",
        "SearchSourceNews",
        "SearchSourceRss",
        "SearchSourceWeb",
        "SearchSourceX",
    ):
        assert name in sampler.__all__


# ---------------------------------------------------------------------------
# SearchSource.from_payload: 4-variant tagged-union dispatch (tag="type").
# ---------------------------------------------------------------------------


def test_search_source_dispatches_x_variant() -> None:
    result = SearchSource.from_payload({"type": "x", "post_view_count": 100})
    assert isinstance(result, SearchSourceX)
    assert result.post_view_count == 100


def test_search_source_dispatches_web_variant() -> None:
    result = SearchSource.from_payload({"type": "web", "country": "us"})
    assert isinstance(result, SearchSourceWeb)
    assert result.country == "us"


def test_search_source_dispatches_news_variant() -> None:
    result = SearchSource.from_payload({"type": "news", "country": "cn"})
    assert isinstance(result, SearchSourceNews)
    assert result.country == "cn"


def test_search_source_dispatches_rss_variant() -> None:
    result = SearchSource.from_payload({"type": "rss", "links": ["https://a.com"]})
    assert isinstance(result, SearchSourceRss)
    assert result.links == ("https://a.com",)


@pytest.mark.parametrize("payload", ["x", None, 1.5, ["type", "x"], 42])
def test_search_source_non_dict_raises_value_error(payload: object) -> None:
    """The dispatcher requires a dict (mirrors serde's strict tagged-union
    parse -- no catch-all). Contrast the variant constructors below, which
    tolerate a non-dict to the all-default instance."""
    with pytest.raises(ValueError):
        SearchSource.from_payload(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "twitter"},
        {"type": "X"},  # case-sensitive wire tag
        {},  # missing type key
        {"type": 5},  # non-string type
        {"type": None},  # null type
    ],
)
def test_search_source_unknown_type_raises_value_error(payload: object) -> None:
    """An unknown / missing / non-string wire ``type`` tag raises (mirrors
    serde's strict tagged-union parse -- no catch-all, same posture as the R207
    :class:`ChatContentBlock` union)."""
    with pytest.raises(ValueError):
        SearchSource.from_payload(payload)


def test_search_source_dispatch_returns_subclass_of_union_base() -> None:
    assert isinstance(SearchSource.from_payload({"type": "x"}), SearchSource)
    assert isinstance(SearchSource.from_payload({"type": "web"}), SearchSource)
    assert isinstance(SearchSource.from_payload({"type": "news"}), SearchSource)
    assert isinstance(SearchSource.from_payload({"type": "rss"}), SearchSource)


# ---------------------------------------------------------------------------
# SearchSourceX: 5 fields (DEPRECATED x_handles kept + Option<Vec<String>>).
# ---------------------------------------------------------------------------


def test_search_source_x_maps_all_five_fields() -> None:
    result = SearchSourceX.from_payload(
        {
            "type": "x",
            "included_x_handles": ["a", "b"],
            "x_handles": ["legacy"],
            "excluded_x_handles": ["spam"],
            "post_favorite_count": 42,
            "post_view_count": 1000,
        }
    )
    assert result.included_x_handles == ("a", "b")
    assert result.x_handles == ("legacy",)
    assert result.excluded_x_handles == ("spam",)
    assert result.post_favorite_count == 42
    assert result.post_view_count == 1000


def test_search_source_x_handles_is_deprecated_but_kept() -> None:
    """``x_handles`` is DEPRECATED in favor of ``included_x_handles`` but is
    still parsed verbatim (faithful to the wire shape; no reconciliation -- the
    consumer decides how to reconcile the two)."""
    result = SearchSourceX.from_payload(
        {"x_handles": ["old"], "included_x_handles": ["new"]}
    )
    assert result.x_handles == ("old",)
    assert result.included_x_handles == ("new",)


def test_search_source_x_optional_string_lists_none_when_missing() -> None:
    """``Option<Vec<String>>`` tolerates a missing key to ``None`` (mirrors
    ``Option::None``)."""
    result = SearchSourceX.from_payload({})
    assert result.included_x_handles is None
    assert result.x_handles is None
    assert result.excluded_x_handles is None


def test_search_source_x_null_handle_list_to_none() -> None:
    result = SearchSourceX.from_payload({"included_x_handles": None})
    assert result.included_x_handles is None


def test_search_source_x_non_list_handle_list_to_none() -> None:
    """A non-list wire value for an ``Option<Vec<String>>`` field tolerates to
    ``None`` (NOT the empty tuple -- the field was absent-shaped)."""
    result = SearchSourceX.from_payload({"included_x_handles": "oops"})
    assert result.included_x_handles is None


def test_search_source_x_skips_non_string_handle_items() -> None:
    """A non-string item in the handle list is skipped (forward-compat: the
    platform tolerates a malformed ``Vec<String>`` that serde would reject)."""
    result = SearchSourceX.from_payload(
        {"included_x_handles": ["ok", 7, None, "good"]}
    )
    assert result.included_x_handles == ("ok", "good")


def test_search_source_x_scalar_fields_pass_through_verbatim() -> None:
    """``Option<i32>``: the wire value passes through with no runtime type check
    (``dict.get`` -- tolerating null / any-shape, mirroring grok's pass-through
    ``Option<T>`` deserialization)."""
    result = SearchSourceX.from_payload(
        {"post_favorite_count": 0, "post_view_count": None}
    )
    assert result.post_favorite_count == 0
    assert result.post_view_count is None


def test_search_source_x_non_dict_payload_is_all_none() -> None:
    """The variant constructor tolerates a non-dict to the all-None instance
    (contrast the union-base dispatcher, which raises ``ValueError``)."""
    assert SearchSourceX.from_payload(None) == SearchSourceX()
    assert SearchSourceX.from_payload("oops") == SearchSourceX()


# ---------------------------------------------------------------------------
# SearchSourceWeb: 4 fields (2 list + country + safe_search).
# ---------------------------------------------------------------------------


def test_search_source_web_maps_all_four_fields() -> None:
    result = SearchSourceWeb.from_payload(
        {
            "type": "web",
            "excluded_websites": ["spam.com"],
            "allowed_websites": ["good.com"],
            "country": "us",
            "safe_search": True,
        }
    )
    assert result.excluded_websites == ("spam.com",)
    assert result.allowed_websites == ("good.com",)
    assert result.country == "us"
    assert result.safe_search is True


def test_search_source_news_omits_allowed_websites_field() -> None:
    """News carries a subset of the Web filters: no ``allowed_websites`` knob
    (a real struct-shape difference between the two variants, not just a
    defaulted field)."""
    assert "allowed_websites" not in SearchSourceNews.__slots__
    assert "allowed_websites" in SearchSourceWeb.__slots__


def test_search_source_web_non_dict_payload_is_all_none() -> None:
    assert SearchSourceWeb.from_payload(None) == SearchSourceWeb()
    assert SearchSourceWeb.from_payload(123) == SearchSourceWeb()


# ---------------------------------------------------------------------------
# SearchSourceNews: 3 fields (subset of Web -- no allowed_websites).
# ---------------------------------------------------------------------------


def test_search_source_news_maps_all_three_fields() -> None:
    result = SearchSourceNews.from_payload(
        {
            "type": "news",
            "excluded_websites": ["bias.org"],
            "country": "uk",
            "safe_search": False,
        }
    )
    assert result.excluded_websites == ("bias.org",)
    assert result.country == "uk"
    assert result.safe_search is False


def test_search_source_news_non_dict_payload_is_all_none() -> None:
    assert SearchSourceNews.from_payload(None) == SearchSourceNews()
    assert SearchSourceNews.from_payload([]) == SearchSourceNews()


# ---------------------------------------------------------------------------
# SearchSourceRss: the only NON-optional field (links: Vec<String>).
# ---------------------------------------------------------------------------


def test_search_source_rss_links_is_tuple() -> None:
    """``Vec<String>`` -> ``tuple[str, ...]`` (the list-carrying Vec becomes an
    immutable tuple)."""
    result = SearchSourceRss.from_payload(
        {"type": "rss", "links": ["https://a.com/feed", "https://b.com/feed"]}
    )
    assert isinstance(result.links, tuple)
    assert result.links == ("https://a.com/feed", "https://b.com/feed")


def test_search_source_rss_missing_links_to_empty_tuple_not_none() -> None:
    """``links`` is the only NON-optional field: a missing key -> empty tuple
    (NOT ``None`` -- contrast the X/Web/News optional lists, which go to
    ``None``). Mirrors grok's bare ``Vec<String>`` (no ``Option`` wrapper)."""
    assert SearchSourceRss.from_payload({}).links == ()
    assert SearchSourceRss.from_payload({"type": "rss"}).links == ()


def test_search_source_rss_null_links_to_empty_tuple() -> None:
    """A ``null`` ``links`` tolerates to the empty tuple (forward-compat, same
    posture as the R208 ``deserialize_null_default`` inline)."""
    assert SearchSourceRss.from_payload({"links": None}).links == ()


def test_search_source_rss_non_list_links_to_empty_tuple() -> None:
    assert SearchSourceRss.from_payload({"links": "oops"}).links == ()
    assert SearchSourceRss.from_payload({"links": {"a": 1}}).links == ()


def test_search_source_rss_skips_non_string_links() -> None:
    result = SearchSourceRss.from_payload({"links": ["ok", 7, None, "good"]})
    assert result.links == ("ok", "good")


def test_search_source_rss_non_dict_payload_is_empty_links() -> None:
    """The variant constructor tolerates a non-dict: ``links`` defaults to the
    empty tuple (the all-default instance)."""
    assert SearchSourceRss.from_payload(None).links == ()
    assert SearchSourceRss.from_payload("oops").links == ()


# ---------------------------------------------------------------------------
# SearchParameters: 6 fields (all optional, no Default derive).
# ---------------------------------------------------------------------------


def test_search_parameters_maps_all_six_fields() -> None:
    result = SearchParameters.from_payload(
        {
            "mode": "on",
            "sources": [{"type": "x"}],
            "from_date": "2026-01-01",
            "to_date": "2026-12-31",
            "return_citations": True,
            "max_search_results": 10,
        }
    )
    assert result.mode == "on"
    assert result.from_date == "2026-01-01"
    assert result.to_date == "2026-12-31"
    assert result.return_citations is True
    assert result.max_search_results == 10
    assert len(result.sources) == 1
    assert isinstance(result.sources[0], SearchSourceX)


def test_search_parameters_mode_is_free_form_string() -> None:
    """``mode`` is a free-form string (the platform does not enforce the
    on/off/auto enum, mirroring grok's ``Option<String>``)."""
    assert SearchParameters.from_payload({"mode": "auto"}).mode == "auto"
    assert SearchParameters.from_payload({"mode": "weird"}).mode == "weird"


def test_search_parameters_sources_to_none_when_missing() -> None:
    """``Option<Vec<SearchSource>>``: a missing key tolerates to ``None``
    (mirrors ``Option::None`` -- distinct from an empty list)."""
    assert SearchParameters.from_payload({}).sources is None


def test_search_parameters_null_sources_to_none() -> None:
    assert SearchParameters.from_payload({"sources": None}).sources is None


def test_search_parameters_non_list_sources_to_none() -> None:
    """A non-list ``sources`` wire value tolerates to ``None`` (NOT the empty
    tuple -- the field was absent-shaped, mirroring ``Option::None``)."""
    assert SearchParameters.from_payload({"sources": "oops"}).sources is None
    assert (
        SearchParameters.from_payload({"sources": {"type": "x"}}).sources is None
    )


def test_search_parameters_empty_list_sources_to_empty_tuple() -> None:
    """An empty list -> empty tuple (NOT ``None``). This is the critical
    ``Option::None`` vs ``Some(vec![])`` distinction: a present-but-empty
    sources list yields ``Some(vec![])`` -> ``()``, not ``None``."""
    result = SearchParameters.from_payload({"sources": []})
    assert result.sources == ()
    assert result.sources is not None


def test_search_parameters_sources_is_tuple_of_variants() -> None:
    """``Vec<SearchSource>`` -> ``tuple[SearchSource, ...]``, each item parsed
    through the tagged-union dispatcher."""
    result = SearchParameters.from_payload(
        {
            "sources": [
                {"type": "x", "post_view_count": 5},
                {"type": "rss", "links": ["https://x.com"]},
            ]
        }
    )
    assert isinstance(result.sources, tuple)
    assert len(result.sources) == 2
    assert isinstance(result.sources[0], SearchSourceX)
    assert result.sources[0].post_view_count == 5
    assert isinstance(result.sources[1], SearchSourceRss)


def test_search_parameters_sources_skips_non_dict_items() -> None:
    """A non-dict item in the sources list is skipped (not crash, no
    ``ValueError`` -- the malformed item is dropped, the rest still parse)."""
    result = SearchParameters.from_payload(
        {"sources": [{"type": "x"}, "junk", 7, {"type": "web"}]}
    )
    assert len(result.sources) == 2
    assert isinstance(result.sources[0], SearchSourceX)
    assert isinstance(result.sources[1], SearchSourceWeb)


def test_search_parameters_non_dict_payload_is_all_none() -> None:
    """A missing / null / non-dict payload -> the all-``None`` instance. Note
    grok ships NO ``#[derive(Default)]`` here (unlike the R208 streaming
    structs), so the all-None instance is reached only via ``from_payload``."""
    result = SearchParameters.from_payload(None)
    assert result.mode is None
    assert result.sources is None
    assert result.from_date is None
    assert result.to_date is None
    assert result.return_citations is None
    assert result.max_search_results is None
    assert SearchParameters.from_payload("oops") == SearchParameters()
    assert SearchParameters.from_payload([]) == SearchParameters()


def test_search_parameters_has_no_default_classmethod() -> None:
    """Faithful to grok's derive set (no ``#[derive(Default)]``): the class
    exposes ``from_payload`` but NOT ``default()`` (contrast the R208 streaming
    structs, which DO expose ``default()``)."""
    assert not hasattr(SearchParameters, "default")


# ---------------------------------------------------------------------------
# Variant subclassing: the 4 variants ARE the union base.
# ---------------------------------------------------------------------------


def test_search_source_variants_are_subclasses_of_union_base() -> None:
    """``isinstance`` dispatch relies on the variant subclasses being
    subclasses of the union base."""
    assert issubclass(SearchSourceX, SearchSource)
    assert issubclass(SearchSourceWeb, SearchSource)
    assert issubclass(SearchSourceNews, SearchSource)
    assert issubclass(SearchSourceRss, SearchSource)


# ---------------------------------------------------------------------------
# Value semantics: frozen + slots + hashable.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "obj",
    [
        SearchSourceX(included_x_handles=("a",)),
        SearchSourceWeb(country="us"),
        SearchSourceNews(safe_search=True),
        SearchSourceRss(links=("https://x.com",)),
        SearchParameters(mode="on"),
    ],
)
def test_search_leaves_are_frozen(obj: object) -> None:
    """``@dataclass(frozen=True)`` -> mutating the first declared slot raises
    (mirrors grok's immutable struct). Uses ``setattr`` with a variable field
    name (the first declared slot) so the raise is driven by the dataclass
    ``__setattr__`` (B010-safe: the attribute name is a variable, not a literal
    constant property name)."""
    field_name = next(iter(type(obj).__slots__))
    with pytest.raises(FrozenInstanceError):
        setattr(obj, field_name, "rewritten")  # type: ignore[misc]


def test_search_leaves_are_hashable_and_equal() -> None:
    """``frozen=True`` -> hashable + equal-by-value, including the nested
    ``sources`` tuple (each SearchSource variant is itself hashable)."""
    a = SearchSourceX(included_x_handles=("a", "b"))
    b = SearchSourceX(included_x_handles=("a", "b"))
    assert a == b
    assert hash(a) == hash(b)

    p1 = SearchParameters(
        mode="on",
        sources=(SearchSourceRss(links=("https://x.com",)),),
    )
    p2 = SearchParameters(
        mode="on",
        sources=(SearchSourceRss(links=("https://x.com",)),),
    )
    assert p1 == p2
    assert hash(p1) == hash(p2)


def test_search_leaves_declare_slots() -> None:
    """``slots=True`` -> each class declares ``__slots__`` over its fields (the
    union base has none of its own)."""
    assert SearchSource.__slots__ == ()
    assert SearchSourceX.__slots__ == (
        "included_x_handles",
        "x_handles",
        "excluded_x_handles",
        "post_favorite_count",
        "post_view_count",
    )
    assert SearchSourceWeb.__slots__ == (
        "excluded_websites",
        "allowed_websites",
        "country",
        "safe_search",
    )
    assert SearchSourceNews.__slots__ == (
        "excluded_websites",
        "country",
        "safe_search",
    )
    assert SearchSourceRss.__slots__ == ("links",)
    assert SearchParameters.__slots__ == (
        "mode",
        "sources",
        "from_date",
        "to_date",
        "return_citations",
        "max_search_results",
    )
