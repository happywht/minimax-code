"""Version-comparison + update-policy pure logic -- fusion of grok's
``xai-grok-update`` (R196, ``version.rs`` + ``minimum_version.rs`` pure subset,
crate first round).

``xai-grok-update`` is grok's self-update layer (channel pointers, npm/gh/GCS
probes, minimum-version enforcement, on-disk binary probing). The crate ships
~1100 lines across four files; this module migrates the **pure-logic core** --
semver comparison, channel derivation, versioned-binary-name parsing, minimum-
version floor decisions, and the version-cache data type -- and records every
I/O / reqwest / tokio / shell-coupled symbol as YAGNI or deferred.

Closes two R42 commitments
----------------------------

:mod:`minimax_code.version` (R42) left two deliberate gaps; this module is the
consumer that closes both:

1. **Pre-release ordering** -- ``version.py`` declares ``Version.pre`` without
   ordered comparison ("YAGNI until an update-check requires deciding
   ``0.8.0-alpha < 0.8.0``"). :func:`_cmp_key` implements semver.org §11 here
   (in the module that *needs* comparison), leaving :class:`version.Version`
   untouched (R42's leaf decision stands).
2. **Update-check wiring** -- ``version.py`` ships :func:`display_version` /
   :func:`display_version_with_commit` (both take a ``channel_label``) but
   defers the channel-label *derivation* to a future round.
   :func:`derive_channel` is that derivation's pure core (the I/O half --
   reading the cached stable pointer -- stays YAGNI here).

Migrated (this round, pure logic)
---------------------------------

* :func:`_cmp_key` -- semver.org §11 comparison key over :class:`version.Version`
  (build metadata ignored per §10; pre-release identifier ordering per §11).
* :func:`semver_max` (``version.rs``) -- semver-greater of two version strings.
* :func:`derive_channel` (``version.rs``) -- ``"alpha"`` / ``"stable"`` / ``None``
  from current vs cached-stable pointer.
* :func:`version_from_versioned_binary_name` (``version.rs``) -- extract the
  ``<version>`` slice from a ``grok-<version>-<platform>`` binary file name.
* :class:`CachedVersion` (``version.rs`` ``GrokVersion``) -- the on-disk cache
  record (version + stable pointer + checked-at) with TTL freshness + serde
  round-trip; the cache *file* I/O is YAGNI, the record shape is pure.
* :func:`pick_target_version` (``minimum_version.rs``) -- ``max(latest, minimum)``.
* :class:`MinimumVersionDecision` + :func:`evaluate_minimum_version`
  (``minimum_version.rs``) -- pure floor check.
* :func:`check_install_target_inner` / :func:`apply_floor_inner`
  (``minimum_version.rs``) -- floor application on an install target.

YAGNI / deferred (this round)
-----------------------------

* **Network probes** -- ``fetch_npm_version`` / ``fetch_gcs_version`` /
  ``fetch_gh_release_version`` / ``fetch_latest_version`` / ``get_latest_version``
  / ``try_fetch_stable_pointer`` (reqwest + ``tokio::process::Command`` npm/gh).
  The platform has no auto-updater; the LLM transport (``agent/llm.py``) owns
  its own httpx client. Multi-round when an update feature lands.
* **Disk cache I/O** -- ``write_version_cache`` / ``cached_stable_version`` /
  ``is_version_cache_fresh`` (``tokio::fs`` / ``std::fs`` over ``~/.grok/
  version.json``). The pure record type (:class:`CachedVersion`) lands here; the
  file read/write is deferred to the update-feature round.
* **Process-latched channel** -- ``channel_name`` / ``channel_label``
  (``OnceLock`` over the cached stable pointer). The pure derivation
  (:func:`derive_channel`) lands here; the process cache + disk read is YAGNI.
* **On-disk binary probe** -- ``installed_on_disk_version`` (symlink read of
  ``~/.grok/bin/grok``). The platform has no managed-binary layout.
* :class:`UpdateConfig` -- depends on ``GrokBuildEnvironment`` (unmigrated).
* :class:`UpdateStatus` (``auto_update.rs``) -- download/install state machine.
* ``enforce_minimum_version`` / ``enforce_minimum_version_or_exit`` -- I/O +
  ``std::process::exit``; the pure decision they consume lands here.
* ``MinimumVersionError`` multi-variant enum -- ``thiserror::Error`` ``Display``
  + I/O-failure variants (``AutoUpdateDisabled`` / ``NoInstaller`` /
  ``UpgradeFailed`` / ``NoSatisfyingVersion`` / ``NoReleaseFound``). Only the
  two pure-logic failure modes are migrated, as :class:`InvalidMinimumVersion`
  (unparseable floor) + :class:`TargetBelowFloor` (target < floor).
* ``check_install_target`` / ``apply_floor`` (the public wrappers) -- they call
  ``config::resolve_minimum_version`` (shell config loader, unmigrated); the
  ``_inner`` pure cores land here and are what the wrappers reduce to.
* ``get_installer`` / ``run_install_script`` / ``auto_update`` -- download +
  install. Entirely YAGNI until an update feature ships.
* ``startup_timer!`` / ``tracing`` macros -- route to ``xai_grok_telemetry``
  (R127-R132 owns startup instrumentation).

Product-fusion renames
----------------------

* type ``GrokVersion`` -> :class:`CachedVersion` (the record is a version-cache
  entry, not a grok identity token; the platform-neutral name records the
  unmigrated ``xai_grok_shell`` coupling that the source type carried).
* env ``GROK_TEST_VERSION`` -> ``MINIMAX_CODE_TEST_VERSION`` is already honored
  by :func:`version.installed` (R42); this module consumes
  :func:`version.Version.parse` so the rename flows through automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from minimax_code.version import Version

# ---------------------------------------------------------------------------
# semver.org §11 comparison key.
#
# `version.Version` (R42) deliberately ships no ordered comparison ("YAGNI until
# an update-check requires deciding 0.8.0-alpha < 0.8.0"). This module is that
# update-check, so the ordering lives here -- R42's leaf stays untouched and
# the parsing (regex + field shape) is reused (DRY: one semver parser).
# ---------------------------------------------------------------------------

#: semver.org §11.4: a release with no pre-release ranks HIGHER than one with a
#: pre-release at the same major.minor.patch. Encoded as a tier so a single
#: tuple comparison covers both the cross-tier and within-tier cases.
_RELEASE_TIER: int = 1  # no pre-release -> ranks higher
_PRE_RELEASE_TIER: int = 0  # has pre-release -> ranks lower


def _pre_release_identifier_key(identifier: str) -> tuple[int, int, str]:
    """Sort key for one pre-release identifier (semver.org §11.4).

    Numeric identifiers are compared numerically and rank *below* alphanumeric
    identifiers. Each key is a uniform ``(tier, numeric, text)`` triple so any
    two identifiers are mutually comparable (tuples never mismatch on type).

    * numeric (``"3"``) -> ``(0, 3, "")`` -- tier 0, value 3, no text.
    * alphanumeric (``"alpha"``) -> ``(1, 0, "alpha")`` -- tier 1, no value,
      lexical text.
    """
    if identifier.isascii() and identifier.isdigit():
        return (0, int(identifier), "")
    return (1, 0, identifier)


def _cmp_key(version: Version) -> tuple[int, int, int, int, tuple[tuple[int, int, str], ...]]:
    """Total-order comparison key for a :class:`version.Version` (semver.org §11).

    Format: ``(major, minor, patch, tier, pre_release_identifiers)``.

    * ``major.minor.patch`` compared numerically (§11.1-3).
    * ``tier`` -- :data:`_RELEASE_TIER` when there is no pre-release (ranks
      higher), else :data:`_PRE_RELEASE_TIER` (§11.4).
    * ``pre_release_identifiers`` -- per-identifier keys joined as a tuple,
      compared lexicographically (§11.4.1-3). Empty tuple for releases.

    Build metadata (§10) is intentionally ignored -- it never participates in
    precedence.
    """
    base = (version.major, version.minor, version.patch)
    if version.pre is None:
        return (*base, _RELEASE_TIER, ())
    identifiers = tuple(_pre_release_identifier_key(pid) for pid in version.pre.split("."))
    return (*base, _PRE_RELEASE_TIER, identifiers)


def semver_max(a: str, b: str) -> str:
    """Return the semver-greater of two version strings (grok ``semver_max``).

    Both inputs must parse as semver (raises ``ValueError`` from
    :func:`version.Version.parse` otherwise, mirroring grok's ``Result``). The
    returned string is the canonical form of the greater version
    (:class:`Version.__str__`).
    """
    va = Version.parse(a)
    vb = Version.parse(b)
    return str(va if _cmp_key(va) >= _cmp_key(vb) else vb)


def derive_channel(current: str, stable: str) -> str | None:
    """Derive the release channel from current vs cached-stable (grok ``derive_channel``).

    Pure comparison: returns ``"alpha"`` when ``current > stable``,
    ``"stable"`` when ``current <= stable``, or ``None`` when either version
    fails to parse. This is the pure core of grok's ``channel_label`` -- the
    process-latched ``OnceLock`` + disk read of the stable pointer is YAGNI.
    """
    try:
        current_v = Version.parse(current)
        stable_v = Version.parse(stable)
    except ValueError:
        return None
    if _cmp_key(current_v) > _cmp_key(stable_v):
        return "alpha"
    return "stable"


# ---------------------------------------------------------------------------
# Versioned-binary-name parsing (grok ``version_from_versioned_binary_name``).
#
# The managed-install layout names binaries ``<prefix>-<version>-<platform>``;
# this extracts the ``<version>`` slice and validates it as semver. Shared by
# the (YAGNI) on-disk probe + download cleanup -- the parser lands here so both
# future consumers share one naming authority.
# ---------------------------------------------------------------------------

#: Platform-OS tokens that terminate the version slice of a versioned binary
#: name (grok ``PLATFORM_OS``). Everything between ``<prefix>-`` and the first
#: platform token is the version (validated as semver so ``grok-latest`` /
#: ``grok-pager-*`` reject instead of returning garbage).
_PLATFORM_OS_TOKENS: frozenset[str] = frozenset({"macos", "linux", "darwin", "windows"})


def version_from_versioned_binary_name(name: str, bin_prefix: str) -> str | None:
    """Extract the ``<version>`` portion of a versioned binary name (grok).

    Handles the internal layout (``grok-0.1.150-macos-aarch64``, including
    pre-releases: ``grok-0.1.150-alpha.1-linux-x86_64`` -> ``0.1.150-alpha.1``)
    and the npm layout without a platform suffix (``grok-0.1.150``). Returns
    ``None`` when the name lacks the prefix, the version slice is not valid
    semver, or the name carries no version (``grok-latest`` / bare ``grok``).
    """
    if not name.startswith(bin_prefix):
        return None
    suffix = name[len(bin_prefix):]
    if not suffix.startswith("-"):
        return None
    suffix = suffix[1:]  # strip the leading "-"
    parts = suffix.split("-")
    platform_start = next(
        (i for i, part in enumerate(parts) if part in _PLATFORM_OS_TOKENS),
        len(parts),
    )
    version_str = "-".join(parts[:platform_start])
    try:
        Version.parse(version_str)
    except ValueError:
        return None
    return version_str


# ---------------------------------------------------------------------------
# CachedVersion -- the on-disk version-cache record (grok ``GrokVersion``).
#
# Pure data + TTL logic + serde round-trip. The cache *file* I/O
# (write_version_cache / cached_stable_version / is_version_cache_fresh) is
# YAGNI -- the record shape lands here so a future update feature has a tested
# serialization contract ready.
# ---------------------------------------------------------------------------


def _parse_rfc3339(text: str) -> datetime | None:
    """Parse an RFC 3339 / ISO 8601 timestamp (grok ``OffsetDateTime::parse``).

    Returns ``None`` when the string is not a parseable timestamp. ``Z`` UTC
    suffixes are normalized for ``datetime.fromisoformat`` (Python < 3.12 does
    not accept a bare ``Z``).
    """
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True, slots=True)
class CachedVersion:
    """The on-disk version-cache record (grok ``GrokVersion``).

    Carries the version last seen, the cached stable-channel pointer (so
    :func:`derive_channel` can label ``[alpha]`` vs ``[stable]`` without network
    I/O), and the ``checked_at`` timestamp. :meth:`is_fresh` is the TTL gate the
    auto-updater consults before re-probing.

    Field order differs from grok's struct (``version, stable_version,
    checked_at``) to satisfy Python's "default-after-required" dataclass rule:
    ``stable_version`` has a default, so it trails the two required fields. JSON
    order is irrelevant (objects), and :meth:`to_mapping` emits grok's wire
    order for on-disk parity.
    """

    version: str
    checked_at: str
    stable_version: str | None = None

    @classmethod
    def new(
        cls,
        version: str,
        stable_version: str | None,
        now: datetime,
    ) -> CachedVersion:
        """Build a record stamped at ``now`` (grok ``GrokVersion::new``).

        ``now`` is formatted ISO 8601 (the RFC 3339 subset). The caller owns the
        clock so tests can inject a fixed time; grok reads
        ``OffsetDateTime::now_utc`` at the call site.
        """
        return cls(
            version=version,
            checked_at=now.isoformat(),
            stable_version=stable_version,
        )

    def is_fresh(self, now: datetime, ttl: timedelta) -> bool:
        """True when ``checked_at`` parses, is not in the future, and is within TTL.

        (grok ``GrokVersion::is_fresh``.) Clock-skew guard: a future
        ``checked_at`` (NTP warp / skew) is never fresh -- without this guard a
        skewed clock would disable auto-update indefinitely. Boundary is strict
        (``now - checked_at < ttl``): at exactly TTL the record is stale.
        """
        checked_at = _parse_rfc3339(self.checked_at)
        if checked_at is None:
            return False
        if checked_at > now:
            return False  # clock-skew guard
        return (now - checked_at) < ttl

    @classmethod
    def from_mapping(cls, data: object) -> CachedVersion | None:
        """Tolerant constructor from a JSON-decoded mapping (serde equivalent).

        Returns ``None`` when ``data`` is not a mapping, ``version`` /
        ``checked_at`` are absent or non-string, or the payload is otherwise
        malformed. ``stable_version`` is optional (absent -> ``None``); unknown
        fields are ignored (forward-compat with future cache formats).
        """
        if not isinstance(data, dict):
            return None
        version = data.get("version")
        checked_at = data.get("checked_at")
        if not isinstance(version, str) or not isinstance(checked_at, str):
            return None
        stable_raw = data.get("stable_version")
        stable_version = stable_raw if isinstance(stable_raw, str) else None
        return cls(version=version, checked_at=checked_at, stable_version=stable_version)

    def to_mapping(self) -> dict[str, str]:
        """Serialize to a JSON-ready mapping in grok's on-disk field order.

        ``stable_version`` is omitted when ``None`` (mirrors grok's
        ``#[serde(skip_serializing_if = "Option::is_none")]``); older readers
        that lack the field deserialize it as ``None`` (``#[serde(default)]``).
        """
        mapping: dict[str, str] = {"version": self.version, "checked_at": self.checked_at}
        if self.stable_version is not None:
            mapping["stable_version"] = self.stable_version
        return mapping


# ---------------------------------------------------------------------------
# Minimum-version floor logic (grok ``minimum_version.rs`` pure subset).
#
# Two pure policies over the comparison key: "is current above the floor?" and
# "bump a target up to the floor". The I/O wrappers (resolve_floor_or_error /
# enforce_minimum_version_or_exit) and the multi-variant error enum are YAGNI;
# only the two pure-logic failure modes (invalid floor / target below floor)
# survive, as dedicated exceptions.
# ---------------------------------------------------------------------------


class MinimumDecisionKind(StrEnum):
    """Outcome kind for :class:`MinimumVersionDecision` (grok enum variants)."""

    ALLOW = "allow"
    BELOW_MINIMUM = "below_minimum"


@dataclass(frozen=True, slots=True)
class MinimumVersionDecision:
    """Result of comparing a running version against a configured floor (grok).

    grok models this as a two-variant enum (``Allow`` / ``BelowMinimum { current,
    minimum }``); the platform mirrors it as a frozen dataclass + kind enum so
    the payload travels with the value (Python enums carry data awkwardly).
    ``current`` / ``minimum`` are populated only for
    :attr:`MinimumDecisionKind.BELOW_MINIMUM`.
    """

    kind: MinimumDecisionKind
    current: str | None = None
    minimum: str | None = None

    @classmethod
    def allow(cls) -> MinimumVersionDecision:
        """Floor satisfied (or unset)."""
        return cls(kind=MinimumDecisionKind.ALLOW)

    @classmethod
    def below_minimum(cls, current: str, minimum: str) -> MinimumVersionDecision:
        """Current version is below the floor (or unparseable -> block)."""
        return cls(kind=MinimumDecisionKind.BELOW_MINIMUM, current=current, minimum=minimum)


class InvalidMinimumVersion(ValueError):
    """The configured minimum-version floor is not valid semver (grok ``InvalidMinimum``).

    Only the ``value`` chains through (the ``source`` semver error is folded into
    the raise-from chain); grok's ``Display`` wording is not migrated (it is
    user-facing copy for the shell launcher, which is YAGNI here).
    """

    def __init__(self, value: str) -> None:
        self.value = value
        super().__init__(f"invalid minimum version: {value!r}")


class TargetBelowFloor(ValueError):
    """An explicit install target is below the configured floor (grok ``TargetBelowFloor``)."""

    def __init__(self, target: str, minimum: str) -> None:
        self.target = target
        self.minimum = minimum
        super().__init__(f"install target {target!r} is below the configured floor {minimum!r}")


def evaluate_minimum_version(
    current_version: str,
    minimum_version: str | None,
) -> MinimumVersionDecision:
    """Pure check of ``current_version`` against an optional floor (grok).

    Empty / whitespace-only ``minimum_version`` is treated as unset (Allow). An
    unparseable floor raises :class:`InvalidMinimumVersion`. An unparseable
    *current* blocks (BelowMinimum) rather than letting an unverifiable build
    through -- grok's deliberate fail-closed for dev builds.
    """
    minimum = minimum_version.strip() if minimum_version is not None else ""
    if minimum == "":
        return MinimumVersionDecision.allow()
    try:
        parsed_min = Version.parse(minimum)
    except ValueError as exc:
        raise InvalidMinimumVersion(minimum) from exc
    try:
        parsed_cur = Version.parse(current_version)
    except ValueError:
        return MinimumVersionDecision.below_minimum(
            current=current_version,
            minimum=str(parsed_min),
        )
    if _cmp_key(parsed_cur) >= _cmp_key(parsed_min):
        return MinimumVersionDecision.allow()
    return MinimumVersionDecision.below_minimum(
        current=str(parsed_cur),
        minimum=str(parsed_min),
    )


def pick_target_version(latest: str | None, minimum: str) -> str:
    """``max(latest, minimum)``; falls back to ``minimum`` (grok ``pick_target_version``).

    Used by the (YAGNI) auto-updater to keep an install at or above the floor.
    ``latest`` that fails to parse decays to ``None`` (fallback to minimum); an
    unparseable ``minimum`` is returned verbatim (grok parses it but only the
    ``latest >= min`` branch reads ``min``, else it returns ``minimum`` raw).
    """
    latest_v: Version | None = None
    if latest is not None:
        try:
            latest_v = Version.parse(latest)
        except ValueError:
            latest_v = None
    if latest_v is not None:
        try:
            min_v = Version.parse(minimum)
        except ValueError:
            return minimum
        if _cmp_key(latest_v) >= _cmp_key(min_v):
            return str(latest_v)
    return minimum


def check_install_target_inner(target: str, floor: str | None) -> None:
    """Reject an install target below the floor (grok ``check_install_target_inner``).

    No-op when ``floor`` is ``None``; raises :class:`TargetBelowFloor` when
    ``target < floor``; raises :class:`InvalidMinimumVersion` when the floor is
    unparseable (via :func:`evaluate_minimum_version`).
    """
    if floor is None:
        return
    decision = evaluate_minimum_version(target, floor)
    if decision.kind is MinimumDecisionKind.BELOW_MINIMUM:
        assert decision.minimum is not None  # BELOW_MINIMUM always carries minimum
        raise TargetBelowFloor(target=target, minimum=decision.minimum)


def apply_floor_inner(target: str, floor: str | None) -> str:
    """Bump ``target`` up to ``floor`` when below it (grok ``apply_floor_inner``).

    Returns ``target`` unchanged when ``floor`` is ``None`` or ``target >= floor``;
    returns the floor when ``target < floor``. Raises :class:`InvalidMinimumVersion`
    when the floor is unparseable.
    """
    if floor is None:
        return target
    decision = evaluate_minimum_version(target, floor)
    if decision.kind is MinimumDecisionKind.BELOW_MINIMUM:
        assert decision.minimum is not None  # BELOW_MINIMUM always carries minimum
        return decision.minimum
    return target


__all__ = [
    "CachedVersion",
    "InvalidMinimumVersion",
    "MinimumDecisionKind",
    "MinimumVersionDecision",
    "TargetBelowFloor",
    "apply_floor_inner",
    "check_install_target_inner",
    "derive_channel",
    "evaluate_minimum_version",
    "pick_target_version",
    "semver_max",
    "version_from_versioned_binary_name",
]
