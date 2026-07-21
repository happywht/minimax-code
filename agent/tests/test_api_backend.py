"""Tests for ``sampler.api_backend`` (R214, ``xai-grok-sampling-types``
``types.rs`` 1010-1030).

Covers the ``ApiBackend`` 3-variant snake_case wire-string enum + its strict
``from_payload`` parser + the ``supports_native_schema`` decision method +
the :data:`DEFAULT_API_BACKEND` constant + barrel re-export identity.

Keystone under test -- the ``supports_native_schema`` asymmetry: the Messages
API does NOT enforce a response JSON schema natively alongside tool calls (a
schema there blocks tool use, so structured output goes through the
StructuredOutput tool instead); Chat Completions + Responses DO. This is the
single behavioral reason the enum exists as more than a plain string -- it
gates the structured-output dispatch.
"""

from __future__ import annotations

import pytest

from minimax_code.sampler import DEFAULT_API_BACKEND, ApiBackend
from minimax_code.sampler.api_backend import (
    DEFAULT_API_BACKEND as _DirectDefault,
)
from minimax_code.sampler.api_backend import ApiBackend as _DirectApiBackend


class TestBarrelReExport:
    """The sampler barrel re-exports both symbols by identity."""

    def test_barrel_api_backend_is_direct_module(self) -> None:
        assert ApiBackend is _DirectApiBackend

    def test_barrel_default_is_direct_module(self) -> None:
        assert DEFAULT_API_BACKEND is _DirectDefault


class TestWireValues:
    """The 3 snake_case wire values mirror grok ``#[serde(rename_all)]``."""

    @pytest.mark.parametrize(
        ("variant", "wire"),
        [
            (ApiBackend.CHAT_COMPLETIONS, "chat_completions"),
            (ApiBackend.RESPONSES, "responses"),
            (ApiBackend.MESSAGES, "messages"),
        ],
    )
    def test_value_and_str_yield_wire_string(
        self, variant: ApiBackend, wire: str
    ) -> None:
        assert variant.value == wire
        assert str(variant) == wire

    def test_enum_has_exactly_three_distinct_variants(self) -> None:
        values = {variant.value for variant in ApiBackend}
        assert values == {"chat_completions", "responses", "messages"}

    def test_direct_construction_via_str_lookup_round_trips(self) -> None:
        # StrEnum value lookup is the inherited serde Deserialize peer.
        assert ApiBackend("chat_completions") is ApiBackend.CHAT_COMPLETIONS
        assert ApiBackend("responses") is ApiBackend.RESPONSES
        assert ApiBackend("messages") is ApiBackend.MESSAGES

    def test_unknown_wire_string_direct_construction_raises(self) -> None:
        # No catch-all variant -> unknown wire string rejects (serde enum fail).
        with pytest.raises(ValueError):
            ApiBackend("completions")


class TestFromPayloadStrict:
    """``from_payload`` is the strict single-string parser (no catch-all)."""

    @pytest.mark.parametrize(
        "wire", ["chat_completions", "responses", "messages"]
    )
    def test_accepts_known_wire_string(self, wire: str) -> None:
        parsed = ApiBackend.from_payload(wire)
        assert parsed.value == wire

    def test_unknown_wire_string_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="unknown api_backend"):
            ApiBackend.from_payload("completions")

    def test_case_sensitive_uppercase_rejected(self) -> None:
        # snake_case wire strings are lowercase; uppercase is NOT a variant.
        with pytest.raises(ValueError, match="unknown api_backend"):
            ApiBackend.from_payload("ChatCompletions")

    @pytest.mark.parametrize(
        "bad", [123, None, [], {}, 1.5, True, b"messages"]
    )
    def test_non_string_raises_value_error(self, bad: object) -> None:
        with pytest.raises(ValueError, match="must be a string"):
            ApiBackend.from_payload(bad)

    def test_value_error_chains_from_inner_lookup(self) -> None:
        # The outer message wraps the inner StrEnum lookup failure (`from exc`).
        with pytest.raises(ValueError) as exc_info:
            ApiBackend.from_payload("streaming")
        assert "unknown api_backend" in str(exc_info.value)
        assert exc_info.value.__cause__ is not None


class TestDefault:
    """``DEFAULT_API_BACKEND`` mirrors grok ``#[default] ChatCompletions``."""

    def test_default_is_chat_completions_variant(self) -> None:
        assert DEFAULT_API_BACKEND is ApiBackend.CHAT_COMPLETIONS

    def test_default_wire_value_is_chat_completions(self) -> None:
        assert DEFAULT_API_BACKEND.value == "chat_completions"


class TestSupportsNativeSchema:
    """The structured-output dispatch gate (the enum's keystone behavior)."""

    @pytest.mark.parametrize(
        ("variant", "expected"),
        [
            (ApiBackend.CHAT_COMPLETIONS, True),
            (ApiBackend.RESPONSES, True),
            (ApiBackend.MESSAGES, False),
        ],
    )
    def test_truth_table(self, variant: ApiBackend, expected: bool) -> None:
        assert variant.supports_native_schema() is expected

    def test_default_backend_supports_native_schema(self) -> None:
        # The default (Chat Completions) enforces a response JSON schema
        # natively alongside tool calls.
        assert DEFAULT_API_BACKEND.supports_native_schema() is True

    def test_messages_backend_does_not_support_native_schema(self) -> None:
        # The Messages API blocks tool use under a schema -> structured output
        # routes through the StructuredOutput tool instead.
        assert ApiBackend.MESSAGES.supports_native_schema() is False


class TestIdentityAndEquality:
    """StrEnum members compare by identity (no two variants alias)."""

    def test_each_variant_is_distinct_identity(self) -> None:
        variants = [
            ApiBackend.CHAT_COMPLETIONS,
            ApiBackend.RESPONSES,
            ApiBackend.MESSAGES,
        ]
        for i, left in enumerate(variants):
            for right in variants[i + 1 :]:
                assert left is not right
                assert left != right

    def test_variant_equals_its_wire_string(self) -> None:
        # StrEnum: a member compares equal to its own wire value.
        assert ApiBackend.CHAT_COMPLETIONS == "chat_completions"
        assert ApiBackend.RESPONSES == "responses"
        assert ApiBackend.MESSAGES == "messages"
