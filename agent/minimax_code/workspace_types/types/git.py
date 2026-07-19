"""Minimal git/VCS shapes (R67).

Fusion of grok-build's ``xai-grok-workspace-types::types::git`` — the
VCS discriminator plus the status / diff / branch / metadata structs
emitted as ``GitStatus`` / ``GitDiff`` / ``GitBranchInfo`` /
``GitMetadata`` chunks. All structs are ``#[serde(default)]`` + derive
``Default``, mirrored here by ``WireModel.default()``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from minimax_code.workspace_types._wire import WireModel

__all__ = [
    "VcsKind",
    "GitStatusOpts",
    "GitStatus",
    "GitDiffArgs",
    "GitDiff",
    "GitBranchInfo",
    "GitMetadata",
]


class VcsKind(StrEnum):
    """Version-control backend (``#[serde(default)] Git``)."""

    Git = "git"
    Jj = "jj"

    @classmethod
    def default(cls) -> VcsKind:
        """Rust ``#[default] Git``."""
        return cls.Git


class GitStatusOpts(WireModel):
    """Options shaping a ``git status`` query."""

    include_untracked: bool = False
    include_ignored: bool = False


class GitStatus(WireModel):
    """Result of ``git status`` (emitted as ``GitStatus`` chunk)."""

    branch: str = ""
    head_commit: str = ""
    root: str = ""
    staged: list[str] = Field(default_factory=list)
    unstaged: list[str] = Field(default_factory=list)
    untracked: list[str] = Field(default_factory=list)
    clean: bool = False
    vcs: VcsKind = Field(default=VcsKind.Git)


class GitDiffArgs(WireModel):
    """Options shaping a ``git diff`` query."""

    range: str | None = None
    paths: list[str] = Field(default_factory=list)
    staged: bool = False


class GitDiff(WireModel):
    """Result of ``git diff`` (emitted as ``GitDiff`` chunk)."""

    patch: str = ""
    files: list[str] = Field(default_factory=list)


class GitBranchInfo(WireModel):
    """Branch listing (emitted as ``GitBranchInfo`` chunk)."""

    current: str | None = None
    local: list[str] = Field(default_factory=list)
    upstream: str | None = None


class GitMetadata(WireModel):
    """Repository metadata (emitted as ``GitMetadata`` chunk)."""

    origin_url: str | None = None
    root: str = ""
    default_branch: str | None = None
    vcs: VcsKind = Field(default=VcsKind.Git)
