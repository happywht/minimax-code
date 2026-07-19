"""Worktree lifecycle RPCs (R75).

Fusion of grok's ``xai-grok-workspace-types::rpc::worktree`` — the 11
``workspace.worktree_*`` / ``create_worktree`` / ``remove_worktree`` /
``apply_worktree`` methods plus the worktree lifecycle wire types. This file
is the **first consumer of R74's dependency root**: it imports
:class:`~minimax_code.workspace_types.rpc.git.ChangeType` and
:class:`~minimax_code.workspace_types.rpc.git.GitFileChange`
(:class:`FileConflict` reuses ``ChangeType`` with the same ``rename = "type"``
override; :data:`ApplyWorktreeResponse` embeds ``list[GitFileChange]``).

This file lands five serde patterns new to the RPC layer:

* **internally tagged enum** — :data:`CreateWorktreeResponse` and
  :data:`ApplyWorktreeResponse` both use ``#[serde(tag = "status")]``: each
  variant flattens into ``{"status": "<tag>", …variant fields…}``. In pydantic
  this is ``Annotated[Union[VariantA, VariantB], Field(discriminator="status")]``
  where each variant carries a ``status: Literal["<tag>"]`` field. This is the
  layer's first tagged union (R70's ``TargetClientId`` was ``untagged``; R74's
  ``GitStatusExtResponse`` was a hand-written rewrap).
* **transparent newtype** — :class:`WorktreeCreateSyncReq` wraps
  :class:`CreateWorktreeRequest` with ``#[serde(transparent)]``, so the wire
  form is byte-identical to the inner struct (no wrapper key). In pydantic this
  is a subclass that inherits every field and only re-points the ``METHOD`` /
  ``Response`` ClassVars.
* **non-transparent ``{inner: …}`` wrapper** —
  :class:`CreateWorktreeFromWorktreeSyncReq` deliberately does **not** derive
  ``transparent``, so its wire form keeps the ``{"inner": {…}}`` wrapper
  (snake_case outer key, camelCase inner struct). This is the explicit
  counterpart to the transparent newtype above.
* **custom ``default`` function** — ``copy_mode: WorktreeCopyMode`` carries
  ``#[serde(default = "default_copy_mode")]`` (defaults to ``Dirty``). This is
  the enum analogue of R73's ``default_true`` bool default.
* **mixed skip matrix (recurring)** — several structs combine always-emitted
  bool / required fields with ``skip_serializing_if = "Option::is_none"``
  fields (:attr:`RemoveWorktreeResponse.resolved_path`, all three Options in
  :class:`CreateWorktreeFromWorktreeResponse`, ``sourceGitRoot`` in every
  :data:`CreateWorktreeResponse` variant), reproduced with per-class wrap
  ``model_serializer`` mixins that pop only the skipping keys.

Three lowercase ``#[serde(rename_all = "lowercase")]`` enums with
``#[default]`` (:class:`WorktreeType` / :class:`WorktreeCopyMode` /
:class:`ApplyMode`) each expose a ``default()`` classmethod mirroring Rust's
``Default`` derive.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal

from pydantic import ConfigDict, Field, model_serializer
from pydantic.alias_generators import to_camel

from minimax_code.workspace_types._wire import WireModel
from minimax_code.workspace_types.rpc.git import ChangeType, GitFileChange

__all__ = [
    # enums (lowercase rename_all + #[default])
    "WorktreeType",
    "WorktreeCopyMode",
    "ApplyMode",
    # summaries
    "DirtyStateSummary",
    "CopiedChangesSummary",
    # create
    "CreateWorktreeRequest",
    "WorktreeCreateSyncReq",
    "CreateWorktreeResponse",
    "CreateWorktreeResponseCreating",
    "CreateWorktreeResponseExists",
    # remove
    "RemoveWorktreeRequest",
    "RemoveWorktreeResponse",
    # create-from-worktree
    "CreateWorktreeFromWorktreeResponse",
    "CreateWorktreeFromWorktreeRequestWire",
    "CreateWorktreeFromWorktreeSyncReq",
    "PrepareWorktreeFromWorktreeResponse",
    # apply
    "ApplyWorktreeRequest",
    "FileConflict",
    "ApplyWorktreeResponse",
    "ApplyWorktreeResponseSuccess",
    "ApplyWorktreeResponseConflicts",
    # worktree_* admin
    "WorktreeShowReq",
    "WorktreeGcReq",
    "WorktreeListReq",
    "WorktreeDbRebuildReq",
    "WorktreeDbPathReq",
    "WorktreeDbPathResponse",
    "WorktreeDbStatsReq",
]

_CAMEL = ConfigDict(populate_by_name=True, alias_generator=to_camel)


# === Enums (lowercase rename_all + #[default]) ===


class WorktreeType(StrEnum):
    """Worktree creation strategy (lowercase wire, default ``Linked``).

    Mirrors ``xai_fast_worktree::CreationMode`` with config-friendly lowercase
    strings. ``#[default] Linked`` → :meth:`default` returns ``LINKED``.
    """

    LINKED = "linked"
    STANDALONE = "standalone"
    GIT = "git"

    @classmethod
    def default(cls) -> WorktreeType:
        return cls.LINKED


class WorktreeCopyMode(StrEnum):
    """Copy mode for worktree creation (lowercase wire, default ``Dirty``)."""

    CLEAN = "clean"
    DIRTY = "dirty"

    @classmethod
    def default(cls) -> WorktreeCopyMode:
        return cls.DIRTY


class ApplyMode(StrEnum):
    """Apply strategy (lowercase wire, default ``Overwrite``)."""

    OVERWRITE = "overwrite"
    MERGE = "merge"

    @classmethod
    def default(cls) -> ApplyMode:
        return cls.OVERWRITE


def _default_copy_mode() -> WorktreeCopyMode:
    """Mirror Rust ``default_copy_mode()`` → ``WorktreeCopyMode::Dirty``."""
    return WorktreeCopyMode.DIRTY


# === Summary structs ===


class DirtyStateSummary(WireModel):
    """Source worktree dirty-state summary (camelCase, derives ``Default``)."""

    model_config = _CAMEL
    staged_count: int
    modified_count: int
    deleted_count: int
    untracked_count: int
    has_partially_staged: bool
    skipped_dirs: list[str]


class CopiedChangesSummary(WireModel):
    """Changes copied to the new worktree (camelCase, derives ``Default``)."""

    model_config = _CAMEL
    staged_copied: int
    modified_copied: int
    untracked_copied: int
    deletions_applied: int
    warnings: list[str]


# === Create worktree ===


class CreateWorktreeRequest(WireModel):
    """``workspace.create_worktree`` request (camelCase).

    Two required string fields (``session_id``, ``source_path``); the remaining
    fields all carry ``#[serde(default)]`` with **no** ``skip_serializing_if``,
    so ``Option`` fields emit ``null`` when absent and the empty
    ``ignored_skip_patterns`` vec emits ``[]``. ``copy_mode`` defaults to
    ``Dirty`` via the ``default_copy_mode`` function (R73's ``default_true``
    pattern applied to an enum).
    """

    model_config = _CAMEL
    session_id: str
    source_path: str
    worktree_path: str | None = None
    copy_mode: WorktreeCopyMode = Field(default_factory=_default_copy_mode)
    git_ref: str | None = None
    copy_ignored_in_background: bool = False
    ignored_skip_patterns: list[str] = Field(default_factory=list)
    worktree_type: WorktreeType | None = None
    label: str | None = None

    METHOD: ClassVar[str] = "workspace.create_worktree"
    Response: ClassVar = Any


class WorktreeCreateSyncReq(CreateWorktreeRequest):
    """``workspace.worktree_create_sync`` — transparent newtype wrapper.

    ``#[serde(transparent)]`` over :class:`CreateWorktreeRequest`: the wire form
    is byte-identical to the inner struct (no ``"inner"`` wrapper key).
    Subclassing inherits every field + the camelCase ``model_config``, only
    re-pointing the ``METHOD`` / ``Response`` ClassVars. Contrast with
    :class:`CreateWorktreeFromWorktreeSyncReq` which keeps its
    ``{"inner": …}`` wrapper.
    """

    METHOD: ClassVar[str] = "workspace.worktree_create_sync"
    Response: ClassVar = Any


class CreateWorktreeResponseCreating(WireModel):
    """:data:`CreateWorktreeResponse` ``creating`` variant (tag = ``"creating"``).

    ``sourceGitRoot`` carries ``skip_serializing_if = "Option::is_none"`` so it
    is omitted when ``None``; the wrap serializer pops it.
    """

    model_config = ConfigDict(populate_by_name=True)
    status: Literal["creating"] = "creating"
    session_id: str = Field(alias="sessionId")
    worktree_path: str = Field(alias="worktreePath")
    source_git_root: str | None = Field(default=None, alias="sourceGitRoot")

    @model_serializer(mode="wrap")
    def _omit(self, handler):  # noqa: ANN001, ANN202
        raw = handler(self)
        if raw.get("sourceGitRoot") is None:
            raw.pop("sourceGitRoot", None)
        return raw


class CreateWorktreeResponseExists(WireModel):
    """:data:`CreateWorktreeResponse` ``exists`` variant (tag = ``"exists"``)."""

    model_config = ConfigDict(populate_by_name=True)
    status: Literal["exists"] = "exists"
    session_id: str = Field(alias="sessionId")
    worktree_path: str = Field(alias="worktreePath")
    commit: str
    source_git_root: str | None = Field(default=None, alias="sourceGitRoot")

    @model_serializer(mode="wrap")
    def _omit(self, handler):  # noqa: ANN001, ANN202
        raw = handler(self)
        if raw.get("sourceGitRoot") is None:
            raw.pop("sourceGitRoot", None)
        return raw


#: :data:`CreateWorktreeResponse` — internally tagged union
#: (``#[serde(tag = "status")]``). Each variant flattens its fields under a
#: ``status`` discriminator key.
CreateWorktreeResponse = Annotated[
    CreateWorktreeResponseCreating | CreateWorktreeResponseExists,
    Field(discriminator="status"),
]


# === Remove worktree ===


class RemoveWorktreeRequest(WireModel):
    """``workspace.remove_worktree`` request (camelCase, Option fields keep null)."""

    model_config = _CAMEL
    worktree_path: str | None = None
    id_or_path: str | None = None
    force: bool = False
    dry_run: bool = False

    METHOD: ClassVar[str] = "workspace.remove_worktree"
    Response: ClassVar = Any


class RemoveWorktreeResponse(WireModel):
    """``workspace.remove_worktree`` response (camelCase).

    ``removed`` is always emitted (required bool); ``resolved_path`` carries
    ``skip_serializing_if = "Option::is_none"`` → omitted when ``None``.
    """

    model_config = _CAMEL
    removed: bool
    resolved_path: str | None = None

    @model_serializer(mode="wrap")
    def _omit(self, handler):  # noqa: ANN001, ANN202
        raw = handler(self)
        if raw.get("resolvedPath") is None:
            raw.pop("resolvedPath", None)
        return raw


# === Create worktree from worktree ===


class CreateWorktreeFromWorktreeResponse(WireModel):
    """Response from creating a worktree from another worktree (camelCase).

    Three required fields (``status``, ``new_session_id``, ``worktree_path``)
    and three ``Option`` fields each with ``skip_serializing_if =
    "Option::is_none"`` → all three omitted when ``None``. The wrap serializer
    pops the three camelCase keys when ``None``.
    """

    model_config = _CAMEL
    status: str
    new_session_id: str
    worktree_path: str
    commit: str | None = None
    copied_changes: CopiedChangesSummary | None = None
    source_git_root: str | None = None

    @model_serializer(mode="wrap")
    def _omit(self, handler):  # noqa: ANN001, ANN202
        raw = handler(self)
        for key in ("commit", "copiedChanges", "sourceGitRoot"):
            if raw.get(key) is None:
                raw.pop(key, None)
        return raw


class CreateWorktreeFromWorktreeRequestWire(WireModel):
    """Wire mirror of the heavy crate's ``CreateWorktreeFromWorktreeRequest``.

    Drops the two ``#[serde(skip)]`` runtime-only fields
    (``cancellation_token``, ``resolved_dest_path``) so the wire shape is
    byte-identical (those fields are already absent from the wire). camelCase;
    ``copy_mode`` defaults to ``Dirty``; the three ``Option`` fields keep
    ``null`` (no ``skip_serializing_if``).
    """

    model_config = _CAMEL
    source_worktree_path: str
    new_session_id: str
    copy_mode: WorktreeCopyMode = Field(default_factory=_default_copy_mode)
    git_ref: str | None = None
    worktree_type: WorktreeType | None = None
    label: str | None = None


class CreateWorktreeFromWorktreeSyncReq(WireModel):
    """``workspace.worktree_create_from_worktree_sync`` — non-transparent wrapper.

    Unlike :class:`WorktreeCreateSyncReq` this is **not** ``transparent``, so
    the wire form keeps the ``{"inner": {…}}`` wrapper (snake_case outer key).
    The inner struct is camelCase.
    """

    inner: CreateWorktreeFromWorktreeRequestWire

    METHOD: ClassVar[str] = "workspace.worktree_create_from_worktree_sync"
    Response: ClassVar[type] = CreateWorktreeFromWorktreeResponse


class PrepareWorktreeFromWorktreeResponse(WireModel):
    """Serializable ``PrepareWorktreeResult`` (snake_case, not a WorkspaceRpc).

    ``response`` is raw JSON (a serialized :data:`CreateWorktreeResponse` on
    success). Both ``Option`` fields keep ``null`` (no ``skip_serializing_if``).
    """

    spawn_task: bool
    response: Any | None = None
    error: str | None = None


# === Apply worktree ===


class ApplyWorktreeRequest(WireModel):
    """``workspace.apply_worktree`` request (camelCase, ``mode`` defaults Overwrite)."""

    model_config = _CAMEL
    session_id: str
    worktree_path: str
    mode: ApplyMode = Field(default_factory=ApplyMode.default)

    METHOD: ClassVar[str] = "workspace.apply_worktree"
    Response: ClassVar = Any


class FileConflict(WireModel):
    """A file conflict during ``apply_worktree`` (camelCase).

    Reuses R74's :class:`~minimax_code.workspace_types.rpc.git.ChangeType` with
    the same ``rename = "type"`` per-field override. ``base`` / ``ours`` /
    ``theirs`` carry no ``skip_serializing_if`` → ``null`` is kept.
    """

    model_config = _CAMEL
    path: str
    change_type: ChangeType = Field(alias="type")
    base: str | None = None
    ours: str | None = None
    theirs: str | None = None


class ApplyWorktreeResponseSuccess(WireModel):
    """:data:`ApplyWorktreeResponse` ``success`` variant (tag = ``"success"``)."""

    model_config = ConfigDict(populate_by_name=True)
    status: Literal["success"] = "success"
    files: list[GitFileChange]
    git_root: str = Field(alias="gitRoot")


class ApplyWorktreeResponseConflicts(WireModel):
    """:data:`ApplyWorktreeResponse` ``conflicts`` variant (tag = ``"conflicts"``)."""

    model_config = ConfigDict(populate_by_name=True)
    status: Literal["conflicts"] = "conflicts"
    files: list[GitFileChange]
    conflicts: list[FileConflict]


#: :data:`ApplyWorktreeResponse` — internally tagged union
#: (``#[serde(tag = "status")]``). Reuses R74's ``GitFileChange`` and this
#: file's :class:`FileConflict`.
ApplyWorktreeResponse = Annotated[
    ApplyWorktreeResponseSuccess | ApplyWorktreeResponseConflicts,
    Field(discriminator="status"),
]


# === worktree_* admin RPCs (snake_case) ===


class WorktreeShowReq(WireModel):
    """``workspace.worktree_show`` (snake_case)."""

    id_or_path: str

    METHOD: ClassVar[str] = "workspace.worktree_show"
    Response: ClassVar = Any


class WorktreeGcReq(WireModel):
    """``workspace.worktree_gc`` (snake_case).

    ``max_age_secs`` has **no** ``#[serde(default)]`` → the key is required on
    deserialization (value may be ``null``); ``dry_run`` / ``force`` default
    ``False``.
    """

    dry_run: bool = False
    max_age_secs: int | None
    force: bool = False

    METHOD: ClassVar[str] = "workspace.worktree_gc"
    Response: ClassVar = Any


class WorktreeListReq(WireModel):
    """``workspace.worktree_list`` (snake_case, derives ``Default``).

    ``types`` carries ``#[serde(default, rename = "type")]`` → wire key is
    ``"type"`` (not ``"types"``), defaults to ``[]``, and **no**
    ``skip_serializing_if`` so the empty vec emits ``"type": []``.
    """

    repo: str | None = None
    types: list[str] = Field(default_factory=list, alias="type")
    include_all: bool = False

    METHOD: ClassVar[str] = "workspace.worktree_list"
    Response: ClassVar = Any


class WorktreeDbRebuildReq(WireModel):
    """``workspace.worktree_db_rebuild`` (empty struct)."""

    METHOD: ClassVar[str] = "workspace.worktree_db_rebuild"
    Response: ClassVar = Any


class WorktreeDbPathResponse(WireModel):
    """``workspace.worktree_db_path`` response (snake_case, ``path`` keeps null)."""

    path: str | None = None


class WorktreeDbPathReq(WireModel):
    """``workspace.worktree_db_path`` (empty struct).

    ``Response = WorktreeDbPathResponse`` (defined above so the ClassVar can
    reference it).
    """

    METHOD: ClassVar[str] = "workspace.worktree_db_path"
    Response: ClassVar[type] = WorktreeDbPathResponse


class WorktreeDbStatsReq(WireModel):
    """``workspace.worktree_db_stats`` (empty struct, derives ``Default``)."""

    METHOD: ClassVar[str] = "workspace.worktree_db_stats"
    Response: ClassVar = Any
