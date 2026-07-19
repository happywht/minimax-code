"""Session file-state / rewind RPCs (R68).

Fusion of grok's ``xai-grok-workspace-types::rpc::session`` — the
``workspace.begin_prompt`` / ``workspace.end_prompt`` /
``workspace.rewind_to`` request structs plus the rewind response and its
conflict shapes.

Response types are declared before the request structs so each request's
``Response`` ClassVar can name its response type directly (Rust uses an
associated ``type Response``; Python resolves a ClassVar default at
class-body evaluation time, so definition order matters).
"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from minimax_code.workspace_types._wire import WireModel

__all__ = [
    "BeginPromptReq",
    "EndPromptReq",
    "RewindToReq",
    "ConflictType",
    "FileRewindConflict",
    "FileRewindResponse",
]


class ConflictType(StrEnum):
    """Kind of external modification detected during a file rewind.

    ``#[serde(rename_all = "snake_case")]`` → lowercase snake values.
    """

    DELETED_EXTERNALLY = "deleted_externally"
    CREATED_EXTERNALLY = "created_externally"
    MODIFIED_EXTERNALLY = "modified_externally"


class FileRewindConflict(WireModel):
    """A single conflict detected during file rewind."""

    path: str
    conflict_type: ConflictType


class FileRewindResponse(WireModel):
    """Response returned by the ``workspace.rewind_to`` RPC.

    ``success`` / ``target_prompt_index`` are required (Rust has no
    ``#[serde(default)]``); the ``Vec`` fields are likewise required on
    the wire; ``error`` is ``Option<String>`` → ``str | None`` defaulting
    to ``None`` (a missing ``error`` key deserialises to ``None``).
    """

    success: bool
    target_prompt_index: int
    reverted_files: list[str]
    clean_files: list[str]
    conflicts: list[FileRewindConflict]
    error: str | None = None


class BeginPromptReq(WireModel):
    """``workspace.begin_prompt`` — begin tracking file state for a prompt.

    ``Response = ()`` (unit) — the server acknowledges without a payload.
    """

    METHOD: ClassVar[str] = "workspace.begin_prompt"
    Response: ClassVar[type] = type(None)

    session_id: str
    prompt_index: int


class EndPromptReq(WireModel):
    """``workspace.end_prompt`` — end tracking (captures after-snapshots).

    ``Response = ()`` (unit).
    """

    METHOD: ClassVar[str] = "workspace.end_prompt"
    Response: ClassVar[type] = type(None)

    session_id: str
    prompt_index: int


class RewindToReq(WireModel):
    """``workspace.rewind_to`` — rewind files to before a prompt index.

    ``Response = FileRewindResponse``.
    """

    METHOD: ClassVar[str] = "workspace.rewind_to"
    Response: ClassVar[type] = FileRewindResponse

    session_id: str
    target_prompt_index: int
