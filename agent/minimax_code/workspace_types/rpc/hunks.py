"""Hunk tracker RPCs (R76).

Fusion of grok's ``xai-grok-workspace-types::rpc::hunks`` — the 10
``workspace.hunk_*`` / ``workspace.get_all_hunks`` / ``workspace.get_session_summary``
methods plus the wire mirrors of ``xai_hunk_tracker`` response types. This file
deliberately does **not** import the R39 ``xai-hunk-tracker`` primitives: grok
mirrors these types in the lean crate to avoid pulling in ``gix`` (source
L111-114), and we preserve that separation — R39 owns the diff *computation*,
R76 owns the diff *wire shape*.

Two of the ten methods drop the ``hunk_`` prefix on the wire
(``workspace.get_all_hunks`` / ``workspace.get_session_summary``), reproduced
verbatim via the ``METHOD`` ClassVar.

This file lands five serde patterns new to the RPC layer:

* **internally tagged enum with ``#[serde(other)]`` fallback** —
  :class:`HunkSourceWire` uses ``#[serde(tag = "type", rename_all = "camelCase")]``
  with a ``#[serde(other)] Unknown`` catch-all variant. ``rename_all`` renames
  the variant *names* (``AgentEdit`` → ``agentEdit``) but **not** struct-variant
  *fields* (``prompt_index`` stays snake_case). pydantic discriminated unions
  have no native ``#[serde(other)]`` arm, so this is modelled as a flat
  :class:`WireModel` with ``type: str`` + ``prompt_index: int | None``: any
  unrecognised tag decodes successfully (forward-tolerant) and round-trips
  verbatim. This is the layer's second tagged enum (R75 was ``tag = "status"``
  with no ``other`` arm) and the first with a catch-all + mixed variant/field
  renaming.
* **hand-written ``Deserialize`` forward-tolerant string enum** —
  :class:`FileContentStatusWire` is a plain ``camelCase`` string enum whose
  ``Deserialize`` is hand-written in grok (source L225-239): an unrecognised
  status string decodes to ``Unknown`` instead of failing the whole structured
  response. ``#[serde(other)]`` is not allowed on plain string enums (only on
  internally/adjacently tagged enums), hence the manual impl. In pydantic this
  is a :class:`StrEnum` with a custom ``__get_pydantic_core_schema__`` routing
  unknown strings to ``UNKNOWN``. Contrast with R71's :class:`HookEventNameWire`
  (str-subclass that preserves the unknown verbatim) — this is a closed enum
  with an explicit ``Unknown`` member and a ``Missing`` default.
* **``DateTime<Utc>`` RFC 3339 ``Z`` suffix** — :attr:`HunkWire.created_at` is
  ``chrono::DateTime<Utc>``, which serde emits with a ``Z`` suffix
  (``2026-06-23T00:00:00Z``). pydantic's default ``datetime`` serialisation uses
  ``+00:00``; the :data:`IsoUtc` alias with a ``PlainSerializer`` restores the
  ``Z`` form for wire fidelity.
* **``PathBuf`` → ``str`` natural mapping** — :attr:`HunkWire.path`,
  :attr:`FileContentEntryWire.path`, :attr:`TurnSummaryWire.files` are
  ``PathBuf`` / ``Vec<PathBuf>`` in grok, which serde emits as bare strings /
  lists of strings. pydantic models these as ``str`` / ``list[str]`` (the
  layer's first ``PathBuf`` wire fields).
* **camelCase nested struct lexical-sort round trip** — :class:`HunkWire` and
  its nested :class:`HunkLineInfoWire` / :class:`HunkSourceWire` are camelCase;
  :meth:`WireModel.to_wire` lexically sorts keys at every depth (BTreeMap
  contract), so round-trip assertions use key-indexed access rather than the
  whole-map equality that grok's serde (field-order) tests rely on.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, ClassVar

from pydantic import ConfigDict, PlainSerializer, model_serializer
from pydantic.alias_generators import to_camel
from pydantic_core import core_schema

from minimax_code.workspace_types._wire import WireModel

__all__ = [
    # enums
    "HunkActionKind",
    "HunkSourceWire",
    "FileContentStatusWire",
    # camelCase wire mirrors of xai_hunk_tracker types
    "HunkLineInfoWire",
    "HunkWire",
    "FileContentViewWire",
    "FileContentEntryWire",
    "SessionStatsWire",
    "TurnSummaryWire",
    "SessionSummaryWire",
    # response / summary types (snake_case)
    "HunkActionResponse",
    "BulkHunkActionResponse",
    "FileSummary",
    "FilteredHunksResponse",
    # request payloads (snake_case)
    "HunkActionReq",
    "HunkSingleActionReq",
    "HunkFileActionReq",
    "HunkTurnActionReq",
    "HunkAllActionReq",
    "HunkGetFilteredHunksReq",
    # empty default requests
    "HunkGetStagedFilesReq",
    "HunkGetFileSummariesReq",
    "HunkGetAllHunksReq",
    "HunkGetAllFileContentsReq",
    "HunkGetSessionSummaryReq",
    # alias
    "IsoUtc",
]

_CAMEL = ConfigDict(populate_by_name=True, alias_generator=to_camel)


# === DateTime<Utc> RFC 3339 Z-suffix serialiser ===


def _dt_to_wire(d: datetime) -> str:
    """Serialise a datetime as RFC 3339 with a ``Z`` suffix (chrono-compatible)."""
    d = d.astimezone(UTC)
    if d.microsecond:
        base = d.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0").rstrip(".")
    else:
        base = d.strftime("%Y-%m-%dT%H:%M:%S")
    return base + "Z"


#: ``chrono::DateTime<Utc>`` wire alias. chrono emits a ``Z`` suffix; pydantic's
#: default ``+00:00`` is rewritten to ``Z`` for wire fidelity.
IsoUtc = Annotated[datetime, PlainSerializer(_dt_to_wire, return_type=str)]


# === Enums ===


class HunkActionKind(StrEnum):
    """Hunk action (lowercase wire, **no** default — request payload only).

    Mirrors ``#[serde(rename_all = "lowercase")] enum HunkActionKind { Accept,
    Reject }``. Unlike R75's :class:`WorktreeType` / :class:`ApplyMode` there is
    no ``#[default]``: the client must always specify the action explicitly.
    """

    ACCEPT = "accept"
    REJECT = "reject"


class HunkSourceWire(WireModel):
    """Wire mirror of ``xai_hunk_tracker::types::HunkSource``.

    Internally tagged (``#[serde(tag = "type", rename_all = "camelCase")]``) with
    an ``#[serde(other)] Unknown`` fallback. ``rename_all`` renames variant
    *names* (``AgentEdit`` → ``agentEdit``, ``ExternalEditOnAgentFile`` →
    ``externalEditOnAgentFile``, ``External`` → ``external``) but **not**
    struct-variant *fields* (``prompt_index`` stays snake_case — only the
    ``AgentEdit`` variant carries it). pydantic discriminated unions have no
    native ``#[serde(other)]`` catch-all, so this is modelled as a flat model
    with ``type: str`` + ``prompt_index: int | None``: any unrecognised tag
    (e.g. ``"futureSource"``) decodes successfully and round-trips verbatim.
    ``prompt_index`` is omitted from the wire when ``None``.
    """

    model_config = ConfigDict(populate_by_name=True)
    type: str
    prompt_index: int | None = None

    @model_serializer(mode="wrap")
    def _omit(self, handler):  # noqa: ANN001, ANN202
        raw = handler(self)
        if raw.get("prompt_index") is None:
            raw.pop("prompt_index", None)
        return raw


class FileContentStatusWire(StrEnum):
    """Wire mirror of ``xai_hunk_tracker::types::FileContentStatus`` (camelCase).

    grok hand-writes ``Deserialize`` (source L225-239) so an unrecognised status
    string from a newer server decodes to :attr:`UNKNOWN` rather than failing the
    whole structured response. ``#[serde(other)]`` is not allowed on plain string
    enums (only internally/adjacently tagged), hence the manual impl. The custom
    ``__get_pydantic_core_schema__`` here routes unknown strings to ``UNKNOWN``;
    serialisation emits the member value (``"missing"`` / ``"tooLarge"`` / …).
    ``#[default] Missing`` → :meth:`default` returns :attr:`MISSING`.
    """

    MISSING = "missing"
    BINARY = "binary"
    TOO_LARGE = "tooLarge"
    LFS_POINTER = "lfsPointer"
    SYMLINK = "symlink"
    FULL = "full"
    UNKNOWN = "unknown"

    @classmethod
    def default(cls) -> FileContentStatusWire:
        return cls.MISSING

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):  # noqa: ANN001, ANN206
        def _coerce(value: str) -> FileContentStatusWire:
            # str_schema guarantees a str input (StrEnum members pass — they are
            # str subclasses); map the string to the enum, unknowns → UNKNOWN.
            try:
                return cls(value)
            except ValueError:
                return cls.UNKNOWN

        # after-validator over a str_schema: the base schema supplies the JSON
        # "string" type + validates the input is a string, then _coerce maps it
        # to the enum member. ``no_info_plain_validator_function`` has no
        # ``json_schema`` parameter in this pydantic release, so the
        # after-validator form is the version-stable way to keep both the
        # forward-tolerant decode and a real JSON schema.
        return core_schema.no_info_after_validator_function(
            _coerce,
            core_schema.str_schema(),
            serialization=core_schema.plain_serializer_function_ser_schema(lambda v: v.value),
        )


# === camelCase wire mirrors of xai_hunk_tracker types ===


class HunkLineInfoWire(WireModel):
    """Wire mirror of ``HunkLineInfo`` (camelCase)."""

    model_config = _CAMEL
    old_start: int
    old_count: int
    new_start: int
    new_count: int


class HunkWire(WireModel):
    """Wire mirror of ``xai_hunk_tracker::types::Hunk`` (camelCase).

    The ``selected`` runtime field is ``#[serde(skip)]`` upstream and so absent.
    ``old_text`` / ``patch`` are ``Option<String>`` with **no**
    ``skip_serializing_if`` → ``null`` is kept. ``path`` (``PathBuf``) maps to
    ``str``; ``created_at`` (``DateTime<Utc>``) maps to :data:`IsoUtc`.
    """

    model_config = _CAMEL
    id: str
    path: str
    line_info: HunkLineInfoWire
    source: HunkSourceWire
    old_text: str | None = None
    new_text: str
    patch: str | None = None
    created_at: IsoUtc


class FileContentViewWire(WireModel):
    """Wire mirror of ``FileContentView`` (camelCase).

    ``status`` is always emitted; ``byte_len`` / ``content`` carry
    ``skip_serializing_if = "Option::is_none"`` → omitted when ``None``.
    """

    model_config = _CAMEL
    status: FileContentStatusWire
    byte_len: int | None = None
    content: str | None = None

    @model_serializer(mode="wrap")
    def _omit(self, handler):  # noqa: ANN001, ANN202
        raw = handler(self)
        for key in ("byteLen", "content"):
            if raw.get(key) is None:
                raw.pop(key, None)
        return raw


class FileContentEntryWire(WireModel):
    """Wire mirror of ``FileContentEntry`` (camelCase, Options keep null)."""

    model_config = _CAMEL
    path: str
    baseline: FileContentViewWire
    current: FileContentViewWire
    is_agent_file: bool
    staged: bool


class SessionStatsWire(WireModel):
    """Wire mirror of ``SessionStats`` (camelCase, derives ``Default``)."""

    model_config = _CAMEL
    accepted_hunks: int
    rejected_hunks: int
    accepted_lines_added: int
    accepted_lines_removed: int
    rejected_lines_added: int
    rejected_lines_removed: int


class TurnSummaryWire(WireModel):
    """Wire mirror of ``TurnSummary`` (camelCase). ``files`` is ``Vec<PathBuf>``."""

    model_config = _CAMEL
    prompt_index: int
    files: list[str]
    pending_hunks: list[HunkWire]
    lines_added: int
    lines_removed: int


class SessionSummaryWire(WireModel):
    """Wire mirror of ``SessionSummary`` (camelCase, derives ``Default``)."""

    model_config = _CAMEL
    stats: SessionStatsWire
    turns: list[TurnSummaryWire]
    files_modified: int
    files_with_pending: int
    pending_hunks: int
    pending_lines_added: int
    pending_lines_removed: int
    unattributed_pending: int


# === Response / summary types (snake_case, no rename_all) ===


class HunkActionResponse(WireModel):
    """Response for a single-hunk action (empty struct, derives ``Default``)."""


class BulkHunkActionResponse(WireModel):
    """Response for bulk hunk actions (snake_case, derives ``Default``).

    ``affected`` is ``Vec<String>`` with **no** ``skip_serializing_if`` → the
    empty vec emits ``"affected": []``. Defaulted so :meth:`default` mirrors
    Rust's ``Default`` (``vec![]``); the server always sends the key on the wire.
    """

    affected: list[str] = []


class FileSummary(WireModel):
    """Per-file hunk summary (snake_case, no ``rename_all``)."""

    path: str
    hunk_count: int
    is_agent_file: bool


class FilteredHunksResponse(WireModel):
    """Filtered hunks response (snake_case, **no** ``rename_all``, ``Default``).

    ``hunks`` (``Vec<HunkWire>``) and ``total`` (``usize``) carry **no**
    ``skip_serializing_if`` → empty/zero emit ``"hunks": []`` / ``"total": 0``.
    Defaulted so :meth:`default` mirrors Rust's ``Default``.
    """

    hunks: list[HunkWire] = []
    total: int = 0


# === Request payloads (snake_case, no rename_all) ===


class HunkActionReq(WireModel):
    """Single-hunk action payload, nested inside :class:`HunkSingleActionReq`."""

    hunk_id: str
    action: HunkActionKind


class HunkSingleActionReq(WireModel):
    """``workspace.hunk_action`` (snake_case). Wire: ``{action: {hunk_id, action}}``."""

    action: HunkActionReq

    METHOD: ClassVar[str] = "workspace.hunk_action"
    Response: ClassVar[type] = HunkActionResponse


class HunkFileActionReq(WireModel):
    """``workspace.hunk_file_action`` (snake_case)."""

    path: str
    action: HunkActionKind

    METHOD: ClassVar[str] = "workspace.hunk_file_action"
    Response: ClassVar[type] = BulkHunkActionResponse


class HunkTurnActionReq(WireModel):
    """``workspace.hunk_turn_action`` (snake_case)."""

    prompt_index: int
    action: HunkActionKind

    METHOD: ClassVar[str] = "workspace.hunk_turn_action"
    Response: ClassVar[type] = BulkHunkActionResponse


class HunkAllActionReq(WireModel):
    """``workspace.hunk_all_action`` (snake_case)."""

    action: HunkActionKind

    METHOD: ClassVar[str] = "workspace.hunk_all_action"
    Response: ClassVar[type] = BulkHunkActionResponse


class HunkGetFilteredHunksReq(WireModel):
    """``workspace.hunk_get_filtered_hunks`` (snake_case, both Options default None).

    ``path`` / ``source`` carry ``#[serde(default)]`` and **no**
    ``skip_serializing_if`` → ``None`` emits ``null``.
    """

    path: str | None = None
    source: str | None = None

    METHOD: ClassVar[str] = "workspace.hunk_get_filtered_hunks"
    Response: ClassVar[type] = FilteredHunksResponse


# === Empty default request structs ===


class HunkGetStagedFilesReq(WireModel):
    """``workspace.hunk_get_staged_files`` (empty struct, ``Default``)."""

    METHOD: ClassVar[str] = "workspace.hunk_get_staged_files"
    Response: ClassVar = list[str]


class HunkGetFileSummariesReq(WireModel):
    """``workspace.hunk_get_file_summaries`` (empty struct, ``Default``)."""

    METHOD: ClassVar[str] = "workspace.hunk_get_file_summaries"
    Response: ClassVar = list[FileSummary]


class HunkGetAllHunksReq(WireModel):
    """``workspace.get_all_hunks`` (empty struct, ``Default``).

    Note the method lacks the ``hunk_`` prefix — it is ``workspace.get_all_hunks``,
    not ``workspace.hunk_get_all_hunks``.
    """

    METHOD: ClassVar[str] = "workspace.get_all_hunks"
    Response: ClassVar = list[HunkWire]


class HunkGetAllFileContentsReq(WireModel):
    """``workspace.hunk_get_all_file_contents`` (empty struct, ``Default``)."""

    METHOD: ClassVar[str] = "workspace.hunk_get_all_file_contents"
    Response: ClassVar = list[FileContentEntryWire]


class HunkGetSessionSummaryReq(WireModel):
    """``workspace.get_session_summary`` (empty struct, ``Default``).

    Note the method lacks the ``hunk_`` prefix — it is
    ``workspace.get_session_summary``.
    """

    METHOD: ClassVar[str] = "workspace.get_session_summary"
    Response: ClassVar[type] = SessionSummaryWire
