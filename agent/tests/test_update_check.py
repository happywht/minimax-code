"""Tests for update_check (R196, ``xai-grok-update`` ``version.rs`` +
``minimum_version.rs`` pure-logic subset).

Covers the eight migrated pure-logic symbols + value semantics, and asserts the
two R42 gap-closures this round delivers:

1. **Pre-release ordering** -- :func:`_cmp_key` realizes semver.org §11 (the
   exact example ``version.py`` docstring names as YAGNI: ``0.8.0-alpha <
   0.8.0``), plus the canonical §11.4 precedence chain.
2. **Channel derivation** -- :func:`derive_channel` is the pure core of grok's
   ``channel_label`` (the wiring ``version.py`` defers to a future round).

The deferred/YAGNI symbols (all ``fetch_*`` network probes, disk-cache I/O,
``channel_name``/``channel_label`` OnceLock+disk, ``UpdateConfig``,
``UpdateStatus``, ``enforce_minimum_version*``, ``MinimumVersionError``
multi-variant enum, ``auto_update``) are documented in ``update_check.py`` and
not tested here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from minimax_code.update_check import (
    CachedVersion,
    InvalidMinimumVersion,
    MinimumDecisionKind,
    MinimumVersionDecision,
    TargetBelowFloor,
    _cmp_key,
    apply_floor_inner,
    check_install_target_inner,
    derive_channel,
    evaluate_minimum_version,
    pick_target_version,
    semver_max,
    version_from_versioned_binary_name,
)
from minimax_code.version import Version

# ---------------------------------------------------------------------------
# semver.org §11 pre-release ordering -- the R42 version.py:88-91 gap-closure.
#
# version.Version deliberately ships no ordered comparison; this module is the
# update-check that needs it, so the ordering lives in _cmp_key. §11.4's
# canonical precedence chain (lifted verbatim from semver.org) is the strongest
# correctness signal: a single transposed identifier would break a link.
# ---------------------------------------------------------------------------


def test_release_ranks_above_pre_release_at_same_mmp() -> None:
    """§11.4: ``1.0.0 > 1.0.0-alpha`` -- the exact example version.py names."""
    # version.py:88-91 declares this undecided ("YAGNI until an update-check
    # requires deciding 0.8.0-alpha < 0.8.0"); _cmp_key decides it.
    assert _cmp_key(Version.parse("0.8.0-alpha")) < _cmp_key(Version.parse("0.8.0"))
    assert _cmp_key(Version.parse("1.0.0-alpha")) < _cmp_key(Version.parse("1.0.0"))


def test_semver_org_canonical_pre_release_chain() -> None:
    """§11.4 verbatim precedence: alpha < alpha.1 < alpha.beta < beta < beta.2
    < beta.11 < rc.1 < 1.0.0. Each adjacent pair must be strictly ordered."""
    chain = [
        "1.0.0-alpha",
        "1.0.0-alpha.1",
        "1.0.0-alpha.beta",
        "1.0.0-beta",
        "1.0.0-beta.2",
        "1.0.0-beta.11",
        "1.0.0-rc.1",
        "1.0.0",
    ]
    keys = [_cmp_key(Version.parse(v)) for v in chain]
    for left, right in pairwise(keys):
        assert left < right


def test_numeric_identifier_compares_numerically_not_lexically() -> None:
    """§11.4.2: beta.2 < beta.11 (numeric 2 < 11, not lexical '2' > '11')."""
    assert _cmp_key(Version.parse("1.0.0-beta.2")) < _cmp_key(Version.parse("1.0.0-beta.11"))


def test_numeric_identifier_ranks_below_alphanumeric() -> None:
    """§11.4.3: numeric identifiers always have lower precedence than non-numeric
    (so alpha.1 < alpha.beta)."""
    assert _cmp_key(Version.parse("1.0.0-alpha.1")) < _cmp_key(Version.parse("1.0.0-alpha.beta"))


def test_shorter_pre_release_ranks_below_longer_when_prefix_equal() -> None:
    """§11.4.1: a larger set of pre-release fields ranks higher when all
    preceding identifiers are equal (alpha < alpha.1)."""
    assert _cmp_key(Version.parse("1.0.0-alpha")) < _cmp_key(Version.parse("1.0.0-alpha.1"))


def test_build_metadata_does_not_affect_precedence() -> None:
    """§10: build metadata MUST be ignored when determining precedence."""
    assert _cmp_key(Version.parse("1.0.0+build.1")) == _cmp_key(Version.parse("1.0.0+build.2"))
    assert _cmp_key(Version.parse("1.0.0")) == _cmp_key(Version.parse("1.0.0+x"))


def test_major_minor_patch_compared_numerically() -> None:
    """§11.1-3: major, then minor, then patch, each numerically."""
    assert _cmp_key(Version.parse("1.2.3")) < _cmp_key(Version.parse("1.2.4"))
    assert _cmp_key(Version.parse("1.2.9")) < _cmp_key(Version.parse("1.3.0"))
    assert _cmp_key(Version.parse("1.9.9")) < _cmp_key(Version.parse("2.0.0"))


def test_cmp_key_is_total_order() -> None:
    """The cmp keys are mutually comparable tuples (no TypeError on mixed
    numeric/alphanumeric identifiers) -- regression guard for the tier triple."""
    versions = ["1.0.0", "1.0.0-1", "1.0.0-a", "1.0.0-a.b", "0.9.0", "2.0.0"]
    keys = [_cmp_key(Version.parse(v)) for v in versions]
    # Sorting must not raise and must be deterministic.
    assert sorted(keys) == sorted(keys)


# ---------------------------------------------------------------------------
# semver_max (grok ``semver_max``).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        # release vs pre-release at same mmp -> release wins
        ("0.1.148-alpha.3", "0.1.148", "0.1.148"),
        # pre-release identifier ordering: alpha.1 < alpha.3
        ("0.1.148-alpha.1", "0.1.148-alpha.3", "0.1.148-alpha.3"),
        # higher minor beats pre-release at lower minor
        ("0.1.149-alpha.1", "0.1.148", "0.1.149-alpha.1"),
        ("0.1.140", "0.1.141", "0.1.141"),
        ("0.0.0", "0.0.1", "0.0.1"),
        ("0.99.99", "1.0.0", "1.0.0"),
        ("1.0.0", "1.0.0", "1.0.0"),
        ("2.0.0", "1.9.9", "2.0.0"),
        # rc.1 > beta.11 (semver §11.4 chain)
        ("1.0.0-rc.1", "1.0.0-beta.11", "1.0.0-rc.1"),
    ],
)
def test_semver_max_matrix(a: str, b: str, expected: str) -> None:
    assert semver_max(a, b) == expected


def test_semver_max_is_symmetric() -> None:
    """Order of arguments must not change the result (commutative max)."""
    assert semver_max("0.1.148", "0.1.149") == semver_max("0.1.149", "0.1.148")


@pytest.mark.parametrize("bad", ["garbage", "0.1", "0.1.0.0", "v0.1.0", ""])
def test_semver_max_invalid_input_returns_err(bad: str) -> None:
    """grok ``test_semver_max_invalid_input_returns_err``: bad semver -> ValueError."""
    with pytest.raises(ValueError):
        semver_max(bad, "0.1.148")
    with pytest.raises(ValueError):
        semver_max("0.1.148", bad)


# ---------------------------------------------------------------------------
# derive_channel (grok ``derive_channel``) -- the R42 version.py:29-33
# channel-label wiring gap-closure (pure core; the disk/OnceLock half is YAGNI).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "stable", "expected"),
    [
        # current ahead of stable -> alpha
        ("0.1.220-alpha.2", "0.1.219", "alpha"),
        ("0.2.5", "0.2.3", "alpha"),
        ("0.2.0", "0.1.219", "alpha"),
        ("0.1.221", "0.1.220", "alpha"),
        # release outranks pre-release at same mmp -> current ahead
        ("1.0.0", "1.0.0-rc.1", "alpha"),
        # current at or behind stable -> stable
        ("0.1.219", "0.1.219", "stable"),
        ("0.1.220-alpha.2", "0.1.220-alpha.2", "stable"),
        ("0.1.220-alpha.2", "0.1.220", "stable"),
        ("0.1.220-alpha.2", "0.2.0", "stable"),
        ("0.1.219", "0.1.220", "stable"),
        ("1.0.0-rc.1", "1.0.0", "stable"),
        ("0.0.0", "0.0.0", "stable"),
        ("0.1.220", "0.1.220", "stable"),
        ("0.1.220-alpha.1", "0.1.220-alpha.2", "stable"),
        # unparseable inputs -> None
        ("garbage", "0.1.219", None),
        ("0.1.219", "garbage", None),
    ],
)
def test_derive_channel_matrix(current: str, stable: str, expected: str | None) -> None:
    assert derive_channel(current, stable) == expected


# ---------------------------------------------------------------------------
# version_from_versioned_binary_name (grok ``version_from_versioned_binary_name``).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "bin_prefix", "expected"),
    [
        # internal layout: <prefix>-<version>-<platform>-<arch>
        ("grok-0.2.46-darwin-arm64", "grok", "0.2.46"),
        ("grok-0.2.46-macos-aarch64", "grok", "0.2.46"),
        ("grok-0.2.46-windows-x86_64", "grok", "0.2.46"),
        ("grok-0.2.46-linux-amd64", "grok", "0.2.46"),
        # pre-release version slice
        ("grok-0.1.220-alpha.4-linux-x86_64", "grok", "0.1.220-alpha.4"),
        # no platform suffix (npm-ish)
        ("grok-0.1.220-alpha.4", "grok", "0.1.220-alpha.4"),
        ("grok-0.2.46", "grok", "0.2.46"),
        # custom prefix
        ("grok-pager-0.1.5-darwin-arm64", "grok-pager", "0.1.5"),
        # non-version suffixes -> None (validated as semver)
        ("grok-pager-0.1.5-darwin-arm64", "grok", None),
        ("grok-latest", "grok", None),
        # no version at all
        ("grok", "grok", None),
        # wrong / empty prefix
        ("other-0.2.46-darwin-arm64", "grok", None),
        ("", "grok", None),
    ],
)
def test_version_from_versioned_binary_name(
    name: str, bin_prefix: str, expected: str | None
) -> None:
    assert version_from_versioned_binary_name(name, bin_prefix) == expected


# ---------------------------------------------------------------------------
# evaluate_minimum_version + MinimumVersionDecision (grok pure floor check).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "minimum", "kind"),
    [
        ("0.1.100", None, MinimumDecisionKind.ALLOW),
        ("0.1.100", "", MinimumDecisionKind.ALLOW),
        ("0.1.100", "   ", MinimumDecisionKind.ALLOW),
        ("0.1.100", "0.1.100", MinimumDecisionKind.ALLOW),
        ("0.2.0", "0.1.100", MinimumDecisionKind.ALLOW),
        ("0.1.99", "0.1.100", MinimumDecisionKind.BELOW_MINIMUM),
        # pre-release at same mmp as a release floor -> below
        ("0.1.100-alpha", "0.1.100", MinimumDecisionKind.BELOW_MINIMUM),
        # unparseable current -> fail-closed (BelowMinimum), not Allow
        ("garbage", "0.1.100", MinimumDecisionKind.BELOW_MINIMUM),
    ],
)
def test_evaluate_minimum_version_decisions(
    current: str, minimum: str | None, kind: MinimumDecisionKind
) -> None:
    assert evaluate_minimum_version(current, minimum).kind is kind


def test_below_minimum_carries_canonical_current_and_minimum() -> None:
    """BelowMinimum carries the canonical (str-normalized) versions, not raw."""
    decision = evaluate_minimum_version("0.1.99", "0.1.100")
    assert decision.kind is MinimumDecisionKind.BELOW_MINIMUM
    assert decision.current == "0.1.99"
    assert decision.minimum == "0.1.100"


def test_below_minimum_for_unparseable_current_keeps_raw_current() -> None:
    """When current is unparseable, current is the raw string (not canonical)."""
    decision = evaluate_minimum_version("garbage", "0.1.100")
    assert decision.kind is MinimumDecisionKind.BELOW_MINIMUM
    assert decision.current == "garbage"
    assert decision.minimum == "0.1.100"


def test_evaluate_minimum_version_floor_is_stripped() -> None:
    """A floor with surrounding whitespace is trimmed before parsing."""
    assert evaluate_minimum_version("0.1.100", "  0.1.100  ").kind is MinimumDecisionKind.ALLOW


def test_evaluate_minimum_version_invalid_floor_raises() -> None:
    """An unparseable floor raises InvalidMinimumVersion (grok ``InvalidMinimum``)."""
    with pytest.raises(InvalidMinimumVersion) as exc_info:
        evaluate_minimum_version("0.1.100", "garbage")
    assert exc_info.value.value == "garbage"


def test_evaluate_minimum_version_invalid_floor_strips_value() -> None:
    """The InvalidMinimumVersion.value is the trimmed floor string."""
    with pytest.raises(InvalidMinimumVersion) as exc_info:
        evaluate_minimum_version("0.1.100", "  garbage  ")
    assert exc_info.value.value == "garbage"


def test_minimum_version_decision_factories() -> None:
    """allow() / below_minimum() constructors populate kind + payload."""
    allow = MinimumVersionDecision.allow()
    assert allow.kind is MinimumDecisionKind.ALLOW
    assert allow.current is None and allow.minimum is None
    below = MinimumVersionDecision.below_minimum(current="0.1.0", minimum="0.2.0")
    assert below.kind is MinimumDecisionKind.BELOW_MINIMUM
    assert below.current == "0.1.0" and below.minimum == "0.2.0"


def test_minimum_version_decision_is_frozen() -> None:
    """Decision records are immutable (mirrors grok's owned enum payload)."""
    from dataclasses import FrozenInstanceError

    decision = MinimumVersionDecision.below_minimum(current="0.1.0", minimum="0.2.0")
    with pytest.raises(FrozenInstanceError):
        decision.current = "0.9.0"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# pick_target_version (grok ``pick_target_version``).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("latest", "minimum", "expected"),
    [
        ("0.1.200", "0.1.150", "0.1.200"),
        ("0.1.140", "0.1.150", "0.1.150"),
        (None, "0.1.150", "0.1.150"),
        # unparseable latest decays to None -> minimum
        ("garbage", "0.1.150", "0.1.150"),
        # pre-release latest below release minimum -> minimum
        ("0.1.200-alpha", "0.1.200", "0.1.200"),
        # unparseable minimum is returned verbatim (grok returns minimum raw)
        ("0.1.200", "not-a-version", "not-a-version"),
    ],
)
def test_pick_target_returns_max_of_latest_and_minimum(
    latest: str | None, minimum: str, expected: str
) -> None:
    assert pick_target_version(latest, minimum) == expected


# ---------------------------------------------------------------------------
# check_install_target_inner / apply_floor_inner (grok pure floor application).
# ---------------------------------------------------------------------------


def test_check_install_target_inner_noop_without_floor() -> None:
    """No floor -> any target allowed (no raise)."""
    check_install_target_inner("0.1.50", None)
    check_install_target_inner("garbage", None)


def test_check_install_target_inner_allows_target_at_or_above_floor() -> None:
    check_install_target_inner("0.1.150", "0.1.100")  # above
    check_install_target_inner("0.1.100", "0.1.100")  # equal


def test_check_install_target_inner_rejects_target_below_floor() -> None:
    """grok ``install_target_helpers_consult_floor``: target < floor -> TargetBelowFloor."""
    with pytest.raises(TargetBelowFloor) as exc_info:
        check_install_target_inner("0.1.50", "0.1.100")
    assert exc_info.value.target == "0.1.50"
    assert exc_info.value.minimum == "0.1.100"


def test_check_install_target_inner_invalid_floor_raises() -> None:
    with pytest.raises(InvalidMinimumVersion):
        check_install_target_inner("0.1.100", "garbage")


def test_apply_floor_inner_passthrough_without_floor() -> None:
    assert apply_floor_inner("0.1.50", None) == "0.1.50"


def test_apply_floor_inner_keeps_target_at_or_above_floor() -> None:
    assert apply_floor_inner("0.1.200", "0.1.100") == "0.1.200"
    assert apply_floor_inner("0.1.100", "0.1.100") == "0.1.100"


def test_apply_floor_inner_bumps_target_below_floor() -> None:
    """grok: when target < floor, the floor is returned (bump up)."""
    assert apply_floor_inner("0.1.50", "0.1.100") == "0.1.100"


def test_apply_floor_inner_invalid_floor_raises() -> None:
    with pytest.raises(InvalidMinimumVersion):
        apply_floor_inner("0.1.50", "garbage")


# ---------------------------------------------------------------------------
# CachedVersion (grok ``GrokVersion``) -- TTL freshness + serde round-trip.
# The cache *file* I/O is YAGNI; the record shape + freshness gate land here.
# ---------------------------------------------------------------------------


_NOW = datetime(2026, 7, 21, 10, 30, 0, tzinfo=UTC)


def test_cached_version_new_stamps_iso8601_checked_at() -> None:
    """new() formats checked_at as ISO 8601 from the injected clock."""
    record = CachedVersion.new(version="0.1.200", stable_version="0.1.199", now=_NOW)
    assert record.version == "0.1.200"
    assert record.stable_version == "0.1.199"
    assert record.checked_at == _NOW.isoformat()


def test_cached_version_new_defaults_stable_none() -> None:
    record = CachedVersion.new(version="0.1.200", stable_version=None, now=_NOW)
    assert record.stable_version is None


@pytest.mark.parametrize(
    ("delta", "ttl", "fresh"),
    [
        # checked_at == now -> within any positive ttl
        (timedelta(seconds=0), timedelta(seconds=60), True),
        # just inside ttl -> fresh (boundary is strict <)
        (timedelta(seconds=29), timedelta(seconds=30), True),
        # exactly ttl -> stale (strict less-than)
        (timedelta(seconds=30), timedelta(seconds=30), False),
        # past ttl -> stale
        (timedelta(seconds=31), timedelta(seconds=30), False),
        # zero ttl is always stale (0 < 0 is False)
        (timedelta(seconds=0), timedelta(seconds=0), False),
    ],
)
def test_is_fresh_ttl_boundaries(delta: timedelta, ttl: timedelta, fresh: bool) -> None:
    """grok ``test_is_fresh_ttl_boundaries``: now - checked_at < ttl (strict)."""
    record = CachedVersion.new(version="0.1.200", stable_version=None, now=_NOW)
    assert record.is_fresh(_NOW + delta, ttl) is fresh


def test_is_fresh_rejects_future_timestamp() -> None:
    """grok ``test_is_fresh_rejects_future_timestamp``: a future checked_at (clock
    skew) is never fresh, regardless of ttl."""
    future = _NOW + timedelta(seconds=600)
    record = CachedVersion.new(version="0.1.200", stable_version=None, now=future)
    assert record.is_fresh(_NOW, timedelta(seconds=30)) is False
    # ... even with a huge ttl the future stamp stays unfresh.
    assert record.is_fresh(_NOW, timedelta(days=365)) is False


def test_is_fresh_rejects_unparseable_checked_at() -> None:
    record = CachedVersion(version="0.1.200", checked_at="not-rfc3339")
    assert record.is_fresh(_NOW, timedelta(seconds=30)) is False


def test_is_fresh_accepts_z_suffix_utc() -> None:
    """RFC 3339 'Z' UTC suffixes parse (normalized for datetime.fromisoformat)."""
    record = CachedVersion(version="0.1.200", checked_at="2026-07-21T10:25:00Z")
    # 5 minutes before _NOW, ttl 10 minutes -> fresh
    assert record.is_fresh(_NOW, timedelta(minutes=10)) is True


def test_cached_version_round_trip_preserves_fields() -> None:
    """to_mapping -> from_mapping is identity (serde round-trip)."""
    record = CachedVersion.new(version="0.1.200", stable_version="0.1.199", now=_NOW)
    assert CachedVersion.from_mapping(record.to_mapping()) == record


def test_cached_version_round_trip_without_stable_version() -> None:
    """A None stable_version round-trips (omitted on serialize, None on parse)."""
    record = CachedVersion.new(version="0.1.200", stable_version=None, now=_NOW)
    mapping = record.to_mapping()
    assert "stable_version" not in mapping  # skip-if-None on serialize
    assert CachedVersion.from_mapping(mapping) == record


def test_cached_version_from_mapping_legacy_payload() -> None:
    """grok ``test_version_json_backward_compat``: old cache (no stable_version)
    deserializes with stable_version=None."""
    legacy = {"version": "0.1.180", "checked_at": "2026-04-22T10:30:00Z"}
    record = CachedVersion.from_mapping(legacy)
    assert record is not None
    assert record.version == "0.1.180"
    assert record.stable_version is None
    assert record.checked_at == "2026-04-22T10:30:00Z"


def test_cached_version_from_mapping_ignores_unknown_fields() -> None:
    """Forward-compat: unknown fields are dropped (mirrors serde's ignore)."""
    record = CachedVersion.from_mapping(
        {"version": "0.1.200", "checked_at": _NOW.isoformat(), "future_field": "x"}
    )
    assert record == CachedVersion(
        version="0.1.200", checked_at=_NOW.isoformat(), stable_version=None
    )


def test_cached_version_from_mapping_rejects_missing_required() -> None:
    """Missing version or checked_at -> None (malformed payload)."""
    assert CachedVersion.from_mapping({"version": "0.1.180"}) is None
    assert CachedVersion.from_mapping({"checked_at": "2026-04-22T10:30:00Z"}) is None


def test_cached_version_from_mapping_rejects_non_string() -> None:
    """Non-string version/checked_at -> None (type mismatch)."""
    assert CachedVersion.from_mapping({"version": 1, "checked_at": "x"}) is None
    assert CachedVersion.from_mapping({"version": "0.1.0", "checked_at": 123}) is None


def test_cached_version_from_mapping_rejects_non_mapping() -> None:
    assert CachedVersion.from_mapping(None) is None
    assert CachedVersion.from_mapping("not a dict") is None
    assert CachedVersion.from_mapping(["a", "list"]) is None


def test_cached_version_is_frozen() -> None:
    from dataclasses import FrozenInstanceError

    record = CachedVersion(version="0.1.200", checked_at="x")
    with pytest.raises(FrozenInstanceError):
        record.version = "0.2.0"  # type: ignore[misc]
