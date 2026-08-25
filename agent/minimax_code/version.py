"""Installed MiniMax Code agent version — fusion of grok's ``xai-grok-version`` (R42).

Lockstepped version vocabulary: the compiled version constant, a test-override
hook, a zero-dependency semver.org parser, and user-facing display formatting
with a channel label. Mirrors grok's ``xai-grok-version`` (lib.rs, 75 lines).

Mapping
-------

* ``env!("CARGO_PKG_VERSION")`` (compile-time, from ``Cargo.toml``) →
  :func:`importlib.metadata.version` (runtime, from ``pyproject.toml``), with a
  hardcoded fallback when the package isn't installed (e.g. running from a bare
  checkout). ``option_env!("GROK_VERSION")`` has no Python compile-time
  equivalent, so the package metadata *is* the single source of truth — editing
  ``pyproject.toml``'s ``version`` is the only place to bump it.
* ``GROK_TEST_VERSION`` call-time override → :data:`TEST_VERSION_ENV`
  (``MINIMAX_CODE_TEST_VERSION``), honored by :func:`installed` so tests can
  inject a version without touching package metadata (e.g. simulate an
  update-check scenario).
* ``semver::Version`` → :class:`Version`, a zero-dependency frozen dataclass
  parsing semver.org (``MAJOR.MINOR.PATCH[-pre][+build]``). ``packaging`` is not
  a MiniMax Code dependency, so a minimal parser is shipped here rather than
  introducing one.

Product fusion
--------------

MiniMax Code's ``/health`` already reports a version and the frontend renders
it; this module is the canonical vocabulary for everything version-adjacent —
self-update checks (compare installed vs latest release), changelog display,
and channel-aware UI strings (``"0.8.0 [stable]"`` vs ``"0.8.0 [alpha]"``). The
update-check + channel-label *wiring* (the ``xai-grok-update`` consumer) is a
future round; this module is the leaf it builds on.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

#: Env var that overrides the reported version at *call* time (testing hook).
#:
#: Mirrors grok's ``GROK_TEST_VERSION`` — set this to inject a version without
#: patching the package metadata (e.g. simulate an upgrade-check scenario).
TEST_VERSION_ENV: str = "MINIMAX_CODE_TEST_VERSION"

#: Hardcoded fallback when the package isn't installed via metadata
#: (e.g. running from a bare checkout without ``uv sync``). Kept in manual sync
#: with ``pyproject.toml``'s ``version`` — the metadata read is the source of
#: truth, this only ever engages in unpackaged contexts.
_FALLBACK_VERSION = "1.4.1"


def _resolve_compiled_version() -> str:
    try:
        return _pkg_version("minimax-code-agent")
    except PackageNotFoundError:
        return _FALLBACK_VERSION


#: The compiled agent version (grok ``VERSION``).
#:
#: Resolved once at import from the installed package metadata (the single
#: source of truth — ``pyproject.toml``'s ``version``), else the hardcoded
#: fallback in unpackaged contexts.
VERSION: str = _resolve_compiled_version()


def installed() -> str:
    """The installed version string, honoring the test-override env (grok ``installed``).

    ``TEST_VERSION_ENV`` override first (trimmed), then :data:`VERSION`. Trimmed
    so non-semver-aware callers can pass the result straight into parsing.
    """
    if raw := os.environ.get(TEST_VERSION_ENV):
        return raw.strip()
    return VERSION


@dataclass(frozen=True, slots=True)
class Version:
    """A parsed semver.org version (zero-dependency mirror of ``semver::Version``).

    Carries ``major.minor.patch`` plus optional ``pre``-release and ``build``
    metadata strings (kept raw — pre-release identifier *ordering* per semver.org
    is not implemented since no caller needs ordered comparison yet; YAGNI until
    an update-check requires deciding ``0.8.0-alpha < 0.8.0``). Two ``Version``
    values are equal iff all five fields match (dataclass value equality).
    """

    major: int
    minor: int
    patch: int
    pre: str | None = None
    build: str | None = None

    @classmethod
    def parse(cls, text: str) -> Version:
        """Parse a semver.org string. Raise ``ValueError`` if not valid semver."""
        m = _SEMVER_RE.match(text.strip())
        if m is None:
            raise ValueError(f"not a valid semver: {text!r}")
        return cls(
            major=int(m["major"]),
            minor=int(m["minor"]),
            patch=int(m["patch"]),
            pre=m["pre"],
            build=m["build"],
        )

    def __str__(self) -> str:
        s = f"{self.major}.{self.minor}.{self.patch}"
        if self.pre is not None:
            s += f"-{self.pre}"
        if self.build is not None:
            s += f"+{self.build}"
        return s


# semver.org regex: MAJOR.MINOR.PATCH[-prerelease][+build]
_SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)


def installed_semver() -> Version:
    """Parse :func:`installed` as a :class:`Version` (grok ``installed_semver``).

    Package metadata reports versions in PEP 440 normalized form, where a
    prerelease loses its hyphen (``1.0.0-rc.1`` in pyproject becomes
    ``1.0.0rc1`` in metadata). :class:`Version` speaks semver.org, so bridge
    the PEP 440 prerelease spellings back to their semver form before parsing.
    Anything else that is not valid semver still raises ``ValueError``
    (mirrors grok's ``Result<Version, semver::Error>``).
    """
    text = installed()
    m = _PEP440_PRE_RE.match(text)
    if m:
        tag = m["dev"] or m["pre"]
        text = f"{m['core']}-{tag}.{m['num']}"
    return Version.parse(text)


# PEP 440 normalized prerelease forms that can appear in package metadata:
# "1.0.0rc1" / "1.0.0a1" / "1.0.0b2" / "1.0.0c3" (tag glued to the number)
# and "1.0.0.dev3" (dot-prefixed). Normalization folds alpha/beta/pre/preview
# spellings into a/b/rc before they reach metadata, and drops the hyphen
# semver would use. A plain "1.0.0" must NOT match — it parses as-is.
_PEP440_PRE_RE = re.compile(
    r"^(?P<core>\d+\.\d+\.\d+)(?:\.(?P<dev>dev)|(?P<pre>a|b|c|rc))(?P<num>\d+)$"
)


def display_version(channel_label: str) -> str:
    """Format the compiled version with a channel label (grok ``display_version``).

    ``channel_label`` is a pre-formatted suffix such as ``" [alpha]"``,
    ``" [stable]"``, or ``""`` (empty when no cached pointer is available).
    Example: ``"0.8.0 [stable]"``.
    """
    return f"{VERSION}{channel_label}"


def display_version_with_commit(version_with_commit: str, channel_label: str) -> str:
    """Format a version-with-commit string with a channel label.

    Same semantics as :func:`display_version` but for the full
    ``"0.8.0 (abc1234)"`` string. Example: ``"0.8.0 (abc1234) [alpha]"``.
    """
    return f"{version_with_commit}{channel_label}"


__all__ = [
    "TEST_VERSION_ENV",
    "VERSION",
    "Version",
    "installed",
    "installed_semver",
    "display_version",
    "display_version_with_commit",
]
