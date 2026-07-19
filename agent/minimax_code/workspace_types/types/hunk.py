"""Diff-hunk tracking shapes (R67).

Fusion of grok-build's ``xai-grok-workspace-types::types::hunk``.

* :class:`Hunk` — one tracked diff hunk (emitted as the body of a
  ``Hunks`` chunk). Line counts and the start line are ``u32``.
* :class:`HunkAction` — adjacent-tagged enum of per-hunk
  accept/reject/revert verdicts; each variant wraps the :class:`HunkId`
  it targets (newtype variant → ``data`` is the bare id string).
"""

from __future__ import annotations

from minimax_code.workspace_types._tagged import AdjacentTagged
from minimax_code.workspace_types._wire import WireModel
from minimax_code.workspace_types.identity import HunkId

__all__ = ["Hunk", "HunkAction"]


class Hunk(WireModel):
    """One tracked diff hunk.

    ``added`` / ``removed`` / ``start_line`` are ``u32`` (Python ``int``);
    ``id`` is the opaque :class:`HunkId`; ``summary`` is a short
    human-readable label.
    """

    id: HunkId
    path: str = ""
    added: int = 0
    removed: int = 0
    start_line: int = 0
    summary: str = ""

    @classmethod
    def default(cls) -> Hunk:
        return cls(id=HunkId(""))


class HunkAction(AdjacentTagged):
    """Per-hunk verdict (adjacent-tagged).

    Each variant wraps the :class:`HunkId` it targets — a newtype-style
    variant, so ``data`` is the bare id string.

    Wire shapes::

        {"type": "accept", "data": "<hunk-id>"}
        {"type": "reject", "data": "<hunk-id>"}
        {"type": "revert", "data": "<hunk-id>"}
    """

    _VARIANTS = ("accept", "reject", "revert")

    @classmethod
    def accept(cls, hunk_id: HunkId | str) -> HunkAction:
        """Accept the hunk's changes."""
        return cls("accept", str(hunk_id))

    @classmethod
    def reject(cls, hunk_id: HunkId | str) -> HunkAction:
        """Reject the hunk's changes."""
        return cls("reject", str(hunk_id))

    @classmethod
    def revert(cls, hunk_id: HunkId | str) -> HunkAction:
        """Revert an already-accepted hunk."""
        return cls("revert", str(hunk_id))
