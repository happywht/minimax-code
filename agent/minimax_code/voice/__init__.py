"""Voice input — language + event/error/config types (R31-R33).

Ports the host-agnostic slices of grok-build's ``xai-grok-voice`` crate:

* **R31** — the STT 25-language catalog (pinned to docs.x.ai) and the pure
  canonicalization / locale-resolution functions that turn a user/config string
  into a concrete code for the speech-to-text wire. Zero IO, zero network,
  zero audio — the language foundation for any future voice-input feature.
* **R32** — the pipeline event union (``VoiceEvent``: interim transcript /
  utterance final / error) and the error hierarchy (``VoiceError`` + 4
  variants). Pure type layer — the signals a voice pipeline emits and the
  ways it fails, before any host wiring.
* **R33** — the ``VoiceConfig`` transport-knob table + the TLS-only WebSocket
  URL builder. Ties R31/R32 together and enforces two security invariants:
  TLS-only ``api_base`` (bearer token never traverses plaintext) and
  anti-spoof runtime-identity fields (``client_identifier`` / ``user_agent``
  are host-stamped, never user-set).

The heavier voice slices (``audio`` capture, streaming ``stt`` client,
``pipeline`` driver, ``probe``) are host-integration layers — audio hardware,
network streaming, the event loop — and remain future rounds.
"""

from __future__ import annotations

from .config import (
    VoiceConfig,
    from_config_table,
    ws_url,
)
from .error import (
    VoiceAuthError,
    VoiceConfigError,
    VoiceError,
    VoiceSttError,
    VoiceWebSocketError,
)
from .event import (
    InterimTranscript,
    UtteranceFinal,
    VoiceEvent,
    VoiceEventError,
)
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
    # language (R31)
    "SttLanguage",
    "STT_LANGUAGE_AUTO",
    "STT_LANGUAGE_DEFAULT",
    "STT_LANGUAGES",
    "stt_language_by_code",
    "canonicalize_stt_language",
    "language_for_api",
    # event (R32)
    "InterimTranscript",
    "UtteranceFinal",
    "VoiceEventError",
    "VoiceEvent",
    # error (R32)
    "VoiceError",
    "VoiceConfigError",
    "VoiceSttError",
    "VoiceAuthError",
    "VoiceWebSocketError",
    # config (R33)
    "VoiceConfig",
    "ws_url",
    "from_config_table",
]
