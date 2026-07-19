"""File I/O RPCs (R77).

Fusion of grok's ``xai-grok-workspace-types::rpc::fs`` — the file I/O
surface: service-level ``workspace.put_files`` / ``workspace.get_files``,
the five ``workspace.fs_*`` extension ops backing the shell's
``x.ai/fs/*`` ACP methods, and the three ``workspace.client_fs_*``
read-only client ops. Ten methods total, closing the ``rpc/`` namespace
(all 10 files now migrated: R68 session/agents_md → R69 code_nav/deploy
→ R70 search → R71 hooks → R72 workspace → R73 skills → R74 git →
R75 worktree → R76 hunks → R77 fs).

This file lands five serde patterns new to the RPC layer:

* **generic ``skip_serializing_if = "Option::is_none"`` base** — every
  response/data struct in this file whose ``Option`` fields all carry
  ``skip_serializing_if`` (8 structs: :class:`PutFileResult`,
  :class:`GetFileEntry`, :class:`GetFileResult`, :class:`FsListNode`,
  :class:`FsReadFileData`, :class:`ClientFsListNode`, :class:`ClientFsStatRes`,
  :class:`ClientFsReadFileRes`) inherits :class:`_DropNoneWire`, whose wrap
  ``model_serializer`` pops every ``None``-valued wire key. This is the
  layer's first *generic* None-elision base (R74/R75/R76 each hand-wrote
  per-class pop lists); it is sound here because grok marks **every**
  ``Option`` on these structs as skip — there is no "emit ``null``"
  ``Option`` to preserve. Request structs (:attr:`FsListReq.cwd` etc.)
  deliberately do **not** inherit it: their ``Option`` fields carry only
  ``#[serde(default)]`` (no skip), so ``None`` must emit ``null``.
* **``rename = "type"`` on a ``String`` (not an enum)** —
  :attr:`FsListNode.node_type` and :attr:`FsReadFileData.content_type` are
  ``String`` with ``#[serde(rename = "type")]`` on top of ``rename_all =
  "camelCase"``. R74/R75 applied ``rename = "type"`` to enum fields
  (:class:`~minimax_code.workspace_types.rpc.git.ChangeType` /
  :class:`~minimax_code.workspace_types.rpc.worktree.FileConflict`); this
  is the ``str`` analogue — ``Field(alias = "type")`` overrides the
  ``to_camel`` generator's ``"nodeType"`` / ``"contentType"``.
* **``Response = ()`` (unit) on the wire** — :class:`FsWriteFileReq` and
  :class:`FsDeleteFileReq` return the unit type (server acknowledges with
  no payload), reproduced as ``Response: ClassVar = type(None)`` (R68
  :class:`~minimax_code.workspace_types.rpc.session.BeginPromptReq` pattern).
* **Req snake / Res camelCase asymmetry** — the service-level and
  ``fs_*`` *requests* are plain snake_case (no ``rename_all``), but the
  ``fs_*`` *responses* (:class:`FsListNode`, :class:`FsExistsData`,
  :class:`FsReadFileData`) are ``camelCase``. The ``client_fs_*`` family
  is camelCase on both sides (grok keeps the workspace server and the
  grok.com backend on the same structs — source L309-320).
* **``u64`` / ``i64`` / ``usize`` / ``u32`` → ``int``** — grok's
  fixed-width integers all map to Python ``int`` (no width distinction);
  notably :attr:`ClientFsListNode.mtime_ms` is ``Option<i64>`` epoch
  millis (not the RFC 3339 ``modified_at`` string of :class:`FsListNode`).
"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from pydantic import ConfigDict, Field, model_serializer
from pydantic.alias_generators import to_camel

from minimax_code.workspace_types._wire import WireModel

__all__ = [
    # enums (lowercase rename_all)
    "FsNodeType",
    "FsReadEncoding",
    "FsContentType",
    # method constants (client_fs_* only — service/fs_* names live on each Req)
    "CLIENT_FS_LIST_METHOD",
    "CLIENT_FS_STAT_METHOD",
    "CLIENT_FS_READ_FILE_METHOD",
    # service-level put/get
    "PutFileEntry",
    "PutFilesReq",
    "PutFileResult",
    "PutFilesRes",
    "GetFileEntry",
    "GetFilesReq",
    "GetFileResult",
    "GetFilesRes",
    # fs_* extension responses
    "FsListNode",
    "FsListData",
    "FsExistsData",
    "FsReadFileData",
    # fs_* extension requests
    "FsListReq",
    "FsExistsReq",
    "FsReadFileReq",
    "FsWriteFileReq",
    "FsDeleteFileReq",
    # client_fs_* (read-only client ops)
    "ClientFsListReq",
    "ClientFsListNode",
    "ClientFsListRes",
    "ClientFsStatReq",
    "ClientFsStatRes",
    "ClientFsReadFileReq",
    "ClientFsReadFileRes",
]

_CAMEL = ConfigDict(populate_by_name=True, alias_generator=to_camel)


# === serde default functions (mirror grok default_depth/default_limit/...) ===
#
# grok defines ``default_depth`` / ``default_limit`` / ``default_true`` /
# ``default_max_bytes`` (service+fs_*) and ``default_client_depth`` /
# ``default_client_limit`` (client_fs_*). The client_* pair duplicates the
# service values (1 / 1000) but is kept distinct to mirror grok's two
# independent ``default`` call-sites — a client_fs default change must not
# silently shift the shell-facing fs_* defaults (and vice versa).


def _default_true() -> bool:
    """Mirror Rust ``default_true()`` → ``True``."""
    return True


def _default_depth() -> int:
    """Mirror Rust ``default_depth()`` → ``1`` (immediate children)."""
    return 1


def _default_limit() -> int:
    """Mirror Rust ``default_limit()`` → ``1000``."""
    return 1000


def _default_max_bytes() -> int:
    """Mirror Rust ``default_max_bytes()`` → ``1_048_576`` (1 MiB chunk cap)."""
    return 1_048_576


def _default_client_depth() -> int:
    """Mirror Rust ``default_client_depth()`` → ``1`` (client_fs_* walk depth)."""
    return 1


def _default_client_limit() -> int:
    """Mirror Rust ``default_client_limit()`` → ``1000`` (client_fs_* page cap)."""
    return 1000


# === Generic skip_serializing_if = "Option::is_none" base ===


class _DropNoneWire(WireModel):
    """Base for structs whose **every** ``Option`` field carries
    ``skip_serializing_if = "Option::is_none"``.

    The wrap ``model_serializer`` pops every wire key whose value is
    ``None`` after the default (alias-aware) serialisation, reproducing
    serde's ``skip_serializing_if = "Option::is_none"`` without a per-class
    pop list. Sound only when the struct has **no** "emit ``null``"
    ``Option`` — i.e. every ``Option`` is marked skip. Request structs
    with ``#[serde(default)]``-only ``Option`` fields (e.g.
    :attr:`FsListReq.cwd`) must **not** inherit this (their ``None`` must
    emit ``null``).
    """

    @model_serializer(mode="wrap")
    def _drop_none(self, handler):  # noqa: ANN001, ANN202
        raw = handler(self)
        return {key: value for key, value in raw.items() if value is not None}


# === Enums (lowercase rename_all) ===


class FsNodeType(StrEnum):
    """Filesystem node kind (lowercase wire, **no** default).

    Mirrors ``#[serde(rename_all = "lowercase")] enum FsNodeType {
    Directory, File }``. Wire values match the shell's ``x.ai/fs/list``
    node ``type`` strings. Unlike :class:`FsReadEncoding` there is no
    ``#[default]``: the value is always carried by the data, never
    defaulted by a request.
    """

    DIRECTORY = "directory"
    FILE = "file"


class FsReadEncoding(StrEnum):
    """Requested content transfer encoding (lowercase wire, default ``Utf8``).

    Mirrors ``#[derive(Default)] enum FsReadEncoding`` with
    ``#[serde(rename_all = "lowercase")]`` and ``#[default] Utf8``.
    ``Utf8`` puts text in ``content`` (falling back to base64 when a
    requested byte range is not valid UTF-8); ``Base64`` puts bytes in
    ``contentBase64`` (binary-safe; chunked readers use this).
    """

    UTF8 = "utf8"
    BASE64 = "base64"

    @classmethod
    def default(cls) -> FsReadEncoding:
        """Mirror Rust ``#[default] Utf8``."""
        return cls.UTF8


class FsContentType(StrEnum):
    """Whether the returned payload was valid UTF-8 (lowercase wire, no default).

    Mirrors ``#[serde(rename_all = "lowercase")] enum FsContentType {
    Text, Binary }`` — the observed encoding of a read payload (set by the
    server), not a request parameter, so there is no ``#[default]``.
    """

    TEXT = "text"
    BINARY = "binary"


# === Service-level file I/O (snake_case, no rename_all) ===


class PutFileEntry(WireModel):
    """One file to write (service-level, snake_case, all fields emitted).

    ``create_dirs`` defaults to ``True`` via ``default_true``; ``append``
    defaults to ``False`` via ``#[serde(default)]``. Neither is ``Option``
    and there is no ``skip_serializing_if`` → both are always on the wire.
    Used for chunked writes: first chunk ``append = False`` (create /
    truncate), subsequent chunks ``append = True``.
    """

    path: str
    content: str
    create_dirs: bool = Field(default_factory=_default_true)
    append: bool = False


class PutFileResult(_DropNoneWire):
    """Per-file result from ``workspace.put_files`` (snake_case).

    ``error`` / ``hash`` are ``Option<String>`` with
    ``skip_serializing_if = "Option::is_none"`` → omitted when ``None``
    (inherited :class:`_DropNoneWire`). ``path`` / ``ok`` are always
    emitted. ``hash`` is the SHA-256 of the chunk written this call (for
    ``append = True`` it is the appended chunk's hash, not the full file).
    """

    path: str
    ok: bool
    error: str | None = None
    hash: str | None = None


class PutFilesRes(WireModel):
    """Response for :class:`PutFilesReq` (snake_case)."""

    results: list[PutFileResult]


class PutFilesReq(WireModel):
    """``workspace.put_files`` (snake_case, non-transactional bulk write).

    Service-level write: NOT tracked in the hunk tracker, NOT visible to
    the model. Files are written sequentially; if file N fails, files
    1..N-1 are already on disk and will NOT roll back — callers must
    inspect per-file results in :class:`PutFilesRes`.
    """

    files: list[PutFileEntry]

    METHOD: ClassVar[str] = "workspace.put_files"
    Response: ClassVar[type] = PutFilesRes


class GetFileEntry(_DropNoneWire):
    """One file to read, with optional cache validation + byte-range (snake_case).

    ``if_none_match`` / ``offset`` / ``length`` are ``Option`` with
    ``skip_serializing_if = "Option::is_none"`` → omitted when ``None``.
    Byte ranges are returned as UTF-8 ``String``; a range that splits a
    multi-byte codepoint yields a per-file error rather than invalid text.
    """

    path: str
    if_none_match: str | None = None
    offset: int | None = None
    length: int | None = None


class GetFileResult(_DropNoneWire):
    """Per-file result from ``workspace.get_files`` (snake_case).

    ``content`` / ``hash`` / ``size`` / ``error`` are ``Option`` with
    ``skip_serializing_if`` → omitted when ``None``. ``matched`` is a
    non-``Option`` ``bool`` with ``#[serde(default)]`` → always emitted
    (``False`` when ``if_none_match`` was absent or did not match).
    ``path`` / ``exists`` are always emitted.
    """

    path: str
    exists: bool
    content: str | None = None
    hash: str | None = None
    matched: bool = False
    size: int | None = None
    error: str | None = None


class GetFilesRes(WireModel):
    """Response for :class:`GetFilesReq` (snake_case)."""

    results: list[GetFileResult]


class GetFilesReq(WireModel):
    """``workspace.get_files`` (snake_case, bulk read with cache validation)."""

    files: list[GetFileEntry]

    METHOD: ClassVar[str] = "workspace.get_files"
    Response: ClassVar[type] = GetFilesRes


# === fs_* extension responses ===


class FsListNode(_DropNoneWire):
    """One listed node for ``workspace.fs_list`` (camelCase).

    Shell-aligned ``x.ai/fs/list`` shape: ``node_type`` is a free-form
    ``String`` with ``#[serde(rename = "type")]`` on top of
    ``rename_all = "camelCase"`` → wire key is ``"type"`` (R74/R75 applied
    the same override to enum fields; this is the ``str`` analogue).
    ``is_symlink`` / ``size`` / ``modified_at`` are ``skip_serializing_if``
    Options → omitted when ``None``. ``modified_at`` is an RFC 3339
    ``String`` (contrast :attr:`ClientFsListNode.mtime_ms` epoch millis).
    """

    model_config = _CAMEL
    name: str
    path: str
    node_type: str = Field(alias="type")
    is_symlink: bool | None = None
    size: int | None = None
    modified_at: str | None = None


class FsListData(WireModel):
    """Response for :class:`FsListReq` (snake_case, **no** ``rename_all``).

    Despite :class:`FsListNode` being camelCase, the outer envelope is
    snake_case (no ``rename_all``) — the Req/Res asymmetry of this file.
    Both fields always emitted.
    """

    nodes: list[FsListNode]
    truncated: bool


class FsExistsData(WireModel):
    """Response for :class:`FsExistsReq` (camelCase). ``exists`` always emitted."""

    model_config = _CAMEL
    exists: bool


class FsReadFileData(_DropNoneWire):
    """Response for :class:`FsReadFileReq` (camelCase).

    ``content_base64`` / ``line_count`` are ``skip_serializing_if`` Options
    → omitted when ``None``. ``content`` / ``size`` always emitted;
    ``content_type`` is a free-form ``String`` with ``#[serde(rename =
    "type")]`` → wire key ``"type"`` (the ``str`` analogue of R74/R75's
    enum override).
    """

    model_config = _CAMEL
    content: str
    content_base64: str | None = None
    size: int
    line_count: int | None = None
    content_type: str = Field(alias="type")


# === fs_* extension requests (snake_case, no rename_all) ===


class FsListReq(WireModel):
    """``workspace.fs_list`` (snake_case).

    ``cwd`` is ``Option<PathBuf>`` with **only** ``#[serde(default)]``
    (no ``skip_serializing_if``) → ``None`` emits ``"cwd": null`` (hence
    this is a plain :class:`WireModel`, not :class:`_DropNoneWire`).
    ``depth`` / ``limit`` default via the grok default functions;
    ``include_hidden`` / ``follow_symlinks`` / ``respect_git_ignore``
    default ``True`` via ``default_true``; ``offset`` defaults ``0``;
    ``include_globs`` / ``exclude_globs`` default ``[]``. ``offset`` is
    applied after the dirs-first case-insensitive sort (divergent from
    the shell, which has no offset).
    """

    path: str
    cwd: str | None = None
    depth: int = Field(default_factory=_default_depth)
    limit: int = Field(default_factory=_default_limit)
    offset: int = 0
    include_hidden: bool = Field(default_factory=_default_true)
    follow_symlinks: bool = Field(default_factory=_default_true)
    respect_git_ignore: bool = Field(default_factory=_default_true)
    include_globs: list[str] = Field(default_factory=list)
    exclude_globs: list[str] = Field(default_factory=list)

    METHOD: ClassVar[str] = "workspace.fs_list"
    Response: ClassVar[type] = FsListData


class FsExistsReq(WireModel):
    """``workspace.fs_exists`` (snake_case).

    ``cwd`` is ``#[serde(default)]``-only ``Option`` → ``None`` emits
    ``"cwd": null``.
    """

    path: str
    cwd: str | None = None

    METHOD: ClassVar[str] = "workspace.fs_exists"
    Response: ClassVar[type] = FsExistsData


class FsReadFileReq(WireModel):
    """``workspace.fs_read_file`` (snake_case).

    ``cwd`` / ``offset`` / ``length`` are ``#[serde(default)]``-only Options
    → ``None`` emits ``null``. ``max_bytes`` defaults via
    ``default_max_bytes`` (1 MiB); ``encoding`` defaults
    :attr:`FsReadEncoding.UTF8`. When ``offset`` / ``length`` are absent the
    whole file is read; a non-UTF-8 range falls back to base64 regardless
    of ``encoding``.
    """

    path: str
    cwd: str | None = None
    offset: int | None = None
    length: int | None = None
    max_bytes: int = Field(default_factory=_default_max_bytes)
    encoding: FsReadEncoding = FsReadEncoding.UTF8

    METHOD: ClassVar[str] = "workspace.fs_read_file"
    Response: ClassVar[type] = FsReadFileData


class FsWriteFileReq(WireModel):
    """``workspace.fs_write_file`` (snake_case, ``Response = ()``).

    Server acknowledges with no payload (unit). ``cwd`` is
    ``#[serde(default)]``-only ``Option`` → ``None`` emits ``null``;
    ``create_dirs`` defaults ``True`` via ``default_true``.
    """

    path: str
    cwd: str | None = None
    content: str
    create_dirs: bool = Field(default_factory=_default_true)

    METHOD: ClassVar[str] = "workspace.fs_write_file"
    Response: ClassVar[type] = type(None)


class FsDeleteFileReq(WireModel):
    """``workspace.fs_delete_file`` (snake_case, ``Response = ()``).

    Server acknowledges with no payload (unit). ``cwd`` is
    ``#[serde(default)]``-only ``Option`` → ``None`` emits ``null``.
    """

    path: str
    cwd: str | None = None

    METHOD: ClassVar[str] = "workspace.fs_delete_file"
    Response: ClassVar[type] = type(None)


# === client_fs_* (read-only client ops, camelCase both sides) ===
#
# Distinct from the shell-facing ``workspace.fs_*`` ops: every ``path`` is
# workspace-root-relative (not absolute), timestamps are ``mtimeMs`` epoch
# milliseconds (not RFC 3339 strings), ``client_fs_list`` paginates with a
# post-sort ``offset``, and reads are binary-safe (base64 chunks). The
# camelCase wire format is shared verbatim by the workspace server
# (``xai-grok-workspace``) and the grok.com backend so a field rename
# breaks both — hence the standalone CLIENT_FS_*_METHOD constants below
# (grok source L309-327).

#: Wire method name for :class:`ClientFsListReq`.
CLIENT_FS_LIST_METHOD = "workspace.client_fs_list"
#: Wire method name for :class:`ClientFsStatReq`.
CLIENT_FS_STAT_METHOD = "workspace.client_fs_stat"
#: Wire method name for :class:`ClientFsReadFileReq`.
CLIENT_FS_READ_FILE_METHOD = "workspace.client_fs_read_file"


class ClientFsListNode(_DropNoneWire):
    """One listed node for ``workspace.client_fs_list`` (camelCase).

    Shell-aligned except ``path`` (workspace-root-relative) and ``mtime_ms``
    (epoch millis). ``node_type`` is the typed :class:`FsNodeType` enum
    with ``#[serde(rename = "type")]`` → wire key ``"type"``
    (``"directory"`` / ``"file"``). ``is_symlink`` / ``size`` / ``mtime_ms``
    are ``skip_serializing_if`` Options → omitted when ``None``.
    """

    model_config = _CAMEL
    name: str
    path: str
    node_type: FsNodeType = Field(alias="type")
    is_symlink: bool | None = None
    size: int | None = None
    mtime_ms: int | None = None


class ClientFsListRes(WireModel):
    """Response for :class:`ClientFsListReq` (camelCase).

    ``nodes`` is the post-sort page slice ``[offset, offset + limit)``;
    ``truncated`` is ``True`` when more entries exist beyond the page or
    the server's collection cap was hit. Both always emitted.
    """

    model_config = _CAMEL
    nodes: list[ClientFsListNode]
    truncated: bool


class ClientFsStatRes(_DropNoneWire):
    """Response for :class:`ClientFsStatReq` (camelCase).

    A missing path (including one whose intermediate component is a file
    rather than a directory) is ``exists: False`` with all other fields
    absent — not an RPC error. ``node_type`` / ``size`` / ``mtime_ms`` /
    ``hash`` are ``skip_serializing_if`` Options → omitted when ``None``;
    ``exists`` is always emitted.
    """

    model_config = _CAMEL
    exists: bool
    node_type: FsNodeType | None = None
    size: int | None = None
    mtime_ms: int | None = None
    hash: str | None = None


class ClientFsReadFileRes(_DropNoneWire):
    """Response for :class:`ClientFsReadFileReq` (camelCase).

    Exactly one of ``content`` / ``content_base64`` is populated, matching
    ``type``: ``text`` ⇒ ``content`` (unless base64 was requested),
    ``binary`` ⇒ ``content_base64``. ``content`` / ``content_base64`` are
    ``skip_serializing_if`` Options → omitted when ``None``; ``size`` /
    ``hash`` always emitted; ``content_type`` is the typed
    :class:`FsContentType` enum with ``#[serde(rename = "type")]`` → wire
    key ``"type"``.
    """

    model_config = _CAMEL
    content: str | None = None
    content_base64: str | None = None
    size: int
    hash: str
    content_type: FsContentType = Field(alias="type")


class ClientFsListReq(WireModel):
    """``workspace.client_fs_list`` (camelCase, ACP-compatible).

    No ``cwd`` field (paths are workspace-root-relative). ``depth``
    defaults via ``default_client_depth`` (1); ``limit`` via
    ``default_client_limit`` (1000); ``include_hidden`` /
    ``follow_symlinks`` / ``respect_git_ignore`` default ``True`` via
    ``default_true``; ``offset`` defaults ``0``; ``include_globs`` /
    ``exclude_globs`` default ``[]``. No ``Option`` fields → all keys
    always present on the wire.
    """

    model_config = _CAMEL
    path: str
    depth: int = Field(default_factory=_default_client_depth)
    include_hidden: bool = Field(default_factory=_default_true)
    limit: int = Field(default_factory=_default_client_limit)
    offset: int = 0
    follow_symlinks: bool = Field(default_factory=_default_true)
    respect_git_ignore: bool = Field(default_factory=_default_true)
    include_globs: list[str] = Field(default_factory=list)
    exclude_globs: list[str] = Field(default_factory=list)

    METHOD: ClassVar[str] = CLIENT_FS_LIST_METHOD
    Response: ClassVar[type] = ClientFsListRes


class ClientFsStatReq(WireModel):
    """``workspace.client_fs_stat`` (camelCase).

    Existence, metadata, and a content hash for one workspace-root-relative
    path.
    """

    model_config = _CAMEL
    path: str

    METHOD: ClassVar[str] = CLIENT_FS_STAT_METHOD
    Response: ClassVar[type] = ClientFsStatRes


class ClientFsReadFileReq(WireModel):
    """``workspace.client_fs_read_file`` (camelCase, binary-safe chunked read).

    Unlike ``workspace.get_files``, byte ranges need no UTF-8 alignment —
    chunks transfer as base64. ``offset`` / ``length`` are
    ``#[serde(default)]``-only Options → ``None`` emits ``null`` (hence a
    plain :class:`WireModel`, not :class:`_DropNoneWire`); ``max_bytes``
    defaults via ``default_max_bytes`` (1 MiB); ``encoding`` defaults
    :attr:`FsReadEncoding.UTF8`. An unset ``length`` still returns at most
    ``max_bytes``; detect "more data" by comparing returned bytes (from
    ``offset``) against ``size``.
    """

    model_config = _CAMEL
    path: str
    offset: int | None = None
    length: int | None = None
    max_bytes: int = Field(default_factory=_default_max_bytes)
    encoding: FsReadEncoding = FsReadEncoding.UTF8

    METHOD: ClassVar[str] = CLIENT_FS_READ_FILE_METHOD
    Response: ClassVar[type] = ClientFsReadFileRes
