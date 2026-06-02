"""OS keyring-backed secret storage with env-var fallback.

This module is the single seam through which the agent reads the
MiniMax API key. The lookup order is:

1. **OS keyring** — :func:`keyring.get_password` on
   ``service="minimax-code"``, ``username="api_key"``. On Windows
   this writes to the *Windows Credential Manager*; on macOS to
   *Keychain*; on Linux to *Secret Service* (libsecret).
2. **Env var** — :data:`os.environ` ``MINIMAX_API_KEY``. Useful
   for CI runners, container images, and quick shell overrides.

Why a separate module?

* :class:`minimax_code.agent.llm.MiniMaxClient` imports this and
  calls :func:`get_api_key` on init. Tests can monkeypatch the
  keyring functions without touching the LLM client.
* Direct ``import keyring`` is deferred to function call time so
  an environment without a working keyring (e.g. a headless CI
  box) does not break the import chain.

Usage
-----

::

    from minimax_code import secrets

    secrets.set_api_key("sk-...")         # stores in OS keyring
    key = secrets.get_api_key()            # reads keyring, falls back to env
    secrets.clear_api_key()                # removes from keyring
"""

from __future__ import annotations

import logging
import os
from typing import Final

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


#: Keyring *service* (the "Internet or network address" on Windows
#: Credential Manager, the "kind" on macOS Keychain, the "schema" on
#: Secret Service).
KEYRING_SERVICE: Final = "minimax-code"

#: Keyring *username* (the label the user sees in Credential Manager
#: together with the service name). Keeping it stable means the entry
#: is upgraded in place when the user re-runs :func:`set_api_key`.
KEYRING_USERNAME: Final = "api_key"

#: Env var that takes priority over nothing — we always try keyring
#: first, this is the *fallback*. Documented here for discoverability.
ENV_VAR: Final = "MINIMAX_API_KEY"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_api_key() -> str | None:
    """Return the MiniMax API key, or ``None`` if none is configured.

    Lookup order:

    1. OS keyring (``minimax-code`` / ``api_key``).
    2. ``MINIMAX_API_KEY`` env var.

    Returns the first non-empty value. Returns ``None`` if both
    lookups yield nothing. Never raises — keyring failures
    degrade silently to the env var.
    """
    key = _read_keyring()
    if key:
        return key
    return _read_env()


def set_api_key(value: str) -> None:
    """Persist ``value`` to the OS keyring.

    Overwrites any existing entry. Raises :class:`keyring.errors.KeyringError`
    (or its backend-specific subclass) if the backend cannot
    store the value — the caller is expected to surface that
    to the user (e.g. "could not write to Credential Manager").
    """
    import keyring
    import keyring.errors

    if not value:
        raise ValueError("api key must be a non-empty string")
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, value)
    except keyring.errors.KeyringError:
        logger.exception("failed to write api key to keyring")
        raise


def clear_api_key() -> None:
    """Remove the API key from the OS keyring.

    Idempotent — calling when no entry exists is a no-op. Raises
    :class:`keyring.errors.KeyringError` on backend failure.
    """
    import keyring
    import keyring.errors

    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except keyring.errors.PasswordDeleteError:
        # Already gone — that's fine.
        return
    except keyring.errors.KeyringError:
        logger.exception("failed to delete api key from keyring")
        raise


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _read_keyring() -> str | None:
    """Try the OS keyring. Returns ``None`` on any failure."""
    import keyring
    import keyring.errors

    try:
        value = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except keyring.errors.KeyringError as exc:
        # Backend unavailable (no keyring daemon, headless container,
        # locked credential store). Fall back to env var — the
        # caller can't tell the difference anyway, and the env
        # var is exactly the kind of override a CI box uses.
        logger.debug("keyring unavailable, falling back to env: %s", exc)
        return None
    if value:
        return value
    return None


def _read_env() -> str | None:
    """Return ``MINIMAX_API_KEY`` from the environment, or ``None``.

    Empty string counts as "not set" — :class:`MiniMaxClient`
    treats an empty key as mock mode, so returning ``""`` here
    would defeat the point of having an env-var override.
    """
    value = os.environ.get(ENV_VAR)
    if value:
        return value
    return None


__all__ = [
    "ENV_VAR",
    "KEYRING_SERVICE",
    "KEYRING_USERNAME",
    "clear_api_key",
    "get_api_key",
    "set_api_key",
]
