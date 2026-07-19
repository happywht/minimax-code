"""Permission request / decision shapes (R67).

Fusion of grok-build's ``xai-grok-workspace-types::types::permission``.

* :class:`PermissionRequest` — the struct emitted as the body of a
  ``NeedPermission`` chunk. ``input_json`` carries the tool's proposed
  input as a JSON string; ``destructive`` flags irreversible operations.
* :class:`PermissionDecision` — adjacent-tagged enum of the user's
  verdict. ``AllowOnce`` / ``AllowSession`` / ``AllowProject`` are unit
  variants; ``Deny { reason }`` carries an optional reason (default
  empty string).
"""

from __future__ import annotations

from minimax_code.workspace_types._tagged import AdjacentTagged
from minimax_code.workspace_types._wire import WireModel

__all__ = ["PermissionRequest", "PermissionDecision"]


class PermissionRequest(WireModel):
    """Permission prompt emitted via ``NeedPermission`` chunks.

    ``input_json`` is a JSON-encoded string (the tool's proposed input),
    not a parsed object — the wire stays a string so the receiver can
    log/redact it uniformly.
    """

    tool_name: str = ""
    summary: str = ""
    input_json: str = ""
    destructive: bool = False


class PermissionDecision(AdjacentTagged):
    """User's verdict on a permission request (adjacent-tagged).

    Wire shapes::

        {"type": "allow_once",    "data": null}
        {"type": "allow_session", "data": null}
        {"type": "allow_project", "data": null}
        {"type": "deny",          "data": {"reason": "<text>"}}

    The three ``Allow*`` variants are unit (``data`` is ``null``);
    ``Deny`` carries a ``reason`` (default ``""`` per
    ``#[serde(default)]``).
    """

    _VARIANTS = ("allow_once", "allow_session", "allow_project", "deny")

    @classmethod
    def allow_once(cls) -> PermissionDecision:
        """Allow this one invocation only."""
        return cls("allow_once", None)

    @classmethod
    def allow_session(cls) -> PermissionDecision:
        """Allow for the rest of this session."""
        return cls("allow_session", None)

    @classmethod
    def allow_project(cls) -> PermissionDecision:
        """Allow persistently for this project."""
        return cls("allow_project", None)

    @classmethod
    def deny(cls, reason: str = "") -> PermissionDecision:
        """Deny, with an optional ``reason`` (default empty)."""
        return cls("deny", {"reason": reason})
