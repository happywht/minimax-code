"""Shared utilities for IPC handler modules.

Centralises the ``HandlerError`` exception and the ``check_params``
validation helper so that every ``handlers_*.py`` can import them
instead of re-defining identical copies.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..workspace_ctx import _project_root
from .protocol import INVALID_PARAMS

# ---------------------------------------------------------------------------
# HandlerError — unified exception for all handlers
# ---------------------------------------------------------------------------

class HandlerError(Exception):
    """Internal sentinel — handlers raise it with a JSON-RPC code.

    The :class:`IPCServer` catches this at the dispatch boundary and
    converts it into a proper JSON-RPC error response.
    """

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data


# ---------------------------------------------------------------------------
# check_params — parameter shape validation
# ---------------------------------------------------------------------------

def check_params(params: Any, *, expected_keys: set[str]) -> None:
    """Validate the JSON-RPC *params* shape.

    Raises :class:`HandlerError` with ``INVALID_PARAMS`` when *params*
    is ``None``, not a ``dict``, or missing any of *expected_keys*.
    """
    if not expected_keys:
        return
    if params is None or not isinstance(params, dict):
        raise HandlerError(
            INVALID_PARAMS,
            "params must be a JSON object with the required keys",
        )
    missing = expected_keys - set(params.keys())
    if missing:
        raise HandlerError(
            INVALID_PARAMS,
            f"missing required param(s): {sorted(missing)}",
        )


# ---------------------------------------------------------------------------
# Per-project root gate (v1.3.0 strict isolation)
# ---------------------------------------------------------------------------

async def project_root_from_params(params: Any) -> Path | None:
    """The project's root when *params* carries a rooted ``project_id``.

    Containment gate shared by the git / patch / terminal handlers:
    returns ``None`` when *params* has no ``project_id``, the project is
    unknown, or its ``root_path`` is unset or missing on disk — callers
    treat ``None`` as "no project scope, keep legacy behaviour", so
    existing callers without a project id are unaffected.
    """
    if not isinstance(params, dict):
        return None
    project_id = params.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        return None
    return await _project_root(project_id)


def ensure_cwd_within_root(cwd: str, root: Path) -> str:
    """Validate that *cwd* resolves inside *root*; return the resolved path.

    Raises :class:`HandlerError` with ``INVALID_PARAMS`` when the resolved
    path escapes the project root (strict isolation: a project-scoped
    call may not touch directories outside its root). Relative *cwd*
    values are interpreted against *root*.
    """
    resolved = Path(cwd).expanduser()
    if not resolved.is_absolute():
        resolved = root / resolved
    resolved = resolved.resolve()
    if resolved != root and root not in resolved.parents:
        raise HandlerError(
            INVALID_PARAMS,
            f"'cwd' {cwd!r} is outside the project root {str(root)!r}",
            {"cwd": cwd, "project_root": str(root)},
        )
    return str(resolved)
