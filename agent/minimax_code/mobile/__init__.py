"""Mobile pairing — token-based PoC for connecting phones / tablets.

The full Phase 2 design will replace this with a proper TLS handshake
+ device-cert verification. For now the contract is intentionally
minimal:

* **Pair start** (operator side, called from the desktop app):
  ``generate_pairing_token()`` mints a 32-byte URL-safe secret and a
  10-minute expiry. The token is *not* persisted to the DB — losing
  it just means the operator has to click "pair new device" again.
* **Pair confirm** (device side, called once the device scans the QR
  code): ``confirm_pairing(token, device_id, name, public_key)``
  validates the token, deletes it from the in-memory cache, and
  writes a row to the ``mobile_devices`` table.

This file is intentionally a *process-local* manager. The token
cache is held in a module-level dict guarded by a
``threading.RLock`` — fine for a single-process agent, but if we
later run multiple replicas we'll need a Redis-style coordinator.

TODO(phase-2): replace the token-only flow with a real
``cryptography.x509`` mutual TLS handshake — the device presents
a cert signed by the agent's CA, the agent presents its own cert
back, and ``public_key`` becomes a verified certificate fingerprint
instead of an opaque blob.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: How long a freshly-minted pairing token stays valid. The QR code
#: typically gets scanned within 30-60 s, so 10 minutes leaves plenty
#: of headroom for the user to fumble with their phone.
DEFAULT_TOKEN_TTL_SECONDS: int = 600


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PairingError(Exception):
    """Raised by :class:`PairingManager` for invalid token / state issues.

    The IPC layer maps this to a JSON-RPC ``INVALID_PARAMS`` envelope
    so the frontend can show a meaningful error toast.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Token record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _TokenRecord:
    """Internal — a token plus its issuance time.

    Stored in the in-memory cache; the ``issued_at`` lets us lazily
    evict stale entries even if nobody ever calls
    :meth:`PairingManager.confirm_pairing`.
    """

    token: str
    issued_at: float
    expires_at: float
    # Optional hint from the operator: a friendly label for the
    # device they're pairing (e.g. "Living Room iPad"). Stored
    # alongside the token so the confirm step can echo it back
    # to the UI as a default ``name`` if the device doesn't send
    # its own.
    suggested_name: str | None = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class PairingManager:
    """Process-local pairing state for the mobile PoC.

    Construction is cheap; the in-memory token cache is empty.
    Pass a ``ttl_seconds`` override in tests to force expiry
    without sleeping for ten minutes.
    """

    def __init__(
        self,
        *,
        ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
        clock=time.time,
    ) -> None:
        self._ttl = int(ttl_seconds)
        self._clock = clock
        self._tokens: dict[str, _TokenRecord] = {}
        self._lock = threading.RLock()

    # -- token lifecycle ---------------------------------------------------

    def generate_pairing_token(
        self,
        *,
        suggested_name: str | None = None,
    ) -> dict[str, Any]:
        """Mint a new short-lived pairing token.

        Returns a dict ``{"token": ..., "expires_at": <iso>}`` ready
        to ship back to the frontend. The token is 32 random bytes
        URL-safe-encoded (``secrets.token_urlsafe(32)``) — about 43
        characters of base64, cryptographically unguessable.

        The token is *not* persisted to the DB on purpose: a pairing
        token is a one-shot secret, and a stale token in the DB
        would just be a permanent footgun if someone leaked it.
        """
        token = secrets.token_urlsafe(32)
        now = float(self._clock())
        expires_at = now + self._ttl
        record = _TokenRecord(
            token=token,
            issued_at=now,
            expires_at=expires_at,
            suggested_name=suggested_name or None,
        )
        with self._lock:
            self._evict_expired_locked()
            self._tokens[token] = record
        return {
            "token": token,
            "expires_at": _iso_from_epoch(expires_at),
        }

    def confirm_pairing(
        self,
        token: str,
        device_id: str,
        name: str,
        public_key: str,
    ) -> dict[str, Any]:
        """Validate ``token`` and write the device row to the DB.

        Returns the inserted device row (as a dict). Raises
        :class:`PairingError` on bad input — callers should map the
        error to ``INVALID_PARAMS`` so the frontend can react.

        Note: the caller is expected to pass a *DAO* in via the
        constructor (see :class:`PairingManagerWithDAO`). The base
        class is DAO-agnostic so it can be unit-tested without an
        ``AsyncDatabase``.
        """
        if not isinstance(token, str) or not token:
            raise PairingError("invalid_token", "token must be a non-empty string")
        if not isinstance(device_id, str) or not device_id.strip():
            raise PairingError("invalid_device_id", "device_id must be a non-empty string")
        if not isinstance(name, str) or not name.strip():
            raise PairingError("invalid_name", "name must be a non-empty string")
        if not isinstance(public_key, str) or not public_key.strip():
            raise PairingError("invalid_public_key", "public_key must be a non-empty string")

        now = float(self._clock())
        with self._lock:
            record = self._tokens.pop(token, None)
            if record is None:
                # Token either never existed or was already used.
                raise PairingError("unknown_token", "pairing token is invalid or already used")
            if record.expires_at < now:
                raise PairingError("expired_token", "pairing token has expired")

        return self._persist(device_id, name, public_key)

    # -- introspection (used by tests) ------------------------------------

    def has_token(self, token: str) -> bool:
        """Return ``True`` if ``token`` is still in the cache and unexpired."""
        now = float(self._clock())
        with self._lock:
            self._evict_expired_locked()
            return token in self._tokens

    def pending_count(self) -> int:
        """Return the number of unexpired tokens currently cached."""
        with self._lock:
            self._evict_expired_locked()
            return len(self._tokens)

    def clear(self) -> None:
        """Drop every cached token. Test helper — also useful if the
        operator wants to "reset pairing" without restarting the agent.
        """
        with self._lock:
            self._tokens.clear()

    # -- subclass hook ----------------------------------------------------

    def _persist(
        self,
        device_id: str,
        name: str,
        public_key: str,
    ) -> dict[str, Any]:
        """Subclass hook: write the device row. The base class raises
        — concrete subclasses (e.g. :class:`PairingManagerWithDAO`)
        override this to talk to storage.
        """
        raise NotImplementedError(
            "PairingManager._persist must be overridden by a subclass"
        )

    # -- internals --------------------------------------------------------

    def _evict_expired_locked(self) -> None:
        """Drop every record whose ``expires_at`` is in the past.

        Called under :attr:`_lock`. We keep it cheap (O(n) over the
        cache) — the cache never has more than a handful of entries
        in a normal pairing flow.
        """
        now = float(self._clock())
        stale = [t for t, r in self._tokens.items() if r.expires_at < now]
        for t in stale:
            self._tokens.pop(t, None)


# ---------------------------------------------------------------------------
# Concrete subclass that talks to a MobileDeviceDAO
# ---------------------------------------------------------------------------


class PairingManagerWithDAO(PairingManager):
    """Pairing manager that writes through a :class:`MobileDeviceDAO`.

    This is the class the IPC layer uses. The ``dao`` argument is
    expected to expose ``register(device_id, name, public_key)`` —
    see :class:`minimax_code.storage.dao.mobile_devices.MobileDeviceDAO`.
    """

    def __init__(self, dao: Any, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._dao = dao

    async def confirm_pairing(
        self,
        token: str,
        device_id: str,
        name: str,
        public_key: str,
    ) -> dict[str, Any]:
        """Validate token, then insert the device row.

        We split the sync validation (above) and the async DB write
        so the validation error path stays synchronous — easier to
        test, easier to reason about in the IPC layer.
        """
        # Run the validation + cache eviction first (sync).
        # Re-implementing the check here (vs calling the base's
        # sync method) keeps the flow linear and avoids an
        # event-loop dance for a pure-Python predicate.
        if not isinstance(token, str) or not token:
            raise PairingError("invalid_token", "token must be a non-empty string")
        if not isinstance(device_id, str) or not device_id.strip():
            raise PairingError("invalid_device_id", "device_id must be a non-empty string")
        if not isinstance(name, str) or not name.strip():
            raise PairingError("invalid_name", "name must be a non-empty string")
        if not isinstance(public_key, str) or not public_key.strip():
            raise PairingError("invalid_public_key", "public_key must be a non-empty string")

        now = float(self._clock())
        with self._lock:
            record = self._tokens.pop(token, None)
            if record is None:
                raise PairingError(
                    "unknown_token",
                    "pairing token is invalid or already used",
                )
            if record.expires_at < now:
                raise PairingError("expired_token", "pairing token has expired")

        return await self._dao.register(device_id, name, public_key)

    async def _persist(
        self,
        device_id: str,
        name: str,
        public_key: str,
    ) -> dict[str, Any]:
        # The base class's sync ``confirm_pairing`` calls
        # ``_persist`` synchronously, but the IPC layer always uses
        # :meth:`confirm_pairing` directly so this path is unused in
        # production. Implemented anyway for parity.
        return await self._dao.register(device_id, name, public_key)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_from_epoch(epoch: float) -> str:
    """Render a UNIX epoch as an ISO-8601 UTC string with ``Z`` suffix.

    The frontend uses the resulting string as ``expires_at``; the
    value is informational only (the manager compares against
    ``time.time()`` directly).
    """
    from datetime import UTC, datetime

    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_pairing_session_id() -> str:
    """Generate a short opaque id used as a ``pair_session_id`` hint
    in the QR-code payload. Currently a hex-encoded uuid4 — enough
    uniqueness that the device side can correlate a scan back to a
    specific desktop session.
    """
    return f"pair_{uuid.uuid4().hex[:16]}"


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------


_MANAGER: PairingManager | None = None
_MOBILE_DAO: Any | None = None
_MANAGER_LOCK = threading.Lock()


def get_pairing_manager() -> PairingManager | None:
    """Return the process-wide :class:`PairingManager`, or ``None``.

    The manager is wired up by :func:`minimax_code.app.register_app_handlers`
    once the DB is open. Handlers fall back to a transient in-memory
    manager if it's still ``None`` so a ``mobile.pair_start`` call
    before the DB is ready doesn't crash.
    """
    return _MANAGER


def set_pairing_manager(manager: PairingManager | None) -> None:
    """Replace the cached manager (test seam)."""
    global _MANAGER
    _MANAGER = manager


def get_mobile_dao() -> Any | None:
    """Return the process-wide :class:`MobileDeviceDAO`, or ``None``.

    Populated by :func:`minimax_code.app.register_app_handlers` once
    the DB is open. The ``mobile.*`` IPC handlers read from this
    singleton so they always see the same rows that
    :meth:`PairingManagerWithDAO.confirm_pairing` wrote.
    """
    return _MOBILE_DAO


def set_mobile_dao(dao: Any | None) -> None:
    """Replace the cached DAO (test seam)."""
    global _MOBILE_DAO
    _MOBILE_DAO = dao


__all__ = [
    "DEFAULT_TOKEN_TTL_SECONDS",
    "PairingError",
    "PairingManager",
    "PairingManagerWithDAO",
    "get_mobile_dao",
    "get_pairing_manager",
    "new_pairing_session_id",
    "set_mobile_dao",
    "set_pairing_manager",
]
