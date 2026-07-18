"""Voice input — language layer (R31).

Ports the host-agnostic slice of grok-build's ``xai-grok-voice`` crate: the
STT language-code catalog plus the canonicalization / resolution helpers that
turn a user/config string into a concrete code for the speech-to-text wire.

* **R31** — the 25-language catalog (pinned to docs.x.ai) and the pure
  canonicalization / locale-resolution functions. Zero IO, zero network, zero
  audio — the language foundation for any future voice-input feature.

The heavier voice slices (``audio`` capture, streaming ``stt`` client,
``auth``, ``pipeline``, ``probe``) are host-integration layers — audio
hardware, network streaming, credentials — and remain future rounds.
"""

from __future__ import annotations

from .language import (
    STT_LANGUAGE_AUTO,
    STT_LANGUAGE_DEFAULT,
    STT_LANGUAGES,
    SttLanguage,
    canonicalize_stt_language,
    language_for_api,
    stt_language_by_code,
)

__all__ = [
    "SttLanguage",
    "STT_LANGUAGE_AUTO",
    "STT_LANGUAGE_DEFAULT",
    "STT_LANGUAGES",
    "stt_language_by_code",
    "canonicalize_stt_language",
    "language_for_api",
]
