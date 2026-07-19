"""Add a ``reasoning_effort`` column to ``model_prefs`` (R61).

This is the *write-side* storage seam of the reasoning-effort pipeline.
The user's chosen reasoning-effort override (a lowercase wire token such
as ``"low"`` / ``"medium"`` / ``"high"`` / ``"xhigh"``) is persisted
alongside the model preference, so a later turn can re-apply it without
the caller re-sending it every request. The companion writer
(:meth:`minimax_code.storage.dao.model_prefs.ModelPrefsDAO.
set_reasoning_effort`) and the ``model.set_reasoning_effort`` IPC handler
consume this column.

The column is nullable and carries **no default**: a ``NULL`` means "no
effort override — use the model's own default" (the pre-R61 behaviour for
every existing row, hence backward compatible — the read side treats a
missing value as ``None``). Every pre-014 row picks up ``NULL``
automatically because SQLite back-fills the new column on ``ALTER TABLE
ADD COLUMN``.

Symmetric to R58's read-side seam (catalog meta → ``model.list``
enrich): R58 surfaces *what a model supports*; R61 persists *what the
user picked*. Together they let the UI show both capability and choice.
The runtime effect — forwarding the stored effort into the LLM call via
``rebuild_subagent_llm`` — is a separate concern, deferred to a later
round; this migration only adds the storage surface.
"""

from __future__ import annotations

from typing import Any

VERSION = 14

DDL = r"""
ALTER TABLE model_prefs ADD COLUMN reasoning_effort TEXT;
"""


def run(conn: Any) -> None:
    conn.executescript(DDL)


__all__ = ["DDL", "VERSION", "run"]
