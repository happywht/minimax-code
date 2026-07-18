"""Voice pipeline error hierarchy (R32).

Ports ``xai-grok-voice/src/error.rs`` — the ``VoiceError`` enum (4 variants,
each a wrapped ``String``) that grok derives via ``thiserror`` with a per-
variant ``#[error("prefix: {0}")]`` display. Pure type layer: no IO, no async.

Product fusion: voice pipelines fail in four well-understood ways — bad
config, STT engine errors, auth failures, WebSocket transport errors. Mapping
each to a dedicated ``VoiceError`` subclass gives MiniMax Code's existing
error-handling paths (which already discriminate by exception type) a clean
way to route voice failures without string-matching messages.

Enum → exception hierarchy mapping
-----------------------------------
grok's ``VoiceError`` is a Rust enum with tuple variants (``Config(String)``,
etc.) plus ``thiserror`` display impls. Python's native equivalent of a
display-formatted error enum is an exception hierarchy: a base class
(:class:`VoiceError`) carries the shared ``message`` payload and the
``"prefix: message"`` formatting that recreates ``#[error]``; each subclass
sets its ``_prefix`` to the variant's literal. ``raise VoiceConfigError("x")``
/ ``except VoiceError`` is the Pythonic ``Result::Err`` / pattern-match
equivalent — far more idiomatic than a value enum, because Python errors flow
through ``raise``/``except``, not ``Result`` returns.
"""

from __future__ import annotations

__all__ = [
    "VoiceError",
    "VoiceConfigError",
    "VoiceSttError",
    "VoiceAuthError",
    "VoiceWebSocketError",
]


class VoiceError(Exception):
    """Base class for voice-pipeline errors.

    Mirrors grok's ``VoiceError`` enum. Each subclass sets ``_prefix`` to
    recreate ``thiserror``'s ``#[error("prefix: {0}")]`` display; the formatted
    string is stored as the exception's ``args`` (so ``str(exc)`` works) and
    the raw payload is kept on ``.message``.
    """

    _prefix: str = "voice"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(f"{self._prefix}: {message}")

    def __str__(self) -> str:
        return f"{self._prefix}: {self.message}"


class VoiceConfigError(VoiceError):
    """Configuration error (grok ``VoiceError::Config``)."""

    _prefix = "configuration"


class VoiceSttError(VoiceError):
    """STT engine error (grok ``VoiceError::Stt``)."""

    _prefix = "STT"


class VoiceAuthError(VoiceError):
    """Authentication error (grok ``VoiceError::Auth``)."""

    _prefix = "auth"


class VoiceWebSocketError(VoiceError):
    """WebSocket transport error (grok ``VoiceError::WebSocket``)."""

    _prefix = "WebSocket"
