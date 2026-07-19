"""Git RPCs (R74).

Fusion of grok's ``xai-grok-workspace-types::rpc::git`` — 20 ``workspace.git_*``
/ ``workspace.detect_vcs_kind`` methods plus the 4 VCS / change / status enums
and ~22 wire data types they trade in. This file is the **dependency root** of
the remaining RPC crate types: :class:`ChangeType` and :class:`GitFileChange`
are re-used by the (R75+) worktree / hunks modules, so it lands first.

The crate's git module is the most serde-pattern-dense file in the RPC layer.
Eight patterns land here, five of them new to the layer:

* **camelCase wire keys** (R70's ``_CAMEL`` reused) — ~15 structs carry
  ``#[serde(rename_all = "camelCase")]`` (``GitFileChange``, ``GitStatusData``,
  ``GitInfoData``, ``RepoInfo``, ``CommitWithPatchData``, …). Field names
  serialise as camelCase via ``alias_generator=to_camel`` while still
  validating under snake_case (``populate_by_name=True``).
* **``rename = "type"`` single-key override** — :class:`GitFileChange`'s
  ``change_type`` field serialises under the literal key ``"type"`` (not the
  ``changeType`` the camelCase generator would produce). A per-field
  ``Field(alias="type")`` overrides the generator; distinct from R67's
  adjacent-tagged ``{"type", "data"}`` enums.
* **custom default functions** (R70's ``default_respect_gitignore`` extended) —
  ``default_true`` → ``True``, ``default_head`` → ``"HEAD"``,
  ``default_working`` → ``"working"``, ``default_max_file_bytes`` → ``0``. In
  pydantic these are plain field defaults (``include_untracked: bool = True``,
  ``version: str = "HEAD"``, …); the field is always emitted (no
  ``skip_serializing_if``), matching ``#[serde(default = …)]`` semantics
  (default affects de-serialisation only, never omits on serialisation).
* **mixed ``skip_serializing_if`` matrix** — unlike R73's :class:`SkillInfo`
  (every ``Option`` field carries ``Option::is_none``), this module is
  *mixed*: some structs omit every ``None`` (A-class → the R73 wrap
  ``_omit_none``), some keep ``None`` as wire ``null`` (``GitError.path``,
  ``CommitResult.warning``, ``CheckoutCommitResponse.error``,
  ``GitBranchListData.current_branch``, ``GitCollectChangesResponse.uncommitted``
  — none of which carry ``skip_serializing_if``), and one — :class:`GitInfoData`
  — is *half-and-half* (``current_branch`` keeps ``null``, ``default_branch`` /
  ``vcs_kind`` are omitted). Each class picks the matching serializer.
* **``skip_serializing_if = "Vec::is_empty"`` (NEW)** —
  :class:`CommitWithPatchData.binary_files` and
  :class:`UncommittedChangesData`'s staged/unstaged binary-file lists default
  to empty **and** are omitted when empty (a non-``Option``, empty-collection
  elision distinct from R72's ``String::is_empty`` and R73's ``Option::is_none``).
  Implemented as a wrap ``model_serializer`` that drops the specific empty key.
* **manual ``Deserialize`` with legacy-flat compatibility (NEW, most complex)**
  — :class:`GitStatusExtResponse` only derives ``Serialize``; its ``Deserialize``
  is hand-written so a legacy *flat* ``GitStatusData`` payload (version skew
  with an older server, carrying none of the envelope's ``format`` / ``data`` /
  ``prompt`` keys) is wrapped as ``format: Structured`` rather than parsed as
  empty. In pydantic this is a ``model_validator(mode="before")`` that rewraps
  any non-empty mapping lacking the envelope keys.
* **non-``Option`` field always emitted** — :class:`RepoInfo`'s ``is_detached``
  (``#[serde(default)] bool``) and every ``Vec`` without ``skip_serializing_if``
  survive ``_omit_none`` (``False`` / ``[]`` are not ``None``).
* **``Option``-shaped ``Response``** — five methods return ``Option<PathBuf>`` /
  ``Option<String>`` / ``Option<GitInfoData>`` / ``()`` / ``VcsKind``, surfaced
  as ``str | None`` / ``GitInfoData | None`` / ``type(None)`` / ``VcsKind``
  ClassVars (envelope's ``TypeAdapter`` handles each shape).

SYNC: mirrors the serde shape of ``xai-grok-workspace-types::rpc::git``.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import ConfigDict, Field, model_serializer, model_validator
from pydantic.alias_generators import to_camel

from minimax_code.workspace_types._wire import WireModel, sort_mappings

__all__ = [
    # enums
    "VcsKind",
    "ChangeType",
    "GitStatusFormat",
    "DiscardScope",
    # wire data types
    "CommitData",
    "CommitResult",
    "StageData",
    "GitFileChange",
    "GitStatusData",
    "GitStatusExtResponse",
    "GitError",
    "GitReadFile",
    "GitReadFilesData",
    "GitDiffsData",
    "GitInfoData",
    "GitBranchEntry",
    "GitBranchListData",
    "RepoInfo",
    "DiffStatsSummary",
    "PublicBaseData",
    "CommitWithPatchData",
    "IdentityData",
    "BinaryFileInfoData",
    "UncommittedChangesData",
    "UntrackedFileData",
    "GitCollectChangesResponse",
    # constant
    "UNTRACKED_CONTENT_THRESHOLD",
    # requests
    "GitStatusReq",
    "GitStatusExtReq",
    "GitFilesReq",
    "GitDiffReq",
    "GitStageReq",
    "GitStageContentReq",
    "GitUnstageReq",
    "GitDiscardReq",
    "GitCommitReq",
    "GitCheckoutReq",
    "GitStashReq",
    "GitInfoReq",
    "GitBranchesReq",
    "GitResolveRootReq",
    "GitCurrentCommitReq",
    "DetectVcsKindReq",
    "GitCheckoutCommitReq",
    "GitBranchInfoReq",
    "GitMetadataReq",
    "GitCollectChangesReq",
]

#: camelCase wire config shared by ~15 structs. ``to_camel`` mirrors
#: ``#[serde(rename_all = "camelCase")]``; combined with ``WireModel.to_wire``'s
#: ``by_alias=True`` dump, field names serialise as camelCase while still
#: validating under their snake_case names. (R70 pattern, reused verbatim.)
_CAMEL = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class _CamelOmitNone(WireModel):
    """camelCase wire + drop every ``None``-valued key (all-Option-skip structs).

    Mirrors grok structs whose **every** ``Option`` field carries
    ``#[serde(skip_serializing_if = "Option::is_none")]`` (e.g.
    :class:`CommitData`, :class:`GitFileChange`, :class:`GitStatusData`,
    :class:`RepoInfo`, :class:`IdentityData`). The wrap ``model_serializer``
    takes the default dump from ``handler(self)`` (already camelCase-aliased
    under ``by_alias=True``) and drops every key whose value is ``None`` in one
    comprehension — the R73 bulk-elision pattern, here combined with
    ``_CAMEL``. Bool / required / ``Vec`` fields survive (never ``None``), so
    ``RepoInfo.is_detached = False`` and every empty non-skip ``Vec`` are
    emitted as-is. The serializer fires at any nesting depth (``model_dump``
    honours it recursively), so ``GitFileChange`` nested inside
    ``GitStatusData.staged`` skips correctly without a ``to_wire`` override.
    """

    model_config = _CAMEL

    @model_serializer(mode="wrap")
    def _omit_none(self, handler):  # noqa: ANN001, ANN202
        # handler(self) returns the default JSON-safe dump (all fields,
        # camelCase-aliased); drop every None-valued key to reproduce
        # #[serde(skip_serializing_if = "Option::is_none")] across every
        # Option field of the struct at once. Bool/required/Vec are never
        # None, so they always survive. sort_mappings re-sorts keys in to_wire.
        raw = handler(self)
        return {k: v for k, v in raw.items() if v is not None}


# =========================================================================
# Enums (4): VcsKind (camelCase), ChangeType/GitStatusFormat/DiscardScope (lowercase)
# =========================================================================


class VcsKind(StrEnum):
    """Kind of version control system detected for a workspace. camelCase wire.

    Mirrors ``#[serde(rename_all = "camelCase")] enum VcsKind``: ``Git`` →
    ``"git"`` (the ``#[default]``), ``JujutsuColocated`` →
    ``"jujutsuColocated"``, ``None`` → ``"none"``. :meth:`is_jj` /
    :meth:`is_repo` mirror the source's predicate methods (Desktop uses
    ``vcs_kind`` to disable features for unsupported VCS).
    """

    GIT = "git"
    JUJUTSU_COLOCATED = "jujutsuColocated"
    NONE = "none"

    def is_jj(self) -> bool:
        """Whether this is a Jujutsu-managed (colocated) repo."""
        return self is VcsKind.JUJUTSU_COLOCATED

    def is_repo(self) -> bool:
        """Whether any VCS was detected (not the ``None`` variant)."""
        return self is not VcsKind.NONE


class ChangeType(StrEnum):
    """Git change classification. lowercase wire.

    Mirrors ``#[serde(rename_all = "lowercase")] enum ChangeType``:
    ``create`` / ``edit`` / ``delete`` / ``rename`` / ``copy`` / ``typechange``
    / ``untracked``. Does **not** derive ``Default`` in the source (no
    ``#[default]``), so no ``default()`` — it is the always-present
    ``change_type`` field of :class:`GitFileChange`. Reused by the (R75+)
    worktree / hunks modules, hence this file lands first as the dependency
    root.
    """

    CREATE = "create"
    EDIT = "edit"
    DELETE = "delete"
    RENAME = "rename"
    COPY = "copy"
    TYPECHANGE = "typechange"
    UNTRACKED = "untracked"


class GitStatusFormat(StrEnum):
    """Output selector for ``git_status_ext``. lowercase wire.

    Mirrors ``#[serde(rename_all = "lowercase")] enum GitStatusFormat``:
    ``Structured`` → ``"structured"`` (the ``#[default]``), ``Prompt`` →
    ``"prompt"`` (compact plain-text status for prompt injection).
    """

    STRUCTURED = "structured"
    PROMPT = "prompt"


class DiscardScope(StrEnum):
    """Scope for ``git_discard`` operations. lowercase wire.

    Mirrors ``#[serde(rename_all = "lowercase")] enum DiscardScope``:
    ``Working`` / ``Staged`` / ``Both`` (the ``#[default]``).
    """

    WORKING = "working"
    STAGED = "staged"
    BOTH = "both"


# =========================================================================
# Leaf wire data types
# =========================================================================


class CommitData(_CamelOmitNone):
    """``git_commit`` success payload. camelCase wire.

    Both ``Option`` fields (``commit_hash``, ``output``) carry
    ``#[serde(skip_serializing_if = "Option::is_none")]`` → omitted when
    ``None`` via :class:`_CamelOmitNone`. Derives ``Default`` → both ``None``.
    """

    commit_hash: str | None = None
    output: str | None = None


class DiffStatsSummary(WireModel):
    """Diff stats summary for wire transfer. camelCase wire; derives Default.

    Three required ``usize`` fields — no ``Option``, so no elision.
    """

    model_config = _CAMEL

    files_changed: int
    insertions: int
    deletions: int


class IdentityData(_CamelOmitNone):
    """Author / committer identity for wire transfer. camelCase wire.

    ``name`` / ``email`` / ``time`` are ``Option`` (each
    ``skip_serializing_if = "Option::is_none"``) → omitted when ``None``.
    ``time_seconds`` (``i64``) and ``offset_minutes`` (``i32``) are required
    and always emitted.
    """

    name: str | None = None
    email: str | None = None
    time: str | None = None
    time_seconds: int
    offset_minutes: int


class BinaryFileInfoData(_CamelOmitNone):
    """Binary file info for wire transfer. camelCase wire.

    ``exclude_reason`` / ``content_base64`` are ``Option`` (each
    ``skip_serializing_if = "Option::is_none"``). The remaining fields
    (``path``, ``status``, ``size_bytes``, ``blob_included``, ``truncated``)
    are required and always emitted.
    """

    path: str
    status: str
    size_bytes: int
    blob_included: bool
    truncated: bool
    exclude_reason: str | None = None
    content_base64: str | None = None


class UntrackedFileData(_CamelOmitNone):
    """Untracked file info for wire transfer. camelCase wire.

    ``content_base64`` is the sole ``Option`` (``skip_serializing_if =
    "Option::is_none"``); the rest are required. Content inclusion rules:
    files larger than :data:`UNTRACKED_CONTENT_THRESHOLD` (1 MB) or binary
    files have ``content_base64 = None`` and ``content_included = False``.
    """

    path: str
    is_binary: bool
    size_bytes: int
    truncated: bool
    content_base64: str | None = None
    content_included: bool


class GitBranchEntry(WireModel):
    """Single branch entry from ``git_branches``. camelCase wire; no Option."""

    model_config = _CAMEL

    name: str
    current: bool
    remote: bool


class PublicBaseData(WireModel):
    """Public base commit info for wire transfer. camelCase wire; no Option."""

    model_config = _CAMEL

    commit: str
    refs: list[str]


class StageData(WireModel):
    """Response for ``git_stage``. camelCase wire; derives Default.

    A single required ``Vec<String>`` — no ``Option``, no elision.
    """

    model_config = _CAMEL

    paths: list[str] = Field(default_factory=list)


class GitError(WireModel):
    """A per-file git read error. camelCase wire.

    ``path`` is ``Option<String>`` **without** ``skip_serializing_if`` → its
    ``None`` stays as wire ``null`` (the error may not map to a specific path).
    Hence this is a plain :class:`WireModel` (not :class:`_CamelOmitNone`):
    the default dump emits ``"path": null`` alongside the required ``code`` /
    ``message``.
    """

    model_config = _CAMEL

    path: str | None = None
    code: str
    message: str


class GitReadFile(_CamelOmitNone):
    """One file read at a version. camelCase wire.

    ``path`` / ``version`` / ``content`` are required; ``is_binary`` is
    ``Option`` (``skip_serializing_if = "Option::is_none"``) → omitted when
    ``None`` via :class:`_CamelOmitNone`.
    """

    path: str
    version: str
    content: str
    is_binary: bool | None = None


class GitFileChange(_CamelOmitNone):
    """One changed file in a status / diff. camelCase wire.

    Eight ``Option`` fields (``old_path``, ``staged``, ``patch``,
    ``patch_bytes``, ``patch_lines``, ``old_text``, ``new_text``) each carry
    ``#[serde(skip_serializing_if = "Option::is_none")]`` → omitted when
    ``None`` via :class:`_CamelOmitNone`. ``path`` / ``additions`` /
    ``deletions`` are required and always emitted.

    ``change_type`` is the ``#[serde(rename = "type")]`` field — its wire key
    is the literal ``"type"`` (not the ``"changeType"`` the camelCase generator
    would produce). A per-field ``Field(alias="type")`` overrides
    ``alias_generator``; combined with ``populate_by_name=True`` it validates
    under either ``change_type`` (snake) or ``type`` (wire alias), and
    serialises under ``"type"``.
    """

    path: str
    old_path: str | None = None
    change_type: ChangeType = Field(alias="type")
    staged: bool | None = None
    additions: int
    deletions: int
    patch: str | None = None
    patch_bytes: int | None = None
    patch_lines: int | None = None
    old_text: str | None = None
    new_text: str | None = None


# =========================================================================
# Composite wire data types
# =========================================================================


class CommitResult(WireModel):
    """Response of ``git_commit``. snake_case wire (no ``rename_all``).

    ``data`` is the required :class:`CommitData` (nested; its
    :class:`_CamelOmitNone` serializer fires at this nesting depth).
    ``warning`` is ``Option<String>`` **without** ``skip_serializing_if`` →
    its ``None`` stays as wire ``null``. Hence a plain :class:`WireModel`:
    the default dump emits ``"warning": null`` alongside ``"data"``.
    """

    data: CommitData
    warning: str | None = None


class CommitWithPatchData(WireModel):
    """Commit data for wire transfer. camelCase wire.

    ``summary`` / ``message`` / ``patch_base64`` are ``Option`` (each
    ``skip_serializing_if = "Option::is_none"``). ``binary_files`` is a
    non-``Option`` ``Vec<BinaryFileInfoData>`` with ``#[serde(default,
    skip_serializing_if = "Vec::is_empty")]`` — it defaults to empty **and** is
    omitted when empty (NEW: a non-``Option`` empty-collection elision,
    distinct from R72's ``String::is_empty`` and R73's ``Option::is_none``).
    The wrap serializer drops every ``None`` key, then drops ``binaryFiles``
    specifically when empty; the required ``id`` / ``parents`` / ``author`` /
    ``committer`` / ``stats`` survive.
    """

    model_config = _CAMEL

    id: str
    parents: list[str]
    author: IdentityData
    committer: IdentityData
    summary: str | None = None
    message: str | None = None
    patch_base64: str | None = None
    stats: DiffStatsSummary
    binary_files: list[BinaryFileInfoData] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def _omit(self, handler):  # noqa: ANN001, ANN202
        # Drop None-valued keys (summary/message/patch_base64), then drop
        # binaryFiles when empty — reproducing #[serde(default,
        # skip_serializing_if = "Vec::is_empty")] on top of the three
        # Option::is_none fields. handler(self) is camelCase-aliased already.
        raw = handler(self)
        out = {k: v for k, v in raw.items() if v is not None}
        if not out.get("binaryFiles"):
            out.pop("binaryFiles", None)
        return out


class UncommittedChangesData(WireModel):
    """Uncommitted changes for wire transfer. camelCase wire.

    ``staged_patch_base64`` / ``unstaged_patch_base64`` are ``Option`` (each
    ``skip_serializing_if = "Option::is_none"``). ``staged_binary_files`` and
    ``unstaged_binary_files`` are non-``Option`` ``Vec`` with ``#[serde(default,
    skip_serializing_if = "Vec::is_empty")]`` — omitted when empty (NEW
    pattern, same as :class:`CommitWithPatchData.binary_files`). The required
    ``staged_stats`` / ``unstaged_stats`` always survive.
    """

    model_config = _CAMEL

    staged_patch_base64: str | None = None
    staged_stats: DiffStatsSummary
    unstaged_patch_base64: str | None = None
    unstaged_stats: DiffStatsSummary
    staged_binary_files: list[BinaryFileInfoData] = Field(default_factory=list)
    unstaged_binary_files: list[BinaryFileInfoData] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def _omit(self, handler):  # noqa: ANN001, ANN202
        # Drop None keys (the two patch_base64), then drop each binary-files
        # list specifically when empty.
        raw = handler(self)
        out = {k: v for k, v in raw.items() if v is not None}
        if not out.get("stagedBinaryFiles"):
            out.pop("stagedBinaryFiles", None)
        if not out.get("unstagedBinaryFiles"):
            out.pop("unstagedBinaryFiles", None)
        return out


class GitStatusData(_CamelOmitNone):
    """Structured git status. camelCase wire; derives Default.

    Nine ``Option`` fields (``root``, ``main_root``, ``is_worktree``,
    ``branch``, ``commit``, ``upstream``, ``remote_url``, ``ahead``, ``behind``)
    each carry ``#[serde(skip_serializing_if = "Option::is_none")]`` → omitted
    when ``None`` via :class:`_CamelOmitNone`. The required ``staged`` /
    ``unstaged`` ``Vec<GitFileChange>`` always survive (empty list emitted as
    ``[]`` — no ``skip_serializing_if`` on them).
    """

    root: str | None = None
    main_root: str | None = None
    is_worktree: bool | None = None
    branch: str | None = None
    commit: str | None = None
    upstream: str | None = None
    remote_url: str | None = None
    ahead: int | None = None
    behind: int | None = None
    staged: list[GitFileChange] = Field(default_factory=list)
    unstaged: list[GitFileChange] = Field(default_factory=list)


class GitReadFilesData(WireModel):
    """Response for ``git_files``. camelCase wire; derives Default.

    Two required ``Vec`` fields (``files``, ``errors``) — no ``Option``, no
    elision. Nested :class:`GitReadFile` / :class:`GitError` serializers fire
    at this depth.
    """

    model_config = _CAMEL

    files: list[GitReadFile] = Field(default_factory=list)
    errors: list[GitError] = Field(default_factory=list)


class GitDiffsData(WireModel):
    """Response for ``git_diff``. camelCase wire; derives Default.

    One required ``Vec<GitFileChange>`` field. :meth:`collect_patches` mirrors
    the source helper (concatenate non-empty patches, joined by ``\\n``;
    ``None`` when no patches).
    """

    model_config = _CAMEL

    files: list[GitFileChange] = Field(default_factory=list)

    def collect_patches(self) -> str | None:
        """Concatenate every present ``patch`` (``\\n``-joined), or ``None``."""
        patches = [f.patch for f in self.files if f.patch is not None]
        return "\n".join(patches) if patches else None


class GitInfoData(WireModel):
    """Structured repo info from ``git_info`` / ``git_branch_info``. camelCase wire.

    **Mixed skip matrix** (the one half-and-half struct in this module):
    ``current_branch`` is ``Option<String>`` **without** ``skip_serializing_if``
    → its ``None`` (detached HEAD) stays as wire ``null``; ``default_branch``
    and ``vcs_kind`` carry ``skip_serializing_if = "Option::is_none"`` → omitted
    when ``None``. A bespoke wrap serializer drops only ``defaultBranch`` /
    ``vcsKind`` when ``None`` and leaves ``currentBranch`` as ``null`` —
    neither the bulk :class:`_CamelOmitNone` (would wrongly drop
    ``currentBranch``) nor a plain :class:`WireModel` (would wrongly keep
    ``defaultBranch`` / ``vcsKind``) matches.
    """

    model_config = _CAMEL

    root: str
    remotes: list[str]
    current_branch: str | None = None  # NO skip_serializing_if → keep null
    default_branch: str | None = None  # skip_serializing_if → omit None
    vcs_kind: VcsKind | None = None  # skip_serializing_if → omit None

    @model_serializer(mode="wrap")
    def _omit(self, handler):  # noqa: ANN001, ANN202
        # Only defaultBranch / vcsKind carry skip_serializing_if; currentBranch
        # has no skip → its None must stay as null on the wire. handler(self)
        # is camelCase-aliased already.
        raw = handler(self)
        if raw.get("defaultBranch") is None:
            raw.pop("defaultBranch", None)
        if raw.get("vcsKind") is None:
            raw.pop("vcsKind", None)
        return raw


class GitBranchListData(WireModel):
    """Structured result of ``git_branches``. camelCase wire.

    ``current_branch`` is ``Option<String>`` **without** ``skip_serializing_if``
    → its ``None`` stays as wire ``null``. A plain :class:`WireModel` (the
    default dump emits ``"currentBranch": null``); nested :class:`GitBranchEntry`
    has no ``Option`` so needs no serializer.
    """

    model_config = _CAMEL

    current_branch: str | None = None
    repo_root: str
    branches: list[GitBranchEntry]


class RepoInfo(_CamelOmitNone):
    """Repository metadata for wire transfer. camelCase wire.

    Eight ``Option`` fields (``git_dir``, ``head``, ``branch``, ``upstream``,
    ``upstream_head``, ``remote_url``, ``ahead``, ``behind``) each carry
    ``#[serde(skip_serializing_if = "Option::is_none")]`` → omitted when ``None``
    via :class:`_CamelOmitNone`. ``root`` (required ``String``) and
    ``is_detached`` (``#[serde(default)] bool`` — no ``skip_serializing_if``)
    always survive: ``is_detached = False`` is emitted as ``false`` (``False``
    is not ``None``), matching the source.
    """

    root: str
    git_dir: str | None = None
    head: str | None = None
    branch: str | None = None
    is_detached: bool = False
    upstream: str | None = None
    upstream_head: str | None = None
    remote_url: str | None = None
    ahead: int | None = None
    behind: int | None = None


class CheckoutCommitResponse(WireModel):
    """Response of ``git_checkout_commit``. snake_case wire (no ``rename_all``).

    ``error`` is ``Option<String>`` **without** ``skip_serializing_if`` → its
    ``None`` stays as wire ``null``. A plain :class:`WireModel` (the default
    dump emits ``"error": null`` alongside the three bool fields).
    """

    checked_out: bool
    stashed: bool
    fetched: bool
    error: str | None = None


class GitStatusExtResponse(WireModel):
    """Response wrapper for ``git_status_ext`` (always the same envelope shape).

    snake_case wire (no ``rename_all`` — keys ``format`` / ``data`` / ``prompt``
    have no underscore either way). ``format`` (``GitStatusFormat``) is always
    emitted; ``data`` / ``prompt`` carry ``skip_serializing_if =
    "Option::is_none"`` → omitted when ``None`` (the wrap ``model_serializer``
    drops every ``None`` key, leaving ``format``).

    ``Deserialize`` is hand-written in the source (this struct only derives
    ``Serialize``) so a **legacy flat** ``GitStatusData`` payload — returned by
    an older workspace server during version skew, carrying none of the
    envelope's own keys — is wrapped as ``format: Structured`` rather than
    silently parsed as empty. Here a ``model_validator(mode="before")`` rewraps
    any non-empty mapping lacking ``format`` / ``data`` / ``prompt``; an empty
    mapping (``cls()``) or any payload carrying an envelope key passes through
    to normal field validation.
    """

    format: GitStatusFormat = GitStatusFormat.STRUCTURED
    data: GitStatusData | None = None
    prompt: str | None = None

    @model_serializer(mode="wrap")
    def _omit_none(self, handler):  # noqa: ANN001, ANN202
        # format always survives (a StrEnum, never None); data/prompt are
        # dropped when None. No aliasing (no _CAMEL), so keys stay verbatim.
        raw = handler(self)
        return {k: v for k, v in raw.items() if v is not None}

    @model_validator(mode="before")
    @classmethod
    def _wrap_legacy_flat(cls, data):  # noqa: ANN001, ANN206
        # A legacy flat GitStatusData payload (version skew with an older
        # server) has none of the envelope's own keys and is non-empty; wrap
        # it as {format: structured, data: <payload>, prompt: None}. An empty
        # mapping (cls()) or any payload carrying format/data/prompt passes
        # through to normal validation.
        if isinstance(data, Mapping) and data and not ({"format", "data", "prompt"} & set(data)):
            return {"format": GitStatusFormat.STRUCTURED.value, "data": data, "prompt": None}
        return data

    @classmethod
    def default(cls) -> GitStatusExtResponse:
        # Manual `Default` (not derived) → format: Structured, data/prompt: None.
        return cls()

    @classmethod
    def structured(cls, data: GitStatusData) -> GitStatusExtResponse:
        """Build a structured response (``format: Structured``, ``data`` set)."""
        return cls(format=GitStatusFormat.STRUCTURED, data=data)

    @classmethod
    def with_prompt(cls, text: str) -> GitStatusExtResponse:
        """Build a prompt-formatted response (``format: Prompt``, ``prompt`` set).

        Named ``with_prompt`` (not ``prompt``) to avoid colliding with the
        ``prompt`` field above (F811); mirrors grok's
        ``GitStatusExtResponse::prompt(text)`` constructor.
        """
        return cls(format=GitStatusFormat.PROMPT, prompt=text)

    def to_wire(self) -> dict[str, Any]:  # type: ignore[override]
        """Sorted JSON-safe envelope (``format`` always; ``data``/``prompt`` when set)."""
        return sort_mappings(self.model_dump(mode="json", by_alias=True))


class GitCollectChangesResponse(WireModel):
    """Response of ``git_collect_changes``. camelCase wire.

    ``uncommitted`` is ``Option<UncommittedChangesData>`` **without**
    ``skip_serializing_if`` → its ``None`` stays as wire ``null``. A plain
    :class:`WireModel`; nested :class:`RepoInfo` / :class:`PublicBaseData` /
    :class:`CommitWithPatchData` / :class:`UncommittedChangesData` /
    :class:`UntrackedFileData` serializers fire at this depth.
    """

    model_config = _CAMEL

    repo: RepoInfo
    head: str
    public_base: PublicBaseData
    commits: list[CommitWithPatchData]
    uncommitted: UncommittedChangesData | None = None
    untracked: list[UntrackedFileData]
    warnings: list[str] = Field(default_factory=list)
    total_size_bytes: int


#: Threshold for including untracked file content in the RPC response (1 MB).
#: Files larger than this have ``content_base64 = None`` and must be fetched
#: separately via ``workspace.fs_read_file``. Mirrors the source ``pub const``.
UNTRACKED_CONTENT_THRESHOLD: int = 1024 * 1024


# =========================================================================
# RPC requests (20)
# =========================================================================


class GitStatusReq(WireModel):
    """``workspace.git_status`` — **deprecated** compact JSON-string status.

    The response value is a JSON string (branch, ahead/behind, staged files),
    capped server-side at ~1 KB. Deprecated; use :class:`GitStatusExtReq` with
    ``format = Prompt`` instead. ``Response = serde_json::Value`` → ``Any``.
    Derives ``Default`` → empty struct.
    """

    METHOD: ClassVar[str] = "workspace.git_status"
    Response: ClassVar = Any  # serde_json::Value


class GitStatusExtReq(WireModel):
    """``workspace.git_status_ext`` — structured / prompt status. camelCase wire.

    Manual ``Default`` (not derived) so the two ``default_true`` bools
    (``include_untracked``, ``ignore_submodules``) match their serde field
    defaults (a derived ``Default`` would set them to ``False``). In pydantic
    the field defaults encode that directly (``= True``); ``format`` defaults
    to ``Structured``. ``Response = GitStatusExtResponse``.
    """

    model_config = _CAMEL

    METHOD: ClassVar[str] = "workspace.git_status_ext"
    Response: ClassVar[type] = GitStatusExtResponse

    git_root: str | None = None
    include_untracked: bool = True
    include_stats: bool = False
    ignore_submodules: bool = True
    include_patches: bool = False
    format: GitStatusFormat = GitStatusFormat.STRUCTURED

    @classmethod
    def default(cls) -> GitStatusExtReq:
        # Manual `Default`: matches the serde field defaults (the two
        # default_true bools are True, not False as a derived Default would).
        return cls()


class GitFilesReq(WireModel):
    """``workspace.git_files`` — read files at a version. camelCase wire.

    ``version`` defaults to ``"HEAD"`` (``default_head``). ``Response =
    GitReadFilesData``.
    """

    model_config = _CAMEL

    METHOD: ClassVar[str] = "workspace.git_files"
    Response: ClassVar[type] = GitReadFilesData

    git_root: str | None = None
    paths: list[str]
    version: str = "HEAD"


class GitDiffReq(WireModel):
    """``workspace.git_diff`` — diff between refs / working tree. camelCase wire.

    ``from`` defaults to ``"HEAD"`` (``default_head``), ``to`` to
    ``"working"`` (``default_working``). ``Response = GitDiffsData``.
    """

    model_config = _CAMEL

    METHOD: ClassVar[str] = "workspace.git_diff"
    Response: ClassVar[type] = GitDiffsData

    git_root: str | None = None
    paths: list[str] | None = None
    from_: str = Field(default="HEAD", alias="from")
    to: str = "working"
    include_patch: bool = False
    include_content: bool = False
    merge_base: bool = False


class GitStageReq(WireModel):
    """``workspace.git_stage`` — stage paths. camelCase wire.

    ``paths`` is ``Option<Vec<String>>`` **without** ``skip_serializing_if``
    → ``None`` stays as wire ``null``. ``Response = StageData``.
    """

    model_config = _CAMEL

    METHOD: ClassVar[str] = "workspace.git_stage"
    Response: ClassVar[type] = StageData

    git_root: str | None = None
    paths: list[str] | None = None


class GitStageContentReq(WireModel):
    """``workspace.git_stage_content`` — stage inline content. camelCase wire.

    ``Response = ()`` → ``type(None)``.
    """

    model_config = _CAMEL

    METHOD: ClassVar[str] = "workspace.git_stage_content"
    Response: ClassVar[type] = type(None)

    git_root: str | None = None
    path: str
    content: str


class GitUnstageReq(WireModel):
    """``workspace.git_unstage`` — unstage paths. camelCase wire.

    ``paths`` is ``Option<Vec<String>>`` (no skip → ``null``). ``Response = ()``.
    """

    model_config = _CAMEL

    METHOD: ClassVar[str] = "workspace.git_unstage"
    Response: ClassVar[type] = type(None)

    git_root: str | None = None
    paths: list[str] | None = None


class GitDiscardReq(WireModel):
    """``workspace.git_discard`` — discard changes. camelCase wire.

    ``scope`` defaults to ``Both`` (``#[default]``). ``paths`` is
    ``Option<Vec<String>>`` (no skip → ``null``). ``Response = ()``.
    """

    model_config = _CAMEL

    METHOD: ClassVar[str] = "workspace.git_discard"
    Response: ClassVar[type] = type(None)

    git_root: str | None = None
    paths: list[str] | None = None
    scope: DiscardScope = DiscardScope.BOTH
    include_untracked: bool = False


class GitCommitReq(WireModel):
    """``workspace.git_commit`` — commit (optionally amend / sign / push). camelCase wire.

    ``Response = CommitResult``.
    """

    model_config = _CAMEL

    METHOD: ClassVar[str] = "workspace.git_commit"
    Response: ClassVar[type] = CommitResult

    git_root: str | None = None
    message: str
    amend: bool = False
    signoff: bool = False
    push: bool = False
    sync: bool = False


class GitCheckoutReq(WireModel):
    """``workspace.git_checkout`` — checkout / create a branch. camelCase wire.

    ``Response = ()``.
    """

    model_config = _CAMEL

    METHOD: ClassVar[str] = "workspace.git_checkout"
    Response: ClassVar[type] = type(None)

    git_root: str | None = None
    branch: str
    create: bool = False


class GitStashReq(WireModel):
    """``workspace.git_stash`` — stash changes. camelCase wire. ``Response = ()``."""

    model_config = _CAMEL

    METHOD: ClassVar[str] = "workspace.git_stash"
    Response: ClassVar[type] = type(None)

    git_root: str | None = None
    include_untracked: bool = False


class GitInfoReq(WireModel):
    """``workspace.git_info`` — structured repo info. camelCase wire.

    Derives ``Default`` → ``git_root`` ``None``. ``Response = GitInfoData``.
    """

    model_config = _CAMEL

    METHOD: ClassVar[str] = "workspace.git_info"
    Response: ClassVar[type] = GitInfoData

    git_root: str | None = None

    @classmethod
    def default(cls) -> GitInfoReq:
        return cls()


class GitBranchesReq(WireModel):
    """``workspace.git_branches`` — list branches. camelCase wire.

    Derives ``Default`` → ``git_root`` ``None``. ``Response = GitBranchListData``.
    """

    model_config = _CAMEL

    METHOD: ClassVar[str] = "workspace.git_branches"
    Response: ClassVar[type] = GitBranchListData

    git_root: str | None = None

    @classmethod
    def default(cls) -> GitBranchesReq:
        return cls()


class GitResolveRootReq(WireModel):
    """``workspace.git_resolve_root`` — resolve the git root from a path.

    snake_case wire (no ``rename_all``). ``Response = Option<PathBuf>`` →
    ``str | None``.
    """

    METHOD: ClassVar[str] = "workspace.git_resolve_root"
    Response: ClassVar[type] = str | None

    cwd: str


class GitCurrentCommitReq(WireModel):
    """``workspace.git_current_commit`` — current commit hash for a git root.

    snake_case wire. ``Response = Option<String>`` → ``str | None``.
    """

    METHOD: ClassVar[str] = "workspace.git_current_commit"
    Response: ClassVar[type] = str | None

    git_root: str


class DetectVcsKindReq(WireModel):
    """``workspace.detect_vcs_kind`` — detect VCS kind (git vs jj) for a path.

    snake_case wire. ``Response = VcsKind``.
    """

    METHOD: ClassVar[str] = "workspace.detect_vcs_kind"
    Response: ClassVar[type] = VcsKind

    path: str


class GitCheckoutCommitReq(WireModel):
    """``workspace.git_checkout_commit`` — checkout a commit with auto-stash.

    snake_case wire. ``head_branch`` is ``Option<String>`` (no skip → ``null``).
    ``Response = CheckoutCommitResponse``.
    """

    METHOD: ClassVar[str] = "workspace.git_checkout_commit"
    Response: ClassVar[type] = CheckoutCommitResponse

    git_root: str
    head_commit: str
    head_branch: str | None = None
    stash_if_dirty: bool


class GitBranchInfoReq(WireModel):
    """``workspace.git_branch_info`` — repo info, or ``null`` if not a repo.

    Derives ``Default`` → empty struct. ``Response = Option<GitInfoData>`` →
    ``GitInfoData | None``.
    """

    METHOD: ClassVar[str] = "workspace.git_branch_info"
    Response: ClassVar[type] = GitInfoData | None


class GitMetadataReq(WireModel):
    """``workspace.git_metadata`` — persisted session git metadata (or ``null``).

    Derives ``Default`` → empty struct. ``Response = serde_json::Value`` → ``Any``.
    """

    METHOD: ClassVar[str] = "workspace.git_metadata"
    Response: ClassVar = Any  # serde_json::Value


class GitCollectChangesReq(WireModel):
    """``workspace.git_collect_changes`` — collect repo changes for serialization.

    snake_case wire (no ``rename_all`` — named ``repo_path``, not ``git_root``,
    to match ``SerializeRepoChangesRequest``). ``include_commits`` /
    ``include_uncommitted`` default to ``True`` (``default_true``);
    ``max_file_bytes`` defaults to ``0`` (``default_max_file_bytes`` — no limit);
    ``base_ref`` / ``force_include_paths`` default to ``None`` / ``[]``.
    ``Response = GitCollectChangesResponse``.
    """

    METHOD: ClassVar[str] = "workspace.git_collect_changes"
    Response: ClassVar[type] = GitCollectChangesResponse

    repo_path: str
    include_commits: bool = True
    include_uncommitted: bool = True
    base_ref: str | None = None
    max_file_bytes: int = 0
    force_include_paths: list[str] = Field(default_factory=list)
