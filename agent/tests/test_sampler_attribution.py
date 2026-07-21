"""Tests for sampler.attribution (R233, ``xai-grok-sampler``
``src/attribution.rs`` whole-leaf migration).

Covers the four migrated symbols: the :class:`SamplingConsumer` 6-variant
endpoint enum + its :meth:`as_endpoint` method, the
:data:`SENT_BEARER_PREFIX_LEN` cross-crate invariant constant, the
:class:`Auth401AttributionCallback` abstract trait (with its
:meth:`record_401` abstractmethod), and the :data:`SharedAttributionCallback`
type alias. The leaf clears the ``SamplerConfig`` deferred-dependency ledger
entry recorded in :mod:`minimax_code.sampler.config` (the
``attribution::SharedAttributionCallback (unmigrated)`` line).
"""

from __future__ import annotations

import abc

import pytest

from minimax_code import sampler
from minimax_code.sampler import attribution as sampler_attribution
from minimax_code.sampler.attribution import (
    SENT_BEARER_PREFIX_LEN,
    Auth401AttributionCallback,
    SamplingConsumer,
    SharedAttributionCallback,
)

# ---------------------------------------------------------------------------
# Barrel surface (module + package).
# ---------------------------------------------------------------------------


def test_module_barrel_exposes_four_symbols() -> None:
    assert len(sampler_attribution.__all__) == 4
    assert set(sampler_attribution.__all__) == {
        "Auth401AttributionCallback",
        "SENT_BEARER_PREFIX_LEN",
        "SamplingConsumer",
        "SharedAttributionCallback",
    }


def test_package_barrel_re_exports_attribution_symbols() -> None:
    """The package barrel re-exports all four attribution symbols (R233 adds
    4 -> the sampler ``__all__`` grows from 201 to 205; the total is asserted
    in ``test_sampler_config.test_package_barrel_exposes_...``)."""
    for name in sampler_attribution.__all__:
        assert name in sampler.__all__
        assert hasattr(sampler, name)
    # Identity: the package symbol IS the module symbol (re-export, not a copy).
    assert sampler.SamplingConsumer is SamplingConsumer
    assert sampler.Auth401AttributionCallback is Auth401AttributionCallback
    assert sampler.SharedAttributionCallback is SharedAttributionCallback
    # Constant: value equality (int identity is not language-guaranteed).
    assert sampler.SENT_BEARER_PREFIX_LEN == SENT_BEARER_PREFIX_LEN


# ---------------------------------------------------------------------------
# SamplingConsumer: 6 variants + value == endpoint + as_endpoint round-trip.
# ---------------------------------------------------------------------------


def test_sampling_consumer_has_exactly_six_variants() -> None:
    assert len(SamplingConsumer) == 6
    assert set(SamplingConsumer) == {
        SamplingConsumer.CHAT_COMPLETIONS_STREAM,
        SamplingConsumer.CHAT_COMPLETIONS,
        SamplingConsumer.RESPONSES_STREAM,
        SamplingConsumer.RESPONSES,
        SamplingConsumer.MESSAGES_STREAM,
        SamplingConsumer.MESSAGES,
    }


def test_sampling_consumer_values_match_endpoint_strings() -> None:
    """The member value IS the endpoint identifier string (mirrors grok
    ``as_endpoint`` return)."""
    assert SamplingConsumer.CHAT_COMPLETIONS_STREAM == "chat_completions_stream"
    assert SamplingConsumer.CHAT_COMPLETIONS == "chat_completions"
    assert SamplingConsumer.RESPONSES_STREAM == "responses_stream"
    assert SamplingConsumer.RESPONSES == "responses"
    assert SamplingConsumer.MESSAGES_STREAM == "messages_stream"
    assert SamplingConsumer.MESSAGES == "messages"


def test_sampling_consumer_str_is_wire_value() -> None:
    """``StrEnum.__str__`` returns the wire value (what serde would emit)."""
    assert str(SamplingConsumer.CHAT_COMPLETIONS_STREAM) == "chat_completions_stream"
    assert str(SamplingConsumer.MESSAGES) == "messages"


@pytest.mark.parametrize(
    ("consumer", "endpoint"),
    [
        (SamplingConsumer.CHAT_COMPLETIONS_STREAM, "chat_completions_stream"),
        (SamplingConsumer.CHAT_COMPLETIONS, "chat_completions"),
        (SamplingConsumer.RESPONSES_STREAM, "responses_stream"),
        (SamplingConsumer.RESPONSES, "responses"),
        (SamplingConsumer.MESSAGES_STREAM, "messages_stream"),
        (SamplingConsumer.MESSAGES, "messages"),
    ],
)
def test_as_endpoint_returns_stable_identifier(
    consumer: SamplingConsumer,
    endpoint: str,
) -> None:
    """``as_endpoint`` mirrors grok's explicit method (kept even though the
    value already equals the identifier, so the two concepts can diverge
    without touching call sites)."""
    assert consumer.as_endpoint() == endpoint
    # The method returns the wire value today; assert the invariant so a future
    # split is a deliberate, visible change.
    assert consumer.as_endpoint() == str(consumer)


# ---------------------------------------------------------------------------
# SENT_BEARER_PREFIX_LEN: cross-crate invariant (mirrors token_suffix = 12).
# ---------------------------------------------------------------------------


def test_sent_bearer_prefix_len_is_twelve() -> None:
    """grok ``SENT_BEARER_PREFIX_LEN: usize = 12`` -- mirrors
    ``xai_grok_shell::auth::token_suffix`` (12-char prefix invariant)."""
    assert SENT_BEARER_PREFIX_LEN == 12
    assert isinstance(SENT_BEARER_PREFIX_LEN, int)
    # Positive (would catch a sign / zero regression if recomputed).
    assert SENT_BEARER_PREFIX_LEN > 0


# ---------------------------------------------------------------------------
# Auth401AttributionCallback: abstract trait + record_401 abstractmethod.
# ---------------------------------------------------------------------------


def test_auth_callback_is_abstract_class() -> None:
    """The trait maps to ``abc.ABC``; the class cannot be instantiated without
    a concrete ``record_401`` override."""
    assert issubclass(Auth401AttributionCallback, abc.ABC)
    with pytest.raises(TypeError):
        Auth401AttributionCallback()  # type: ignore[abstract]


def test_record_401_is_abstractmethod() -> None:
    """``fn record_401`` is the single abstract trait method."""
    assert "record_401" in Auth401AttributionCallback.__abstractmethods__


def test_concrete_callback_must_implement_record_401() -> None:
    """A subclass missing ``record_401`` stays abstract and cannot instantiate."""

    class _Incomplete(Auth401AttributionCallback):
        pass

    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]


def test_concrete_callback_receives_consumer_and_prefix() -> None:
    """``record_401`` receives the ``SamplingConsumer`` site + the scrubbed
    bearer prefix (or ``None`` when no bearer was sent)."""

    recorded: list[tuple[SamplingConsumer, str | None]] = []

    class _Recorder(Auth401AttributionCallback):
        def record_401(
            self,
            consumer: SamplingConsumer,
            sent_bearer_prefix: str | None,
        ) -> None:
            recorded.append((consumer, sent_bearer_prefix))

    cb = _Recorder()
    cb.record_401(SamplingConsumer.MESSAGES, "Bearer ABC123")
    cb.record_401(SamplingConsumer.CHAT_COMPLETIONS_STREAM, None)

    assert recorded == [
        (SamplingConsumer.MESSAGES, "Bearer ABC123"),
        (SamplingConsumer.CHAT_COMPLETIONS_STREAM, None),
    ]


# ---------------------------------------------------------------------------
# SharedAttributionCallback: type alias (Arc<dyn Trait> -> bare alias).
# ---------------------------------------------------------------------------


def test_shared_attribution_callback_alias_is_the_trait() -> None:
    """grok ``SharedAttributionCallback = Arc<dyn Auth401AttributionCallback>``.
    Python objects are shared by reference natively (no ``Arc`` runtime
    wrapper); the alias is the trait itself."""
    assert SharedAttributionCallback is Auth401AttributionCallback


def test_concrete_callback_satisfies_shared_alias() -> None:
    """A concrete callback instance can be assigned to the alias (it IS the
    trait type), and ``isinstance`` holds -- a caller wiring
    ``Option<Arc<dyn ...>>`` accepts any concrete implementation."""

    class _Recorder(Auth401AttributionCallback):
        def record_401(
            self,
            consumer: SamplingConsumer,
            sent_bearer_prefix: str | None,
        ) -> None:
            return None

    cb: SharedAttributionCallback = _Recorder()
    assert isinstance(cb, SharedAttributionCallback)
