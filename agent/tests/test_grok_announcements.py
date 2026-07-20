"""Tests for grok_announcements (R194, ``xai-grok-announcements`` lib.rs).

Mirrors grok-build's ``lib.rs`` inline ``#[cfg(test)]`` suite (9 tests) plus
platform structural coverage + async I/O round-trip + product-identity rename
assertions. The ``#[cfg(test, feature="ts")] export_all_bindings`` test is
YAGNI (ts_rs codegen pipeline absent -- see module docstring).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import minimax_code.grok_announcements as ga
from minimax_code.grok_announcements import (
    AnnouncementCta,
    AnnouncementsRefreshed,
    RemoteAnnouncement,
    announcement_hide_key,
    filter_expired,
    filter_expired_at,
    is_expired_at,
    parse_hidden_announcement_ids,
    prune_hidden_announcement_ids,
    read_hidden_announcement_ids,
    resolve_startup,
    serialize_hidden_announcement_ids,
    visible_announcements,
    write_hidden_announcement_ids,
)

# ---------------------------------------------------------------------------
# Structural: barrel surface + identity renames.
# ---------------------------------------------------------------------------


def test_barrel_exposes_fourteen_symbols() -> None:
    """3 wire types + 9 pure functions + 2 async I/O functions = 14 symbols."""
    assert len(ga.__all__) == 14
    assert set(ga.__all__) == {
        "AnnouncementCta",
        "AnnouncementsRefreshed",
        "RemoteAnnouncement",
        "announcement_hide_key",
        "filter_expired",
        "filter_expired_at",
        "is_expired_at",
        "parse_hidden_announcement_ids",
        "prune_hidden_announcement_ids",
        "read_hidden_announcement_ids",
        "resolve_startup",
        "serialize_hidden_announcement_ids",
        "visible_announcements",
        "write_hidden_announcement_ids",
    }


def test_override_env_var_renamed_to_platform_identity() -> None:
    """grok ``GROK_ANNOUNCEMENTS_OVERRIDE`` -> ``MINIMAX_CODE_ANNOUNCEMENTS_OVERRIDE``."""
    assert ga._ANNOUNCEMENTS_OVERRIDE_ENV == "MINIMAX_CODE_ANNOUNCEMENTS_OVERRIDE"


def test_hide_key_separator_is_unit_separator() -> None:
    """The content-fallback separator is U+001F (grok ``\\u{1f}``), load-bearing."""
    assert ga._HIDE_KEY_SEPARATOR == "\x1f"


# ---------------------------------------------------------------------------
# Rust inline test 1: filter_expired_removes_past.
# ---------------------------------------------------------------------------


def test_filter_expired_removes_past() -> None:
    past = RemoteAnnouncement(expires_at="2000-01-01T00:00:00Z")
    future = RemoteAnnouncement(expires_at="2100-01-01T00:00:00Z")
    none = RemoteAnnouncement()
    filtered = filter_expired([past, future, none])
    assert len(filtered) == 2


# ---------------------------------------------------------------------------
# Rust inline test 2: filter_expired_at_honors_injected_clock (+ boundary).
# ---------------------------------------------------------------------------


def test_filter_expired_at_honors_injected_clock() -> None:
    item = RemoteAnnouncement(expires_at="2030-01-01T00:00:00Z")
    expiry = datetime(2030, 1, 1, tzinfo=UTC)
    before = expiry - timedelta(seconds=1)
    assert len(filter_expired_at([item], before)) == 1
    assert filter_expired_at([item], expiry) == []
    after = expiry + timedelta(seconds=1)
    assert filter_expired_at([item], after) == []


def test_is_expired_at_exact_boundary_is_expired() -> None:
    """Strict ``dt <= now``: at the exact expiry instant the item is already expired."""
    a = RemoteAnnouncement(expires_at="2030-01-01T00:00:00Z")
    expiry = datetime(2030, 1, 1, tzinfo=UTC)
    assert is_expired_at(a, expiry) is True
    assert is_expired_at(a, expiry - timedelta(seconds=1)) is False


def test_is_expired_at_missing_or_unparseable_never_expires() -> None:
    now = datetime.now(UTC)
    assert is_expired_at(RemoteAnnouncement(), now) is False
    assert is_expired_at(RemoteAnnouncement(expires_at="not a date"), now) is False


# ---------------------------------------------------------------------------
# Rust inline test 3: resolve_startup_env_override (renamed env).
# ---------------------------------------------------------------------------


def test_resolve_startup_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIMAX_CODE_ANNOUNCEMENTS_OVERRIDE", '[{"id":"test"}]')
    result = resolve_startup(None)
    assert result is not None
    assert result[0].id == "test"


def test_resolve_startup_invalid_json_falls_back_to_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """grok logs+ignores invalid override JSON; falls back to remote (identity)."""
    monkeypatch.setenv("MINIMAX_CODE_ANNOUNCEMENTS_OVERRIDE", "not json")
    remote = [RemoteAnnouncement(id="remote")]
    assert resolve_startup(remote) is remote


def test_resolve_startup_non_list_payload_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINIMAX_CODE_ANNOUNCEMENTS_OVERRIDE", '{"id":"x"}')
    remote = [RemoteAnnouncement(id="remote")]
    assert resolve_startup(remote) is remote


def test_resolve_startup_env_unset_returns_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINIMAX_CODE_ANNOUNCEMENTS_OVERRIDE", raising=False)
    assert resolve_startup(None) is None
    remote = [RemoteAnnouncement(id="remote")]
    assert resolve_startup(remote) is remote


# ---------------------------------------------------------------------------
# Rust inline test 4: cta_parses_nested_partial_and_absent (+ tolerance).
# ---------------------------------------------------------------------------


def test_cta_parses_nested_partial_and_absent() -> None:
    full = RemoteAnnouncement.from_mapping(
        json.loads(
            '{"id":"p","severity":"promo","cta":{"label":"Get SuperGrok",'
            '"url":"https://x.ai/grok","caption":"or use Ctrl+O"}}'
        )
    )
    assert full.id == "p"
    cta = full.cta
    assert cta is not None
    assert cta.label == "Get SuperGrok"
    assert cta.url == "https://x.ai/grok"
    assert cta.caption == "or use Ctrl+O"

    partial = RemoteAnnouncement.from_mapping(json.loads('{"cta":{"label":"only label"}}'))
    assert partial.cta == AnnouncementCta(label="only label")

    absent = RemoteAnnouncement.from_mapping(json.loads('{"id":"a"}'))
    assert absent.cta is None


def test_remote_announcement_tolerates_unknown_fields_and_wrong_types() -> None:
    """Unknown keys ignored; wrong-typed field values decay to None."""
    a = RemoteAnnouncement.from_mapping(
        {"id": "x", "severity": 123, "dismissible": "yes", "future_field": {"z": 1}}
    )
    assert a.id == "x"
    assert a.severity is None  # non-string -> None
    assert a.dismissible is None  # non-bool -> None


# ---------------------------------------------------------------------------
# Rust inline test 5: hidden_ids_round_trip (+ compact+sorted).
# ---------------------------------------------------------------------------


def test_hidden_ids_round_trip() -> None:
    ids = {"outage-a", "outage-b"}
    s = serialize_hidden_announcement_ids(ids)
    assert s is not None
    assert parse_hidden_announcement_ids(s) == ids
    assert s == '{"hidden_ids":["outage-a","outage-b"]}'

    empty_s = serialize_hidden_announcement_ids(set())
    assert empty_s is not None
    assert parse_hidden_announcement_ids(empty_s) == set()


def test_serialize_hidden_ids_is_compact_and_sorted() -> None:
    """Compact JSON (no whitespace) + sorted keys for a stable on-disk file."""
    s = serialize_hidden_announcement_ids({"b", "a", "c"})
    assert s == '{"hidden_ids":["a","b","c"]}'


# ---------------------------------------------------------------------------
# Rust inline test 6: parse_hidden_ids_discards_legacy_bool_shape.
# ---------------------------------------------------------------------------


def test_parse_hidden_ids_discards_legacy_bool_shape() -> None:
    assert parse_hidden_announcement_ids('{"hidden":true}') == set()
    assert parse_hidden_announcement_ids('{"hidden":false}') == set()


# ---------------------------------------------------------------------------
# Rust inline test 7: parse_hidden_ids_tolerates_unknown_fields_and_malformed.
# ---------------------------------------------------------------------------


def test_parse_hidden_ids_tolerates_unknown_fields_and_malformed_input() -> None:
    got = parse_hidden_announcement_ids('{"hidden_ids":["a"],"future_field":{"x":1}}')
    assert got == {"a"}
    assert parse_hidden_announcement_ids("") == set()
    assert parse_hidden_announcement_ids("not json") == set()
    assert parse_hidden_announcement_ids('{"hidden_ids":"oops"}') == set()


def test_parse_hidden_ids_filters_non_string_items() -> None:
    """Non-string items in hidden_ids are dropped (tolerant)."""
    assert parse_hidden_announcement_ids('{"hidden_ids":["a", 1, null, "b"]}') == {"a", "b"}


# ---------------------------------------------------------------------------
# Rust inline test 8: prune_hidden_ids_drops_ids_absent_from_active_list.
# ---------------------------------------------------------------------------


def test_prune_hidden_ids_drops_ids_absent_from_active_list() -> None:
    active = [
        RemoteAnnouncement(id="live"),
        RemoteAnnouncement(title="T", message="M"),
    ]
    ids = {"live", "gone", announcement_hide_key(active[1])}

    assert prune_hidden_announcement_ids(ids, active) is True
    assert len(ids) == 2
    assert "live" in ids
    assert announcement_hide_key(active[1]) in ids

    # Second prune with the same list is a no-op.
    assert prune_hidden_announcement_ids(ids, active) is False


# ---------------------------------------------------------------------------
# Rust inline test 9: announcement_hide_key_prefers_id_with_content_fallback.
# ---------------------------------------------------------------------------


def test_announcement_hide_key_prefers_id_with_content_fallback() -> None:
    with_id = RemoteAnnouncement(id="  spaced-id  ", title="T", message="M")
    assert announcement_hide_key(with_id) == "spaced-id"

    blank_id = RemoteAnnouncement(id="   ", title="T", message="M")
    assert announcement_hide_key(blank_id) == "content:T\x1fM"

    no_id = RemoteAnnouncement()
    assert announcement_hide_key(no_id) == "content:\x1f"

    # The unprintable separator disambiguates title/message splits.
    ab_c = RemoteAnnouncement(title="a|b", message="c")
    a_bc = RemoteAnnouncement(title="a", message="b|c")
    assert announcement_hide_key(ab_c) != announcement_hide_key(a_bc)


# ---------------------------------------------------------------------------
# Rust inline test 10: visible_announcements_filters_empty_message.
# ---------------------------------------------------------------------------


def test_visible_announcements_filters_empty_message() -> None:
    a1 = RemoteAnnouncement(message="valid")
    a2 = RemoteAnnouncement()
    a3 = RemoteAnnouncement(message="   ")
    assert len(visible_announcements([a1, a2, a3])) == 1


# ---------------------------------------------------------------------------
# Platform async I/O: read/write round-trip against a scratch path.
# ---------------------------------------------------------------------------


async def test_read_write_hidden_announcement_ids_round_trip(tmp_path: Path) -> None:
    """write then read returns the same set; missing file reads as empty."""
    path = tmp_path / "announcements.json"
    await write_hidden_announcement_ids({"a", "b"}, path)
    assert await read_hidden_announcement_ids(path) == {"a", "b"}

    # Missing file -> empty set (no error).
    missing = tmp_path / "absent.json"
    assert await read_hidden_announcement_ids(missing) == set()


async def test_write_hidden_announcement_ids_creates_parent_dir(tmp_path: Path) -> None:
    """The parent directory is created if missing (platform hardening over grok)."""
    path = tmp_path / "nested" / "dir" / "announcements.json"
    await write_hidden_announcement_ids({"x"}, path)
    assert path.exists()
    assert await read_hidden_announcement_ids(path) == {"x"}


# ---------------------------------------------------------------------------
# AnnouncementsRefreshed wire type (platform structural).
# ---------------------------------------------------------------------------


def test_announcements_refreshed_parses_gen_and_items() -> None:
    payload = json.loads('{"gen": 7, "announcements": [{"id":"a"},{"id":"b"}]}')
    refreshed = AnnouncementsRefreshed.from_mapping(payload)
    assert refreshed is not None
    assert refreshed.gen == 7
    assert [a.id for a in refreshed.announcements] == ["a", "b"]


def test_announcements_refreshed_absent_announcements_defaults_empty() -> None:
    refreshed = AnnouncementsRefreshed.from_mapping(json.loads('{"gen": 1}'))
    assert refreshed is not None
    assert refreshed.announcements == []


def test_announcements_refreshed_rejects_non_int_gen() -> None:
    assert AnnouncementsRefreshed.from_mapping(json.loads('{"gen": "x"}')) is None
    assert AnnouncementsRefreshed.from_mapping(None) is None


# ---------------------------------------------------------------------------
# Value semantics (platform structural).
# ---------------------------------------------------------------------------


def test_remote_announcement_is_frozen_and_hashable() -> None:
    a = RemoteAnnouncement(id="x", message="m")
    b = RemoteAnnouncement(id="x", message="m")
    assert a == b
    assert hash(a) == hash(b)
    cta_a = AnnouncementCta(label="L")
    cta_b = AnnouncementCta(label="L")
    assert cta_a == cta_b
    assert hash(cta_a) == hash(cta_b)
