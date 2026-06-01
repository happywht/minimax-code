"""Storage DAOs — one per entity.

The DAO layer is the only surface area the rest of the agent should
touch for persistent state. Each DAO exposes typed CRUD plus the
list/filter operations that the IPC layer needs.

Conventions
-----------
* Functions accept and return :class:`dict` row representations so
  that we can hand them straight to JSON-RPC without further
  serialization work.
* All write functions accept a ``Database`` (or
  :class:`AsyncDatabase`) and run inside a transaction — callers
  should *not* wrap them in an outer transaction.
* The DAO module deliberately does *not* re-export the database
  primitives; use ``from minimax_code.storage.db import Database``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def row_to_dict(row: Any) -> dict[str, Any] | None:
    """Convert a ``sqlite3.Row`` (or ``aiosqlite.Row``) to a plain dict.

    Returns ``None`` for a falsy row. The conversion is intentionally
    minimal — the caller is responsible for any JSON deserialization
    of columns that are stored as JSON text.
    """
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def rows_to_dicts(rows: Iterable[Any]) -> list[dict[str, Any]]:
    """Bulk version of :func:`row_to_dict`."""
    return [row_to_dict(r) for r in rows if r is not None]


def dumps_json(value: Any) -> str | None:
    """Encode ``value`` as a JSON string for storage.

    Returns ``None`` when ``value`` is ``None`` — we don't store the
    string ``"null"``.
    """
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def loads_json(value: Any) -> Any:
    """Decode a column that may be ``None`` or a JSON string.

    Returns the decoded value, or ``None`` if the column was NULL.
    Raises the underlying :class:`json.JSONDecodeError` on bad data
    so the caller can decide how to surface it.
    """
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    return json.loads(value)


def now_iso() -> str:
    """UTC ISO-8601 timestamp, second precision.

    Shared between DAOs that need to populate ``created_at`` /
    ``updated_at`` columns.
    """
    from ..db import _now_iso  # local re-export

    return _now_iso()


# ---------------------------------------------------------------------------
# Pagination / filter support
# ---------------------------------------------------------------------------


def apply_pagination(
    sql: str,
    params: list[Any],
    *,
    limit: int | None = None,
    offset: int | None = None,
) -> tuple[str, list[Any]]:
    """Append ``LIMIT ? OFFSET ?`` to ``sql`` when given.

    Returns the modified SQL and the updated params list. A negative
    or zero ``limit`` is treated as "no limit".
    """
    if limit is None or limit <= 0:
        return sql, params
    sql = f"{sql} LIMIT ? OFFSET ?"
    params.extend([int(limit), int(offset or 0)])
    return sql, params


def parse_order_by(
    order_by: str | None,
    allowed: Sequence[str],
    default: str = "created_at DESC",
) -> str:
    """Validate a caller-supplied ORDER BY expression.

    ``order_by`` is a single column name or ``"col DIR"``. The column
    must appear in ``allowed`` (case-insensitive); direction must be
    ``ASC`` or ``DESC``. On any failure we return ``default``.
    """
    if not order_by:
        return default
    parts = order_by.strip().split()
    if len(parts) == 1:
        col, direction = parts[0], "ASC"
    elif len(parts) == 2:
        col, direction = parts
    else:
        return default
    if col.lower() not in {a.lower() for a in allowed}:
        return default
    direction_up = direction.upper()
    if direction_up not in {"ASC", "DESC"}:
        direction_up = "ASC"
    return f"{col} {direction_up}"


__all__ = [
    "apply_pagination",
    "dumps_json",
    "loads_json",
    "now_iso",
    "parse_order_by",
    "row_to_dict",
    "rows_to_dicts",
]
