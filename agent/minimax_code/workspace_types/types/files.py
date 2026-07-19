"""``@file`` reference resolution shapes (R67).

Fusion of grok-build's ``xai-grok-workspace-types::types::files``.

* :class:`FileReference` — the raw ``@file`` token plus an optional
  pre-resolved absolute path.
* :class:`ResolvedFile` — the resolution result surfaced in
  ``OpsChunk::ResolvedFiles``. ``reference`` is the original reference
  **text** (a string), not a nested :class:`FileReference` — the wire
  flattens the two so the receiver gets the user's raw token directly.
"""

from __future__ import annotations

from minimax_code.workspace_types._wire import WireModel

__all__ = ["FileReference", "ResolvedFile"]


class FileReference(WireModel):
    """A reference (input) to be resolved by the ``@file`` provider.

    ``raw`` is the raw token (e.g. ``"@docs/AGENTS.md"``);
    ``absolute_path`` is an optional already-resolved absolute path.
    """

    raw: str
    absolute_path: str | None = None

    @classmethod
    def default(cls) -> FileReference:
        return cls(raw="")


class ResolvedFile(WireModel):
    """A resolved file returned in ``OpsChunk::ResolvedFiles``.

    ``reference`` is the original reference text (a string, not a nested
    struct); ``resolved`` is ``False`` on failure, in which case
    ``error`` carries the reason; ``preview`` is the optional first-N-bytes
    preview.
    """

    reference: str
    path: str = ""
    resolved: bool = False
    preview: str | None = None
    error: str | None = None

    @classmethod
    def default(cls) -> ResolvedFile:
        return cls(reference="")
