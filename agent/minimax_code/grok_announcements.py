"""Remote announcement types + persistence + filtering -- fusion of grok's
``xai-grok-announcements`` (R194, lib.rs, 443 lines, single-file crate).

The crate ships banner-notification logic shared by ``xai-grok-shell`` and
``xai-grok-pager``: tolerant wire types for remote announcements, hidden-state
persistence, expiry filtering, and a startup override hook. This module
migrates all five slices; one slice is recorded as YAGNI.

Slices
------

1. **Wire types** (pure) -- ``RemoteAnnouncement`` + ``AnnouncementCta`` +
   ``AnnouncementsRefreshed``. Migrated as frozen+slots dataclasses with a
   tolerant ``from_mapping`` (mirrors serde ``#[serde(default)]``: missing
   fields default to ``None``, unknown fields ignored, per-field type-checked
   so a wrong-typed value decays to ``None`` rather than poisoning the whole
   parse). ``AnnouncementsRefreshed.gen`` keeps the ``"gen"`` JSON key
   (``#[serde(rename = "gen")]``); the Rust ``r#gen`` escape has no Python
   analogue.
2. **Hidden-state logic** (pure) -- ``announcement_hide_key`` (preserves the
   ``\\x1f`` unit-separator fallback), ``parse_hidden_announcement_ids`` /
   ``serialize_hidden_announcement_ids`` (``set``-based; serialize is sorted
   for a stable on-disk file, mirroring ``BTreeSet``'s load-bearing order) /
   ``prune_hidden_announcement_ids``. Migrated.
3. **Hidden-state I/O** (async) -- ``read_hidden_announcement_ids`` /
   ``write_hidden_announcement_ids``. grok uses ``tokio::fs`` against
   ``~/.grok/announcements.json``; the platform adapts to
   :func:`platformdirs.user_data_dir` (``MINIMAX_CODE_DATA_DIR`` override,
   aligned with ``storage.db.default_data_dir``) and runs the (tiny) file I/O
   via :func:`asyncio.to_thread` -- no ``aiofiles`` dependency. The path is
   parameterised for test injection (grok's signatures take no path; the
   default path is computed lazily so tests can point elsewhere).
4. **Expiry filtering** (pure, injected clock) -- ``visible_announcements`` /
   ``filter_expired`` / ``filter_expired_at`` / ``is_expired_at``. Migrated;
   ``chrono::{DateTime,Utc}`` -> :class:`datetime.datetime` (timezone-aware);
   ``DateTime::parse_from_rfc3339`` -> :meth:`datetime.datetime.fromisoformat`
   (Python 3.11+ accepts the ``Z`` suffix).
5. **Startup resolution** (pure) -- ``resolve_startup``. Migrated with the
   product-identity env rename (see below).

``ts_rs::TS`` feature YAGNI
---------------------------

The ``#[cfg_attr(feature = "ts", derive(ts_rs::TS))]`` + ``#[cfg(test,
feature="ts")] mod bindings_export`` block generates TypeScript bindings via
``ts_rs``'s export-test pattern (``export_all_bindings`` driven by
``generate.sh``). The platform has no ``ts_rs`` codegen pipeline -- web types
are hand-written in ``web/src/types`` -- so the TS derive, the ``ts(...)``
attributes, and the ``export_all_bindings`` test are YAGNI (mirrors the R132
OTel-SDK / R190 ``pb`` / R193 patterns).

Product fusion renames (grok -> MiniMax Code identity)
------------------------------------------------------

* env ``GROK_ANNOUNCEMENTS_OVERRIDE`` -> ``MINIMAX_CODE_ANNOUNCEMENTS_OVERRIDE``
  (mirrors R42 ``GROK_TEST_VERSION`` -> ``MINIMAX_CODE_TEST_VERSION`` and
  R193 ``GROK_CLIENT_NAME`` -> ``MINIMAX_CODE_CLIENT_NAME``).
* on-disk path ``~/.grok/announcements.json`` (``grok_home()``) -> the
  platform user-data dir (``platformdirs.user_data_dir("MiniMaxCode")`` /
  ``MINIMAX_CODE_DATA_DIR`` override). This is a path-resolution adaptation,
  not an env rename: ``grok_home`` is not an env var.

Cross-crate source-of-truth note (vs ``config_types``)
------------------------------------------------------

``RemoteAnnouncement`` is already present as an *inlined tolerant copy* in
:mod:`minimax_code.config_types.types` (R66), where it was inlined to keep
that type layer dependency-free with the note "originates in the
``xai_grok_announcements`` crate; inlined here." This module is the crate's
faithful source (structured ``AnnouncementCta`` cta, precise 9-field shape);
the R66 inline is a simplified tolerant view (``extra="allow"``, ``cta: Any``)
for the ``RemoteSettings`` payload. The dependency direction is NOT flipped
in this round (flipping would perturb R66's own test surface); a future round
may unify by importing from here. Recorded so the unify round honours it --
the symmetric counterpart to R193's ``OriginClientInfo`` source-of-truth note.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import platformdirs

# Env-var + identity renames (grok -> MiniMax Code; see module docstring).
_ANNOUNCEMENTS_OVERRIDE_ENV = "MINIMAX_CODE_ANNOUNCEMENTS_OVERRIDE"
_DATA_DIR_ENV = "MINIMAX_CODE_DATA_DIR"
_APP_NAME = "MiniMaxCode"
_ANNOUNCEMENTS_FILENAME = "announcements.json"
#: Unit separator (U+001F) joining title/message in the content-derived hide
#: key fallback (grok ``\u{1f}``). Load-bearing: disambiguates title/message
#: splits so distinct splits cannot collide.
_HIDE_KEY_SEPARATOR = "\x1f"

_log = logging.getLogger(__name__)


# --- Wire types (grok RemoteAnnouncement / AnnouncementCta / AnnouncementsRefreshed)


def _opt_str(v: Any) -> str | None:
    """Coerce a wire value to ``str | None`` (non-string -> ``None``).

    Mirrors serde ``Option<String>`` with a per-field type guard: a wrong-typed
    value (number / null / object) decays to ``None`` instead of poisoning the
    parent parse -- the tolerant shape grok's ``#[serde(default)]`` fields
    imply at the *field* level.
    """
    return v if isinstance(v, str) else None


def _opt_bool(v: Any) -> bool | None:
    """Coerce a wire value to ``bool | None`` (non-bool -> ``None``).

    ``bool`` is checked before ``int`` semantics apply: Python's ``True`` /
    ``False`` are ``int`` subclasses, but a wire ``1`` is a number, not a flag,
    so it decays to ``None`` (grok ``Option<bool>`` would reject it).
    """
    return v if isinstance(v, bool) else None


@dataclass(frozen=True, slots=True)
class AnnouncementCta:
    """Optional call-to-action on an announcement (grok ``AnnouncementCta``).

    Tolerant: every field is ``Option``; :meth:`from_mapping` ignores unknown
    keys and decays wrong-typed values to ``None`` (so a partial cta parses
    instead of poisoning the parent). The server only emits it with both
    ``label`` / ``url`` non-empty and ``url`` https, but parsing stays lenient
    like the parent struct. ``caption`` is optional dim helper text after the
    button.
    """

    label: str | None = None
    url: str | None = None
    caption: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> AnnouncementCta | None:
        """Parse a cta from a wire mapping; ``None`` if ``data`` is absent/non-mapping."""
        if not isinstance(data, Mapping):
            return None
        return cls(
            label=_opt_str(data.get("label")),
            url=_opt_str(data.get("url")),
            caption=_opt_str(data.get("caption")),
        )


@dataclass(frozen=True, slots=True)
class RemoteAnnouncement:
    """One announcement from remote settings or a local override.

    Mirrors grok ``RemoteAnnouncement``: nine optional fields, all
    ``#[serde(default)]``. :meth:`from_mapping` ignores unknown keys and decays
    wrong-typed field values to ``None`` (tolerant). ``cta`` parses into a
    structured :class:`AnnouncementCta` when the wire value is a mapping,
    else ``None``.
    """

    id: str | None = None
    message: str | None = None
    severity: str | None = None
    title: str | None = None
    cta: AnnouncementCta | None = None
    updated_at: str | None = None
    expires_at: str | None = None
    dismissible: bool | None = None
    persistent: bool | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> RemoteAnnouncement:
        """Parse an announcement from a wire mapping (tolerant).

        ``None`` / non-mapping -> empty announcement (all fields ``None``).
        Unknown keys are ignored; wrong-typed field values decay to ``None``.
        """
        if not isinstance(data, Mapping):
            return cls()
        return cls(
            id=_opt_str(data.get("id")),
            message=_opt_str(data.get("message")),
            severity=_opt_str(data.get("severity")),
            title=_opt_str(data.get("title")),
            cta=AnnouncementCta.from_mapping(data.get("cta")),
            updated_at=_opt_str(data.get("updated_at")),
            expires_at=_opt_str(data.get("expires_at")),
            dismissible=_opt_bool(data.get("dismissible")),
            persistent=_opt_bool(data.get("persistent")),
        )


@dataclass(frozen=True, slots=True)
class AnnouncementsRefreshed:
    """Payload for the ``x.ai/announcements/update`` ACP notification.

    ``gen`` carries the wire field ``"gen"`` (``#[serde(rename = "gen")]``); the
    Rust field is named ``r#gen`` to escape the ``gen`` reserved word, which
    has no Python analogue (the field is just ``gen``). ``announcements``
    defaults to empty (``#[serde(default)]``); items are parsed tolerantly via
    :meth:`RemoteAnnouncement.from_mapping`.
    """

    gen: int
    announcements: list[RemoteAnnouncement] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> AnnouncementsRefreshed | None:
        """Parse from a wire mapping; ``None`` if absent/non-mapping/non-int ``gen``.

        A JSON ``true`` / ``false`` is rejected for ``gen`` (``bool`` is an
        ``int`` subclass in Python but not a valid wire generation number),
        matching grok's ``u64`` deserialization.
        """
        if not isinstance(data, Mapping):
            return None
        raw_gen = data.get("gen")
        if not isinstance(raw_gen, int) or isinstance(raw_gen, bool):
            return None
        raw_list = data.get("announcements")
        if isinstance(raw_list, list):
            items = [RemoteAnnouncement.from_mapping(it) for it in raw_list]
        else:
            items = []
        return cls(gen=raw_gen, announcements=items)


# --- Hidden-state logic (pure)


def announcement_hide_key(a: RemoteAnnouncement) -> str:
    """Stable per-announcement hide key (grok ``announcement_hide_key``).

    The trimmed non-empty ``id`` when present; otherwise a content-derived
    fallback joining title/message with the unit separator (``\\x1f``) so
    distinct title/message splits cannot collide and real ids cannot
    plausibly match.
    """
    if a.id is not None:
        trimmed = a.id.strip()
        if trimmed:
            return trimmed
    title = a.title if a.title is not None else ""
    message = a.message if a.message is not None else ""
    return f"content:{title}{_HIDE_KEY_SEPARATOR}{message}"


def parse_hidden_announcement_ids(s: str) -> set[str]:
    """Parse persisted hidden state into a set of ids (grok ``parse_hidden_announcement_ids``).

    Tolerant: unknown fields ignored; malformed input / non-list ``hidden_ids``
    / non-string items yield an empty set (or the filtered subset). The legacy
    ``{"hidden": bool}`` shape carries no ids, so it decays to empty -- the
    banner re-shows once and the next hide re-persists per-ID.
    """
    try:
        data = json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return set()
    if not isinstance(data, dict):
        return set()
    raw = data.get("hidden_ids")
    if not isinstance(raw, list):
        return set()
    return {item for item in raw if isinstance(item, str)}


def serialize_hidden_announcement_ids(ids: set[str]) -> str | None:
    """Serialize hidden ids to the ``hidden_ids`` JSON shape (grok ``serialize_hidden_announcement_ids``).

    Returns ``None`` only if JSON encoding fails (it cannot for ``set[str]``).
    The id list is **sorted** so the on-disk file is stable across writes
    (mirrors ``BTreeSet``'s load-bearing deterministic order). Compact output
    (no whitespace) matches serde_json's default.
    """
    try:
        return json.dumps({"hidden_ids": sorted(ids)}, separators=(",", ":"))
    except (TypeError, ValueError):
        return None


def prune_hidden_announcement_ids(
    ids: set[str], active: Sequence[RemoteAnnouncement]
) -> bool:
    """Drop hidden ids whose announcement is no longer active (grok ``prune_hidden_announcement_ids``).

    Mutates ``ids`` in place (set intersection update); returns whether the set
    shrank, so callers can persist only on change. Meant for real update paths
    only -- a per-frame prune would churn on transient list states.
    """
    live = {announcement_hide_key(a) for a in active}
    before = len(ids)
    ids.intersection_update(live)
    return len(ids) != before


# --- Hidden-state path + async I/O (grok tokio::fs -> asyncio.to_thread + platformdirs)


def _default_announcements_state_path() -> Path:
    """Default on-disk path for hidden-announcement state.

    ``~/.grok/announcements.json`` (``grok_home()``) -> the platform user-data
    dir. Aligned with :func:`minimax_code.storage.db.default_data_dir`: same
    ``MINIMAX_CODE_DATA_DIR`` override, same ``platformdirs.user_data_dir``
    call (``appname="MiniMaxCode"``, ``appauthor=False``, ``roaming=True``).
    Kept local (not imported from ``storage``) so this leaf stays
    storage-independent; the duplication is five lines and documented.
    """
    override = os.environ.get(_DATA_DIR_ENV)
    if override:
        base = Path(override).expanduser()
    else:
        base = Path(
            platformdirs.user_data_dir(
                appname=_APP_NAME,
                appauthor=False,
                roaming=True,
            )
        )
    return base / _ANNOUNCEMENTS_FILENAME


async def read_hidden_announcement_ids(path: Path | None = None) -> set[str]:
    """Read hidden ids from disk (grok ``read_hidden_announcement_ids``).

    Returns an empty set (everything visible) on missing or malformed file.
    The (tiny) file I/O runs in a worker thread so the event loop is not
    blocked; ``aiofiles`` is avoided (no extra dependency). ``path`` defaults
    to :func:`_default_announcements_state_path` and is parameterised so tests
    can inject a scratch path (grok's signature takes no path).
    """
    p = path if path is not None else _default_announcements_state_path()

    def _read() -> set[str]:
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            return set()
        return parse_hidden_announcement_ids(text)

    return await asyncio.to_thread(_read)


async def write_hidden_announcement_ids(ids: set[str], path: Path | None = None) -> None:
    """Write hidden ids to disk (grok ``write_hidden_announcement_ids``).

    No-op if serialization returns ``None``. The parent directory is created
    if missing (grok assumes ``~/.grok`` exists; the platform's data dir may
    not yet). I/O runs in a worker thread; failures are swallowed (best-effort
    persist, matching grok's ``let _ =``).
    """
    p = path if path is not None else _default_announcements_state_path()
    serialized = serialize_hidden_announcement_ids(ids)
    if serialized is None:
        return

    def _write() -> None:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(serialized, encoding="utf-8")
        except OSError:
            pass

    await asyncio.to_thread(_write)


# --- Filtering (pure, injected clock)


def visible_announcements(
    announcements: Sequence[RemoteAnnouncement],
) -> list[RemoteAnnouncement]:
    """Return only announcements with non-empty (trimmed) messages.

    (grok ``visible_announcements``.) grok returns ``Vec<&RemoteAnnouncement>``
    (borrowed); Python returns the filtered values (no borrow semantics). Draw-
    time consumers iterate this to decide which banners to render.
    """
    return [a for a in announcements if a.message is not None and a.message.strip() != ""]


def filter_expired(
    announcements: Iterable[RemoteAnnouncement],
) -> list[RemoteAnnouncement]:
    """Filter out announcements whose ``expires_at`` is in the past.

    (grok ``filter_expired``.) Thin wrapper over :func:`filter_expired_at` with
    the wall clock; prefer :func:`filter_expired_at` in tests for determinism.
    """
    return filter_expired_at(announcements, datetime.now(UTC))


def filter_expired_at(
    announcements: Iterable[RemoteAnnouncement], now: datetime
) -> list[RemoteAnnouncement]:
    """:func:`filter_expired` with an injectable clock (grok ``filter_expired_at``).

    ``now`` should be timezone-aware (the expiry strings are RFC3339); an aware
    ``now`` is comparable with the parsed (UTC-normalised) expiry. Used so
    expiry-crossing behavior (an item live at the last check that has since
    passed ``expires_at``) is unit-testable.
    """
    return [a for a in announcements if not is_expired_at(a, now)]


def is_expired_at(a: RemoteAnnouncement, now: datetime) -> bool:
    """Whether ``expires_at`` parses and is at/behind ``now``.

    (grok ``is_expired_at``.) Strict ``dt <= now``: an item is live only BEFORE
    its expiry (at the exact expiry instant it is already expired). Missing or
    unparseable ``expires_at`` never expires. ``DateTime::parse_from_rfc3339``
    -> :meth:`datetime.datetime.fromisoformat` (Python 3.11+ accepts ``Z``).
    Allocation-free per call so draw-time consumers can check every frame.
    """
    if a.expires_at is None:
        return False
    dt = _parse_rfc3339(a.expires_at)
    if dt is None:
        return False
    return dt <= now


def _parse_rfc3339(text: str) -> datetime | None:
    """Parse an RFC3339 timestamp, returning ``None`` on failure.

    Wraps :meth:`datetime.datetime.fromisoformat` (Python 3.11+ accepts the
    ``Z`` suffix). Returns ``None`` on any parse error so callers can treat
    unparseable timestamps as "never expires" (mirrors grok's ``let Ok``
    guard). A naive result (no tzinfo) is normalised to UTC so it is
    comparable with an aware ``now``.
    """
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


# --- Startup resolution (pure, env override)


def resolve_startup(
    remote_announcements: list[RemoteAnnouncement] | None,
) -> list[RemoteAnnouncement] | None:
    """Resolve startup announcements (grok ``resolve_startup``).

    Precedence: ``MINIMAX_CODE_ANNOUNCEMENTS_OVERRIDE`` env var (JSON list) ->
    remote announcements. Invalid JSON or a non-list value is logged and
    ignored (falls back to remote). Each override item is parsed tolerantly
    via :meth:`RemoteAnnouncement.from_mapping`.
    """
    raw = os.environ.get(_ANNOUNCEMENTS_OVERRIDE_ENV)
    if raw is not None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            _log.warning(
                "invalid %s JSON; ignoring override", _ANNOUNCEMENTS_OVERRIDE_ENV
            )
            return remote_announcements
        if isinstance(data, list):
            return [RemoteAnnouncement.from_mapping(item) for item in data]
        _log.warning(
            "invalid %s payload (not a JSON array); ignoring override",
            _ANNOUNCEMENTS_OVERRIDE_ENV,
        )
    return remote_announcements


__all__ = [
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
]
