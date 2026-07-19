"""App-deployment error-code vocabulary (R69, Err side of the envelope).

Fusion of grok's ``xai-grok-workspace-types::rpc::deploy`` — the
:class:`DeployError` enum carried inside an :class:`RpcEnvelope` error arm's
``code`` field. This is the first envelope consumer on the **Err side**:
the 15 deploy-specific codes are a sub-vocabulary of the open-ended
``RpcError.code`` string, with an explicit member-name → wire-code mapping
(not a derived one), so :class:`DeployError` is a plain :class:`enum.Enum`
whose ``value`` *is* the wire code — not a :class:`enum.StrEnum`.

``wire_code()`` / ``from_wire_code()`` round-trip the mapping;
``DeployError.ALL`` enumerates every kind for exhaustive iteration.
"""

from __future__ import annotations

from enum import Enum

__all__ = ["DeployError"]


class DeployError(Enum):
    """A deployment error kind, carried as ``RpcError.code`` on the wire.

    The member name (e.g. ``URL_CONFLICT``) and the wire discriminant
    (``"deploy_url_conflict"``) are **not** derivable from each other — the
    source has an explicit ``wire_code()`` match. We therefore store the wire
    code as the enum ``value`` (so ``wire_code()`` is ``self.value``) and keep
    the explicit methods for API parity with the source. A plain ``Enum``
    (value is a ``str``) is used rather than ``StrEnum`` because the mapping
    is explicit, not name-derived, and ``StrEnum`` would imply ``str(value)``
    is the canonical form.
    """

    URL_CONFLICT = "deploy_url_conflict"
    URL_MODERATION = "deploy_url_moderation"
    IDEMPOTENCY_CONFLICT = "deploy_idempotency_conflict"
    NOT_FOUND = "deploy_not_found"
    PERMISSION_DENIED = "deploy_permission_denied"
    DEPLOYMENT_NOT_IN_BUILDING_STATE = "deploy_not_in_building_state"
    UNSUPPORTED_PROJECT_TYPE = "deploy_unsupported_project_type"
    PROVIDER_UNAVAILABLE = "deploy_provider_unavailable"
    INTERNAL = "deploy_internal"
    UNAUTHENTICATED = "deploy_unauthenticated"
    INVALID_ARGUMENT = "deploy_invalid_argument"
    RESOURCE_EXHAUSTED = "deploy_resource_exhausted"
    DEADLINE_EXCEEDED = "deploy_deadline_exceeded"
    ALREADY_EXISTS = "deploy_already_exists"
    FAILED_PRECONDITION = "deploy_failed_precondition"

    def wire_code(self) -> str:
        """The ``RpcError.code`` discriminant for this kind."""
        return self.value

    @classmethod
    def from_wire_code(cls, code: str) -> DeployError | None:
        """Parse an ``RpcError.code`` back into a kind, or ``None`` if unknown.

        Mirrors the source's ``Option<Self>``: an unrelated code (e.g.
        ``"hub_error"``) returns ``None`` rather than raising.
        """
        try:
            return cls(code)
        except ValueError:
            return None


#: Every kind, for exhaustive iteration in tests — mirrors ``DeployError::ALL``.
#: Assigned after the class body because ``tuple(cls)`` needs the enum closed.
DeployError.ALL = tuple(DeployError)  # type: ignore[attr-defined]
