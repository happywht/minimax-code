"""Tests for the version vocabulary — fusion of grok's ``xai-grok-version`` (R42).

Mirrors grok's ``test_display_version_formatting_matrix`` (the display label
append invariant across alpha/stable/empty) and pins the Python mapping: the
test-override hook, the zero-dependency semver parser, value equality, and
frozen-ness.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code import version as V

# --- display formatting (mirror grok test_display_version_formatting_matrix) --


def test_display_version_with_commit_matrix():
    """grok matrix: label appends correctly across alpha/stable/empty."""
    cases = [
        ("0.2.5 (abc1234)", " [alpha]", "0.2.5 (abc1234) [alpha]"),
        ("0.2.5 (abc1234)", " [stable]", "0.2.5 (abc1234) [stable]"),
        ("0.2.5 (abc1234)", "", "0.2.5 (abc1234)"),
        ("0.1.220-alpha.2 (def0)", " [alpha]", "0.1.220-alpha.2 (def0) [alpha]"),
    ]
    for vwc, label, expected in cases:
        assert V.display_version_with_commit(vwc, label) == expected


def test_display_version_appends_label():
    """display_version uses the compiled VERSION; just verify label appending."""
    assert V.display_version("") == V.VERSION
    assert V.display_version(" [stable]").endswith("[stable]")


# --- installed() + TEST_VERSION_ENV override -------------------------------


def test_installed_honors_test_env_override(monkeypatch):
    """TEST_VERSION_ENV wins over the compiled VERSION (testing hook)."""
    monkeypatch.setenv(V.TEST_VERSION_ENV, "9.9.9")
    assert V.installed() == "9.9.9"


def test_installed_trims_override(monkeypatch):
    """Override is trimmed so callers can pass it straight into parsing (grok parity)."""
    monkeypatch.setenv(V.TEST_VERSION_ENV, "  0.1.220-alpha.2  ")
    assert V.installed() == "0.1.220-alpha.2"


def test_installed_falls_back_to_version(monkeypatch):
    """Without the override env, installed() returns the compiled VERSION."""
    monkeypatch.delenv(V.TEST_VERSION_ENV, raising=False)
    assert V.installed() == V.VERSION


def test_installed_returns_nonempty():
    """Sanity: the resolved version is a non-empty string in every environment."""
    assert isinstance(V.installed(), str)
    assert V.installed() != ""


# --- Version semver parser -------------------------------------------------


def test_version_parse_simple():
    v = V.Version.parse("0.8.0")
    assert (v.major, v.minor, v.patch) == (0, 8, 0)
    assert v.pre is None and v.build is None


def test_version_parse_prerelease():
    v = V.Version.parse("0.1.220-alpha.2")
    assert (v.major, v.minor, v.patch) == (0, 1, 220)
    assert v.pre == "alpha.2"
    assert v.build is None


def test_version_parse_build_metadata():
    v = V.Version.parse("1.0.0+build.7")
    assert v.build == "build.7"
    assert v.pre is None


def test_version_parse_prerelease_and_build():
    v = V.Version.parse("2.5.1-rc.1+exp.sha.5114f85")
    assert v.pre == "rc.1"
    assert v.build == "exp.sha.5114f85"


def test_version_parse_invalid_raises():
    with pytest.raises(ValueError):
        V.Version.parse("not-a-version")
    with pytest.raises(ValueError):
        V.Version.parse("0.8")  # missing patch
    with pytest.raises(ValueError):
        V.Version.parse("01.2.3")  # leading zero not allowed in semver


def test_version_str_roundtrip():
    """str(Version) reproduces the canonical semver form."""
    assert str(V.Version.parse("0.8.0")) == "0.8.0"
    assert str(V.Version.parse("0.1.220-alpha.2")) == "0.1.220-alpha.2"
    assert str(V.Version.parse("1.0.0+build.7")) == "1.0.0+build.7"


def test_version_equality():
    """Value equality across all five fields (dataclass frozen equality)."""
    assert V.Version.parse("0.8.0") == V.Version(0, 8, 0)
    assert V.Version.parse("0.8.0") != V.Version.parse("0.8.1")
    assert V.Version.parse("1.0.0-alpha") != V.Version.parse("1.0.0-beta")


def test_version_is_frozen():
    """frozen=True mirrors grok's value semantics; assignment raises."""
    v = V.Version.parse("0.8.0")
    with pytest.raises(FrozenInstanceError):
        v.major = 1  # type: ignore[misc]


# --- installed_semver() ----------------------------------------------------


def test_installed_semver_returns_version(monkeypatch):
    """installed_semver parses installed() into a Version (grok installed_semver)."""
    monkeypatch.setenv(V.TEST_VERSION_ENV, "0.1.220-alpha.2")
    v = V.installed_semver()
    assert isinstance(v, V.Version)
    assert (v.major, v.minor, v.patch) == (0, 1, 220)
    assert v.pre == "alpha.2"


def test_installed_semver_invalid_raises(monkeypatch):
    """Mirrors grok's Result<Version, semver::Error> on a non-semver installed string."""
    monkeypatch.setenv(V.TEST_VERSION_ENV, "totally-not-semver")
    with pytest.raises(ValueError):
        V.installed_semver()


# --- PEP 440 metadata bridge (1.0.0-rc.1 era) -------------------------------


def test_installed_semver_bridges_pep440_prerelease(monkeypatch):
    """Package metadata reports PEP 440 normalized forms ("1.0.0rc1" — the
    hyphen semver wants is dropped by normalization). installed_semver must
    bridge them back; without this the public API raises on our own version
    (first bitten by the 1.0.0-rc.1 bump)."""
    for pep440, expected_pre in [
        ("1.0.0rc1", "rc.1"),
        ("1.0.0a1", "a.1"),
        ("1.0.0b2", "b.2"),
        ("1.0.0c3", "c.3"),
        ("1.0.0.dev3", "dev.3"),
    ]:
        monkeypatch.setenv(V.TEST_VERSION_ENV, pep440)
        v = V.installed_semver()
        assert (v.major, v.minor, v.patch, v.pre) == (1, 0, 0, expected_pre), pep440


def test_installed_semver_stable_form_untouched_by_bridge(monkeypatch):
    """Plain stable versions must bypass the bridge entirely — the regex must
    not match them (a greedy-optional bridge would corrupt stable parsing)."""
    monkeypatch.setenv(V.TEST_VERSION_ENV, "1.0.0")
    v = V.installed_semver()
    assert (v.major, v.minor, v.patch, v.pre) == (1, 0, 0, None)


def test_installed_semver_pep440_post_still_raises(monkeypatch):
    """.post has no semver equivalent; bridging it would invent semantics,
    so it stays a ValueError like any other non-semver string."""
    monkeypatch.setenv(V.TEST_VERSION_ENV, "1.0.0.post1")
    with pytest.raises(ValueError):
        V.installed_semver()


def test_installed_semver_live_metadata_is_parseable():
    """Contract: whatever version this checkout ships, installed_semver() must
    never raise on the real package metadata (source of truth for the bump)."""
    v = V.installed_semver()
    assert isinstance(v, V.Version)
    assert (v.major, v.minor, v.patch) == (1, 0, 0)
    assert v.pre == "rc.1"
