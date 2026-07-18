"""Tests for the voice pipeline event union (R32).

grok's ``event.rs`` has no ``#[cfg(test)]`` module (the file is a 12-line enum
definition), so these tests are all Python-specific guards: the union
membership, per-variant fields, frozen-dataclass value semantics, and the
``isinstance`` / ``match`` dispatch patterns that are Python's structural
analogue of Rust's ``match`` on the enum.
"""

from __future__ import annotations

import pytest

from minimax_code.voice import (
    InterimTranscript,
    UtteranceFinal,
    VoiceEvent,
    VoiceEventError,
)
from minimax_code.voice.event import VoiceEvent as VoiceEventFromModule


def test_voice_event_alias_is_union_of_three():
    """VoiceEvent is exactly the union of the three variant dataclasses."""
    args = set(VoiceEvent.__args__)
    assert args == {InterimTranscript, UtteranceFinal, VoiceEventError}


def test_voice_event_reexported_from_package():
    assert VoiceEvent is VoiceEventFromModule


def test_interim_transcript_fields():
    assert InterimTranscript(text="hello").text == "hello"


def test_utterance_final_fields():
    assert UtteranceFinal(text="submit me").text == "submit me"


def test_voice_event_error_fields():
    assert VoiceEventError(message="STT timed out").message == "STT timed out"


@pytest.mark.parametrize(
    ("event", "kind"),
    [
        (InterimTranscript(text="x"), InterimTranscript),
        (UtteranceFinal(text="y"), UtteranceFinal),
        (VoiceEventError(message="z"), VoiceEventError),
    ],
)
def test_isinstance_dispatch(event, kind):
    """isinstance is the structural match on the union (Rust `match`)."""
    assert isinstance(event, kind)
    assert isinstance(event, VoiceEvent)


def test_variants_are_frozen_and_hashable():
    """frozen + slots: immutable, hashable, value-equal (Rust derive PartialEq+Eq+Hash)."""
    a = InterimTranscript(text="x")
    b = InterimTranscript(text="x")
    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b}) == 1
    with pytest.raises((AttributeError, TypeError)):
        a.text = "mut"  # type: ignore[misc]


def test_variants_are_distinct_types():
    """Each variant is a distinct class — no overlap in the union."""
    assert InterimTranscript is not UtteranceFinal
    assert UtteranceFinal is not VoiceEventError
    assert InterimTranscript is not VoiceEventError


def test_match_statement_dispatch():
    """A `match` statement mirrors Rust's match on the enum."""

    def classify(evt: VoiceEvent) -> str:
        match evt:
            case InterimTranscript(text=t):
                return f"interim:{t}"
            case UtteranceFinal(text=t):
                return f"final:{t}"
            case VoiceEventError(message=m):
                return f"error:{m}"

    assert classify(InterimTranscript(text="hi")) == "interim:hi"
    assert classify(UtteranceFinal(text="done")) == "final:done"
    assert classify(VoiceEventError(message="bad")) == "error:bad"
