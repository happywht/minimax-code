"""Voice pipeline events (R32).

Ports ``xai-grok-voice/src/event.rs`` — the events emitted by
``run_voice_pipeline`` to the pager event loop. Pure tagged-union data: no IO,
no async, no host dependency. The pipeline pushes these as STT chunks arrive;
the UI consumes them to render live transcripts and surface errors.

Product fusion: ``InterimTranscript`` / ``UtteranceFinal`` map directly onto
MiniMax Code's existing streaming message channel (``agent.message_chunk``) —
a voice-driven prompt is just another streaming producer. ``VoiceEventError``
mirrors the error-surfacing convention already used by tool results. The
pipeline driver itself (mic capture → STT WebSocket → these events) is a
host-integration layer and remains a future round.

Enum → tagged-union mapping
---------------------------
grok's ``VoiceEvent`` is a Rust enum with struct variants carrying a payload
(``InterimTranscript { text }``, etc.). The faithful Python analogue is a
union of frozen dataclasses — one per variant — with a ``VoiceEvent`` type
alias as the union. Dispatch is ``isinstance`` (Python's structural match on
the union), which mirrors Rust's ``match``; a ``match`` statement works too.
frozen + slots gives the Rust ``Debug + Clone + PartialEq + Eq`` derives
(immutable, comparable, hashable).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "InterimTranscript",
    "UtteranceFinal",
    "VoiceEventError",
    "VoiceEvent",
]


@dataclass(frozen=True, slots=True)
class InterimTranscript:
    """Partial transcript while the user is speaking.

    Emitted for ``interim_results`` / non-final streaming chunks — the live,
    updating text shown as the user talks, before the utterance completes.
    """

    text: str


@dataclass(frozen=True, slots=True)
class UtteranceFinal:
    """Utterance complete — the final, committed transcript.

    Emitted on ``speech_final`` (streaming STT) or as a batch result. This is
    the text that becomes the user's prompt submission.
    """

    text: str


@dataclass(frozen=True, slots=True)
class VoiceEventError:
    """Non-fatal or fatal error from STT, surfaced as a pipeline event.

    Distinct from :class:`minimax_code.voice.error.VoiceError` (the exception
    hierarchy for thrown errors): this is an *event* carrying a message string,
    emitted on the event stream so the UI can render it inline rather than via
    a thrown exception.
    """

    message: str


#: Tagged union of all voice-pipeline events (Rust ``VoiceEvent`` enum).
#:
#: Dispatch with ``isinstance(evt, InterimTranscript | UtteranceFinal | VoiceEventError)``
#: or a ``match`` statement — Python's structural analogue of Rust's ``match``
#: on the enum.
VoiceEvent = InterimTranscript | UtteranceFinal | VoiceEventError
