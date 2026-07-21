"""Search parameters + sources leaf types (R209, ``xai-grok-sampling-types`` ``types.rs``).

R209 lands the fourth slice of ``types.rs`` -- the realtime-data search knobs:
:class:`SearchParameters` (the ``search_parameters`` request body) +
:class:`SearchSource` (a 4-variant tagged union carried inside ``sources``).
Both close dependency-free against the std scalars alone (no R206/R207 leaf, no
``crate::rs``, no ``serde_helpers``), making this the cleanest pure-leaf slice
since the R206 atomic cluster.

- :class:`SearchParameters` (struct) -- ``mode`` / ``sources`` / ``from_date``
  / ``to_date`` / ``return_citations`` / ``max_search_results``, all optional.
  Carries a tolerant ``from_payload`` (a non-dict payload -> an all-``None``
  instance). Note grok ships NO ``#[derive(Default)]`` here (unlike the R208
  streaming structs), so no ``default()`` classmethod is exposed -- faithful to
  the derive set; the all-``None`` instance is reached only via ``from_payload``.
- :class:`SearchSource` (4-variant tagged union, ``tag="type"``) -- ``x`` /
  ``web`` / ``news`` / ``rss``, each a per-variant ``#[serde(rename="...")]``
  wire tag. The ``x`` variant carries a DEPRECATED ``x_handles`` field (kept for
  backward wire-compat, superseded by ``included_x_handles``); the ``rss``
  variant's ``links`` is the only NON-optional field (``Vec<String>`` ->
  ``tuple[str, ...]``, tolerating a missing/non-list wire value as the empty
  tuple). ``from_payload`` dispatches strictly on the wire ``type`` tag -- an
  unknown tag raises ``ValueError`` (mirrors serde's strict tagged-union parse,
  no catch-all -- same posture as the R207 :class:`ChatContentBlock` union).

The slice is no-I/O (``serde_json::Value`` -> ``dict`` / wire value).
Migration map (grok -> Python):

- ``#[serde(tag="type", rename="...")] enum`` (:class:`SearchSource`) ->
  frozen+slots union base + 4 subclasses; the base ``from_payload`` dispatches
  on the wire ``type`` tag, each subclass reading its own struct fields. The
  union has NO untagged catch-all, so an unknown tag raises ``ValueError``
  (mirrors serde's strict tagged-union parse -- same posture as the R207
  :class:`ChatContentBlock`).
- ``Option<Vec<String>>`` -> ``tuple[str, ...] | None`` (a ``null`` / absent /
  non-list wire value tolerates to ``None``; a list becomes an immutable tuple,
  non-string items skipped for forward-compat).
- ``Vec<String>`` (non-optional, ``rss.links``) -> ``tuple[str, ...]`` (a
  missing / non-list wire value tolerates to the empty tuple -- forward-compat,
  same posture as the R208 ``deserialize_null_default`` inline).
- ``Option<T>`` for scalars (``String`` / ``bool`` / ``i32``) -> ``T | None =
  None`` (a ``dict.get`` with no runtime type check -- the wire value passes
  through verbatim, tolerating ``null`` / absent / any-shape forward-compat).
- plain ``#[derive(Serialize, Deserialize)] struct`` (:class:`SearchParameters`)
  -> ``@dataclass(frozen=True, slots=True)`` with a tolerant ``from_payload``.

Naming: the 4 ``SearchSource`` variants carry a ``SearchSource`` prefix
(``SearchSourceX`` / ``SearchSourceWeb`` / ``SearchSourceNews`` /
``SearchSourceRss``) to flatten unambiguously into the package barrel (the bare
``X`` / ``Web`` / ``News`` / ``Rss`` variant names would collide with future
leaves and read poorly at the barrel level). :class:`SearchParameters` /
:class:`SearchSource` are new (no Anthropic Messages API peer, no collision).

YAGNI: full serde ``Serialize``/``Deserialize`` round-trip -- ``from_payload``
covers the parse direction the platform needs. The DEPRECATED ``x_handles``
field is parsed but no migration to ``included_x_handles`` is performed
(faithful to the wire shape; the consumer decides how to reconcile).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Option<Vec<String>> / Vec<String> wire helpers (module-private).
# ---------------------------------------------------------------------------
#
# Two parsers because grok's two list shapes differ on the missing/non-list
# fallback: ``Option<Vec<String>>`` tolerates to ``None`` (the field was
# absent), while a bare ``Vec<String>`` tolerates to the empty tuple (the field
# is required but the wire shape is malformed -- forward-compat, same posture
# as the R208 ``deserialize_null_default`` inline). Non-string list items are
# skipped in both (a ``Vec<String>`` of mixed shape would fail serde; the
# platform tolerates).


def _parse_optional_string_list(value: Any) -> tuple[str, ...] | None:
    """Mirror ``Option<Vec<String>>``: a list -> an immutable tuple of its
    string items (non-string items skipped); anything else (``null`` / absent /
    non-list) -> ``None``."""
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    return None


def _parse_required_string_list(value: Any) -> tuple[str, ...]:
    """Mirror ``Vec<String>`` (non-optional, e.g. ``rss.links``): a list -> an
    immutable tuple of its string items (non-string items skipped); anything
    else (``null`` / absent / non-list) -> the empty tuple (forward-compat
    tolerance)."""
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    return ()


# ---------------------------------------------------------------------------
# SearchSource: 4-variant tagged union (tag="type", rename x/web/news/rss).
# ---------------------------------------------------------------------------
#
# The realtime-data source selector carried inside ``SearchParameters.sources``.
# Internally tagged on the wire ``type`` field; serde tries each variant's
# struct shape, there is NO catch-all, so an unknown ``type`` fails the parse --
# ``from_payload`` mirrors that by raising ``ValueError`` (same posture as the
# R207 ``ChatContentBlock`` union). The 4 subclass names carry a ``SearchSource``
# prefix to flatten unambiguously into the package barrel.


@dataclass(frozen=True, slots=True)
class SearchSource:
    """Realtime-data source union base (tagged on the wire ``type``). A
    :class:`SearchParameters` ``sources`` list is a list of these. Use
    :meth:`from_payload` for the wire dict -> variant mapping."""

    @classmethod
    def from_payload(cls, payload: Any) -> SearchSource:
        """Dispatch on the wire ``type`` tag. Known tags (``x`` / ``web`` /
        ``news`` / ``rss``) map to their variant; an unknown tag or a non-dict
        payload raises ``ValueError`` (mirrors serde's strict tagged-union
        parse -- no catch-all)."""
        if not isinstance(payload, dict):
            raise ValueError(
                f"search source must be a dict, got {type(payload).__name__}"
            )
        kind = payload.get("type")
        if kind == "x":
            return SearchSourceX.from_payload(payload)
        if kind == "web":
            return SearchSourceWeb.from_payload(payload)
        if kind == "news":
            return SearchSourceNews.from_payload(payload)
        if kind == "rss":
            return SearchSourceRss.from_payload(payload)
        raise ValueError(f"unknown search source type: {kind!r}")


@dataclass(frozen=True, slots=True)
class SearchSourceX(SearchSource):
    """``{"type":"x",...}`` -- the ``SearchSource::X`` variant. X (formerly
    Twitter) post filters. ``x_handles`` is DEPRECATED in favor of
    ``included_x_handles`` (both are parsed verbatim; no reconciliation is
    performed -- faithful to the wire shape)."""

    included_x_handles: tuple[str, ...] | None = None
    x_handles: tuple[str, ...] | None = None
    excluded_x_handles: tuple[str, ...] | None = None
    post_favorite_count: int | None = None
    post_view_count: int | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> SearchSourceX:
        """Tolerant constructor: each field defaults to ``None`` when the
        payload is missing / null / not a dict / lacks the key. The three
        handle lists parse through ``Option<Vec<String>>`` (non-string items
        skipped)."""
        if not isinstance(payload, dict):
            return cls()
        return cls(
            included_x_handles=_parse_optional_string_list(payload.get("included_x_handles")),
            x_handles=_parse_optional_string_list(payload.get("x_handles")),
            excluded_x_handles=_parse_optional_string_list(payload.get("excluded_x_handles")),
            post_favorite_count=payload.get("post_favorite_count"),
            post_view_count=payload.get("post_view_count"),
        )


@dataclass(frozen=True, slots=True)
class SearchSourceWeb(SearchSource):
    """``{"type":"web",...}`` -- the ``SearchSource::Web`` variant. Web-search
    filters: excluded / allowed website lists, an ISO alpha-2 ``country`` code,
    and the ``safe_search`` mature-content toggle."""

    excluded_websites: tuple[str, ...] | None = None
    allowed_websites: tuple[str, ...] | None = None
    country: str | None = None
    safe_search: bool | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> SearchSourceWeb:
        if not isinstance(payload, dict):
            return cls()
        return cls(
            excluded_websites=_parse_optional_string_list(payload.get("excluded_websites")),
            allowed_websites=_parse_optional_string_list(payload.get("allowed_websites")),
            country=payload.get("country"),
            safe_search=payload.get("safe_search"),
        )


@dataclass(frozen=True, slots=True)
class SearchSourceNews(SearchSource):
    """``{"type":"news",...}`` -- the ``SearchSource::News`` variant. News-search
    filters (a subset of the Web filters: no ``allowed_websites`` knob)."""

    excluded_websites: tuple[str, ...] | None = None
    country: str | None = None
    safe_search: bool | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> SearchSourceNews:
        if not isinstance(payload, dict):
            return cls()
        return cls(
            excluded_websites=_parse_optional_string_list(payload.get("excluded_websites")),
            country=payload.get("country"),
            safe_search=payload.get("safe_search"),
        )


@dataclass(frozen=True, slots=True)
class SearchSourceRss(SearchSource):
    """``{"type":"rss",...}`` -- the ``SearchSource::Rss`` variant. The only
    variant with a NON-optional field (``links: Vec<String>``). A missing /
    non-list ``links`` tolerates to the empty tuple (forward-compat, same
    posture as the R208 ``deserialize_null_default`` inline)."""

    links: tuple[str, ...] = ()

    @classmethod
    def from_payload(cls, payload: Any) -> SearchSourceRss:
        if not isinstance(payload, dict):
            return cls()
        return cls(links=_parse_required_string_list(payload.get("links")))


# ---------------------------------------------------------------------------
# SearchParameters: the realtime-data request knob (struct, no Default derive).
# ---------------------------------------------------------------------------
#
# ``mode`` / ``sources`` / ``from_date`` / ``to_date`` / ``return_citations`` /
# ``max_search_results``, all optional. grok ships NO ``#[derive(Default)]``
# here (unlike the R208 streaming structs), so no ``default()`` classmethod is
# exposed -- only the tolerant ``from_payload`` (a non-dict payload -> the
# all-``None`` instance). ``sources`` is a ``Option<Vec<SearchSource>>`` ->
# ``tuple[SearchSource, ...] | None``; a null / missing / non-list wire value
# tolerates to ``None`` (NOT the empty tuple -- the field was absent, mirroring
# ``Option::None``, distinct from an empty ``Some(vec![])``).


@dataclass(frozen=True, slots=True)
class SearchParameters:
    """Realtime-data search parameters (the ``search_parameters`` request knob).

    ``mode`` is ``"off"`` / ``"on"`` / ``"auto"`` (a free-form string -- the
    platform does not enforce the enum, mirroring grok's ``Option<String>``);
    ``sources`` is the optional list of :class:`SearchSource` filters;
    ``from_date`` / ``to_date`` are ISO-8601 ``YYYY-MM-DD`` bounds;
    ``return_citations`` toggles citation echoing; ``max_search_results`` caps
    the result count. All fields are optional (``None`` when absent). Use
    :meth:`from_payload` for the tolerant wire-dict -> instance mapping."""

    mode: str | None = None
    sources: tuple[SearchSource, ...] | None = None
    from_date: str | None = None
    to_date: str | None = None
    return_citations: bool | None = None
    max_search_results: int | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> SearchParameters:
        """Tolerant constructor: each field defaults to ``None`` when the payload
        is missing / null / not a dict / lacks the key. ``sources`` parses each
        dict item through :meth:`SearchSource.from_payload` (non-dict items are
        skipped) and tolerates a null / missing / non-list wire value to
        ``None`` (distinct from an empty list, which yields an empty tuple --
        mirroring ``Option::None`` vs ``Some(vec![])``)."""
        if not isinstance(payload, dict):
            return cls()
        raw_sources = payload.get("sources")
        if isinstance(raw_sources, list):
            sources: tuple[SearchSource, ...] | None = tuple(
                SearchSource.from_payload(item)
                for item in raw_sources
                if isinstance(item, dict)
            )
        else:
            sources = None
        return cls(
            mode=payload.get("mode"),
            sources=sources,
            from_date=payload.get("from_date"),
            to_date=payload.get("to_date"),
            return_citations=payload.get("return_citations"),
            max_search_results=payload.get("max_search_results"),
        )


__all__ = [
    "SearchParameters",
    "SearchSource",
    "SearchSourceNews",
    "SearchSourceRss",
    "SearchSourceWeb",
    "SearchSourceX",
]
