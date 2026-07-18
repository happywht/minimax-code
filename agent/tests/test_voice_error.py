"""Tests for the voice error hierarchy (R32).

grok's ``error.rs`` has no ``#[cfg(test)]`` module (the file is a 24-line
thiserror enum), so these tests pin the per-variant prefix/display that
recreates ``#[error("prefix: {0}")]``, the exception-hierarchy relationships
(``issubclass`` / ``isinstance``), and the raise/catch semantics that are
Python's analogue of Rust ``Result::Err`` propagation.
"""

from __future__ import annotations

import pytest

from minimax_code.voice import (
    VoiceAuthError,
    VoiceConfigError,
    VoiceError,
    VoiceSttError,
    VoiceWebSocketError,
)


@pytest.mark.parametrize(
    ("exc_cls", "prefix", "payload", "display"),
    [
        (VoiceConfigError, "configuration", "bad config", "configuration: bad config"),
        (VoiceSttError, "STT", "engine down", "STT: engine down"),
        (VoiceAuthError, "auth", "no token", "auth: no token"),
        (VoiceWebSocketError, "WebSocket", "closed", "WebSocket: closed"),
    ],
)
def test_variant_prefix_and_display(exc_cls, prefix, payload, display):
    """Each variant carries its thiserror prefix and formats display correctly."""
    exc = exc_cls(payload)
    assert exc._prefix == prefix
    assert exc.message == payload
    assert str(exc) == display


def test_base_voice_error_display():
    """The bare base class uses the default 'voice' prefix."""
    exc = VoiceError("generic")
    assert exc._prefix == "voice"
    assert str(exc) == "voice: generic"


@pytest.mark.parametrize(
    "exc_cls",
    [VoiceConfigError, VoiceSttError, VoiceAuthError, VoiceWebSocketError],
)
def test_all_variants_are_voice_errors(exc_cls):
    """Every variant subclasses VoiceError (the enum's single-type root)."""
    assert issubclass(exc_cls, VoiceError)
    assert isinstance(exc_cls("x"), VoiceError)


def test_raise_and_catch_as_base():
    """A subclass raised is catchable as the base (Result::Err propagation)."""
    with pytest.raises(VoiceError):
        raise VoiceSttError("boom")


def test_variants_are_distinct_exception_types():
    """Each variant is catchable independently of the others."""
    assert not issubclass(VoiceSttError, VoiceAuthError)
    assert not issubclass(VoiceAuthError, VoiceWebSocketError)
    assert not issubclass(VoiceWebSocketError, VoiceConfigError)


def test_catch_specific_variant_only():
    """A specific except does not swallow a different variant."""
    with pytest.raises(VoiceSttError):
        try:
            raise VoiceSttError("x")
        except VoiceAuthError:  # pragma: no cover
            pytest.fail("VoiceSttError must not be caught as VoiceAuthError")


def test_message_payload_appears_in_display():
    """The raw payload survives on .message and is embedded in str()."""
    exc = VoiceWebSocketError("1006 abnormal closure")
    assert exc.message == "1006 abnormal closure"
    assert exc.message in str(exc)
    assert str(exc).startswith("WebSocket: ")


def test_exception_args_carry_display_string():
    """str(exc) == args[0] — the formatted display is the exception message."""
    exc = VoiceConfigError("bad")
    assert exc.args == ("configuration: bad",)
    assert str(exc) == exc.args[0]
