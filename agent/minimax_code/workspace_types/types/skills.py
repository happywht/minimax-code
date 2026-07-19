"""Skill discovery shape (R67).

Fusion of grok-build's ``xai-grok-workspace-types::types::skills``.

.. note::

   This ``source``-keyed :class:`SkillInfo` is **not** the wire shape of
   the ``workspace.discover_skills`` RPC — that lives in
   ``rpc::skills::SkillInfo`` (``scope``-keyed) and is deferred to a
   later round. This leaf struct surfaces discovered skills via
   ``OpsChunk::Skills`` and ``WorkspaceEvent::SkillsChanged``.
"""

from __future__ import annotations

from minimax_code.workspace_types._wire import WireModel

__all__ = ["SkillInfo"]


class SkillInfo(WireModel):
    """Discovered skill metadata.

    ``id`` is also the slash-command name; ``source`` is a free-form
    bucket label (``"global"`` / ``"workspace"`` / ``"server"`` /
    ``"bundled"``).
    """

    id: str
    display_name: str = ""
    description: str = ""
    path: str = ""
    source: str = ""

    @classmethod
    def default(cls) -> SkillInfo:
        return cls(id="")
