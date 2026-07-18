"""Voice pipeline configuration (R33).

Ports ``xai-grok-voice/src/config.rs`` — the ``VoiceConfig`` transport-knob
table plus the TLS-only WebSocket URL builder. Pure data + pure logic: no IO,
no async, no audio. This is the last host-agnostic slice of the voice crate —
it ties together the R31 language catalog and the R32 error hierarchy, and
hands a ready URL + language code to the future streaming-STT driver.

Two security properties are the heart of this module and must be preserved:

1. **TLS-only**: ``http://`` / ``ws://`` ``api_base`` is *rejected* (raises
   :class:`VoiceConfigError`), never silently downgraded — the bearer token
   travels on this socket and must never traverse plaintext.
2. **Anti-spoof identity**: ``client_identifier`` / ``user_agent`` are runtime
   identity stamped by the host, *not* user config — ``from_config_table``
   deliberately ignores them even if present, so a user can't forge the
   attribution headers.

Struct → dataclass mapping
--------------------------
grok's ``VoiceConfig`` is ``#[derive(Debug, Clone, Serialize, Deserialize,
PartialEq)]`` — note *no* ``Eq``/``Hash`` (it's a mutable serde target), so we
use a plain ``@dataclass`` (mutable, not frozen). All fields carry defaults
(grok's ``#[serde(default)]`` makes the ``[voice]`` table optional).
``from_config_table`` recreates the no-``deny_unknown_fields`` tolerance
(legacy/unknown keys are silently dropped) and the ``#[serde(skip)]`` identity
discipline.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .error import VoiceConfigError
from .language import STT_LANGUAGE_DEFAULT

__all__ = [
    "VoiceConfig",
    "ws_url",
    "from_config_table",
]


@dataclass
class VoiceConfig:
    """Voice settings for the STT transport.

    Carries transport knobs parsed from the optional ``[voice]`` config table
    (STT URL pieces, language, sample rate, endpointing) plus two runtime-
    identity fields the host stamps in after parsing (``client_identifier``,
    ``user_agent``). Whether voice is available is resolved by the host — there
    is deliberately no local enable/disable knob here.
    """

    api_base: str = "https://api.x.ai"
    stt_ws_path: str = "/v1/stt"
    language: str = STT_LANGUAGE_DEFAULT
    sample_rate: int = 16_000
    stt_endpointing_ms: int = 400
    stt_interim_results: bool = True
    # Runtime identity, NOT user config — host stamps these after parsing.
    client_identifier: str = ""
    user_agent: str = ""

    def stt_ws_url(self) -> str:
        """Build the streaming-STT WebSocket URL (TLS-only).

        Raises:
            VoiceConfigError: if ``api_base`` is ``http://`` / ``ws://``
                (insecure — bearer token would traverse plaintext).
        """
        return ws_url(self.api_base, self.stt_ws_path)


def ws_url(api_base: str, path: str) -> str:
    """Build a TLS-only ``wss://`` URL from ``api_base`` + ``path``.

    Only TLS endpoints are allowed: ``https://`` / ``wss://`` (or scheme-less)
    ``api_base`` maps to ``wss://``. ``http://`` / ``ws://`` is rejected with
    :class:`VoiceConfigError` rather than silently downgraded, since the bearer
    token is sent as a header on this connection and must never traverse a
    plaintext socket.
    """
    base = api_base.rstrip("/")
    if base.startswith(("http://", "ws://")):
        raise VoiceConfigError(
            f"insecure voice api_base {api_base!r}: voice requires a TLS endpoint "
            "(https:// / wss://). Refusing to send the bearer token over a "
            "plaintext connection."
        )
    for scheme in ("https://", "wss://"):
        if base.startswith(scheme):
            base = base[len(scheme) :]
            break
    path = path.lstrip("/")
    return f"wss://{base}/{path}"


def from_config_table(root: Mapping[str, Any]) -> VoiceConfig:
    """Parse ``[voice]`` from the root of an effective config document.

    Recreates grok's serde semantics:

    * missing ``[voice]`` table → all defaults;
    * unknown / legacy keys (e.g. the removed ``enabled`` opt-out) silently
      dropped (no ``deny_unknown_fields``);
    * ``client_identifier`` / ``user_agent`` deliberately **not** read — runtime
      identity, anti-spoof (grok ``#[serde(skip)]``).
    """
    table = root.get("voice")
    if not isinstance(table, Mapping):
        return VoiceConfig()
    fields: dict[str, Any] = {}
    for str_key in ("api_base", "stt_ws_path", "language"):
        if str_key in table:
            fields[str_key] = table[str_key]
    for int_key in ("sample_rate", "stt_endpointing_ms"):
        if int_key in table:
            fields[int_key] = table[int_key]
    if "stt_interim_results" in table:
        fields["stt_interim_results"] = table["stt_interim_results"]
    return VoiceConfig(**fields)
