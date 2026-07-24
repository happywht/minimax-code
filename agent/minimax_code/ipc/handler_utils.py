"""Shared utilities for IPC handler modules.

Centralises the ``HandlerError`` exception and the ``check_params``
validation helper so that every ``handlers_*.py`` can import them
instead of re-defining identical copies.
"""

from __future__ import annotations

from typing import Any

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
