"""Lift legacy sub-agent iteration budgets from 8 to 100 (P0-3).

Migration 010 created ``agents.max_iterations`` with ``DEFAULT 8`` — a
budget sub-agents routinely exhaust mid-deliverable (the run hits the
iteration safety valve before ``report_completion`` lands). Production
INSERTs have since moved to :meth:`AgentDAO.upsert`, which writes the
current default (100) explicitly, but every row created while the old
paths were live still carries the 8.

This migration lifts those rows in place. It deliberately targets only
``max_iterations = 8`` — the legacy default — so seeded reviewers
(migration 011, explicit 10) and any user-tuned budget survive intact.
The table's column DEFAULT is *not* rebuilt: SQLite cannot ALTER a
DEFAULT without a full table rebuild, no production INSERT relies on
the column default any more (see ``AgentDAO.upsert``), and a rebuild
would risk the UNIQUE name index for zero behavioral gain.

Idempotent by construction: re-running matches zero rows once every
legacy 8 has been lifted (or was never there — fresh databases seed
nothing at 8 via this path).

v1.6.1 — optimization_v1 P0-3.
"""

from __future__ import annotations

from typing import Any

VERSION = 29


def run(conn: Any) -> None:
    """Lift legacy ``agents.max_iterations = 8`` rows to 100."""
    conn.execute(
        "UPDATE agents SET max_iterations = 100 WHERE max_iterations = 8"
    )


__all__ = ["VERSION", "run"]
