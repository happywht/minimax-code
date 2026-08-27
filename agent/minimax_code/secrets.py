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

#: Provider id that owns the legacy global key. v1.6.4: the API Key
#: settings tab was retired, so the legacy keyring entry migrates
#: into this provider's per-provider slot on first read.
_BUILTIN_MINIMAX: Final = "builtin-minimax"


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


def has_api_key() -> bool:
    """``True`` iff :func:`get_api_key` would return a non-empty value.

    Convenience for the UI's "is the agent in mock mode?" check.
    Same failure semantics as :func:`get_api_key` (never raises).
    """
    return get_api_key() is not None


def key_source() -> str:
    """Return which lookup layer is currently serving the API key.

    One of:

    * ``"keyring"`` — read from the OS keyring.
    * ``"env"``    — read from ``MINIMAX_API_KEY`` env var.
    * ``"none"``   — no key configured; the agent is in mock mode.

    The IPC ``secrets.status`` endpoint exposes this verbatim so
    the UI can show *where* the active key is coming from. Never
    raises — backend failures (keyring down) silently fall
    through to the env-var / none tiers, same as
    :func:`get_api_key`.
    """
    if _read_keyring():
        return "keyring"
    if _read_env():
        return "env"
    return "none"


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
# Per-provider key management
# ---------------------------------------------------------------------------


def get_provider_key(provider_id: str) -> str | None:
    """Return the API key for a specific provider.

    Lookup order:
    1. OS keyring (``minimax-code / provider:<provider_id>``).
    2. For the built-in MiniMax provider only — one-time migration:
       the legacy global keyring entry (``minimax-code / api_key``)
       is copied into the per-provider slot, then served. The legacy
       entry itself is left in place; it is removed only when the
       user clears the provider key (see :func:`clear_provider_key`),
       which keeps "clear" final.
    3. For the built-in provider only, the ``MINIMAX_API_KEY`` env
       var (dynamic — never persisted to the keyring).

    Returns ``None`` if no key is found anywhere.
    """
    key = _read_keyring_username(f"provider:{provider_id}")
    if key:
        return key
    # The legacy global key belongs to MiniMax. Reusing it for an
    # arbitrary OpenAI-compatible provider makes that provider look
    # configured while sending the wrong credential.
    if provider_id == _BUILTIN_MINIMAX:
        legacy = _read_keyring()
        if legacy:
            # Migrate on first sight so the Providers tab owns the
            # credential lifecycle (the API Key tab was retired).
            # Fail-open: a broken keyring still serves the value, and
            # the next call retries the copy (idempotent, same value).
            try:
                set_provider_key(provider_id, legacy)
            except Exception:
                logger.debug(
                    "legacy key migration deferred for %s", provider_id, exc_info=True
                )
            return legacy
        return _read_env()
    return None


def set_provider_key(provider_id: str, value: str) -> None:
    """Persist an API key for a specific provider to the OS keyring."""
    import keyring
    import keyring.errors

    if not value:
        raise ValueError("api key must be a non-empty string")
    try:
        keyring.set_password(
            KEYRING_SERVICE, f"provider:{provider_id}", value
        )
    except keyring.errors.KeyringError:
        logger.exception("failed to write provider key to keyring")
        raise


def clear_provider_key(provider_id: str) -> None:
    """Remove a provider-specific API key from the OS keyring.

    Idempotent — calling when no entry exists is a no-op. Raises
    :class:`keyring.errors.KeyringError` on backend failure.

    For the built-in MiniMax provider the legacy global keyring
    entry is removed too — otherwise the next
    :func:`get_provider_key` call would migrate it right back and
    silently undo the user's "clear".
    """
    import keyring
    import keyring.errors

    username = f"provider:{provider_id}"
    try:
        keyring.delete_password(KEYRING_SERVICE, username)
    except keyring.errors.PasswordDeleteError:
        return
    except keyring.errors.KeyringError:
        logger.exception("failed to delete provider key from keyring")
        raise
    if provider_id == _BUILTIN_MINIMAX:
        # Fail-open on purpose: if the legacy entry survives, the
        # migration in get_provider_key() will bring it back — but
        # the explicit clear was persisted for the provider slot,
        # which is what the UI surfaces.
        try:
            clear_api_key()
        except Exception:
            logger.debug(
                "legacy key cleanup deferred for %s", provider_id, exc_info=True
            )


def has_provider_key(provider_id: str) -> bool:
    """``True`` iff a specific provider has an API key configured."""
    return get_provider_key(provider_id) is not None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _read_keyring() -> str | None:
    """Try the OS keyring. Returns ``None`` on any failure."""
    return _read_keyring_username(KEYRING_USERNAME)


def _read_keyring_username(username: str) -> str | None:
    """Try the OS keyring for an arbitrary username. Returns ``None`` on failure."""
    import keyring
    import keyring.errors

    try:
        value = keyring.get_password(KEYRING_SERVICE, username)
    except keyring.errors.KeyringError as exc:
        logger.debug("keyring unavailable for %s: %s", username, exc)
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
    "clear_provider_key",
    "get_api_key",
    "get_provider_key",
    "has_api_key",
    "key_source",
    "set_api_key",
    "set_provider_key",
]
