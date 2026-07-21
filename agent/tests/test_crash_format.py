"""Tests for minimax_code.crash.format (R226, JSON crash blob serialization)."""

from __future__ import annotations

import dataclasses
import json

import pytest

from minimax_code.crash.format import (
    MAGIC,
    MAX_FRAMES,
    VERSION,
    CrashBlob,
)


def _sample_blob() -> CrashBlob:
    """A representative valid blob for round-trip / mutation tests."""
    return CrashBlob(
        signal=11,  # SIGSEGV
        si_code=1,  # SEGV_MAPERR
        si_addr=0x7F8A12340000,
        pid=42,
        timestamp=1_712_678_587,
        frames=(0xDEADBEEF, 0xCAFEBABE, 0x12345678),
        app_version="0.8.0",
    )


def _sample_payload() -> dict[str, object]:
    """A representative valid payload (matches ``_sample_blob``)."""
    return {
        "magic": MAGIC,
        "version": VERSION,
        "signal": 11,
        "si_code": 1,
        "si_addr": 0x7F8A12340000,
        "pid": 42,
        "timestamp": 1_712_678_587,
        "frames": [0xDEADBEEF, 0xCAFEBABE, 0x12345678],
        "app_version": "0.8.0",
    }


class TestConstants:
    """grok ``format.rs`` constants (magic / version / max-frames)."""

    def test_magic_matches_grok(self) -> None:
        assert MAGIC == "GCRX"

    def test_version_matches_grok(self) -> None:
        assert VERSION == 1

    def test_max_frames_matches_grok(self) -> None:
        assert MAX_FRAMES == 64


class TestCrashBlobConstruction:
    """grok ``CrashBlob`` value type."""

    def test_construction_round_trips_all_fields(self) -> None:
        blob = _sample_blob()
        assert blob.signal == 11
        assert blob.si_code == 1
        assert blob.si_addr == 0x7F8A12340000
        assert blob.pid == 42
        assert blob.timestamp == 1_712_678_587
        assert blob.frames == (0xDEADBEEF, 0xCAFEBABE, 0x12345678)
        assert blob.app_version == "0.8.0"

    def test_empty_frames_accepted(self) -> None:
        blob = CrashBlob(
            signal=11,
            si_code=0,
            si_addr=0,
            pid=0,
            timestamp=0,
            frames=(),
            app_version="x",
        )
        assert blob.frames == ()

    def test_is_frozen(self) -> None:
        blob = _sample_blob()
        first_field = next(iter(type(blob).__slots__))
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(blob, first_field, 999)

    def test_slots_block_dynamic_attrs(self) -> None:
        # frozen's __setattr__ raises first; bypass it to confirm slots also guards.
        blob = _sample_blob()
        with pytest.raises(AttributeError):
            object.__setattr__(blob, "unexpected", "no dynamic attrs")

    def test_value_equality(self) -> None:
        assert _sample_blob() == _sample_blob()


class TestAsPayload:
    """grok ``writer`` module (serialize to JSON dict)."""

    def test_stamps_magic_and_version(self) -> None:
        payload = _sample_blob().as_payload()
        assert payload["magic"] == MAGIC
        assert payload["version"] == VERSION

    def test_emits_frames_as_list(self) -> None:
        payload = _sample_blob().as_payload()
        assert payload["frames"] == [0xDEADBEEF, 0xCAFEBABE, 0x12345678]
        assert isinstance(payload["frames"], list)

    def test_round_trips_through_from_payload(self) -> None:
        blob = _sample_blob()
        assert CrashBlob.from_payload(blob.as_payload()) == blob


class TestFromPayload:
    """grok ``CrashBlob::parse`` (refuse-and-return-None contract)."""

    def test_parses_valid_payload(self) -> None:
        blob = CrashBlob.from_payload(_sample_payload())
        assert blob is not None
        assert blob.signal == 11
        assert blob.si_code == 1
        assert blob.frames == (0xDEADBEEF, 0xCAFEBABE, 0x12345678)
        assert blob.app_version == "0.8.0"

    def test_returns_none_for_non_dict(self) -> None:
        assert CrashBlob.from_payload(None) is None
        assert CrashBlob.from_payload("GCRX") is None
        assert CrashBlob.from_payload(42) is None
        assert CrashBlob.from_payload([1, 2]) is None

    def test_returns_none_for_bad_magic(self) -> None:
        payload = _sample_payload()
        payload["magic"] = "NOPE"
        assert CrashBlob.from_payload(payload) is None

    def test_returns_none_for_wrong_version(self) -> None:
        payload = _sample_payload()
        payload["version"] = 99
        assert CrashBlob.from_payload(payload) is None

    def test_returns_none_for_missing_magic(self) -> None:
        payload = _sample_payload()
        del payload["magic"]
        assert CrashBlob.from_payload(payload) is None

    def test_returns_none_for_missing_field(self) -> None:
        for field in (
            "signal",
            "si_code",
            "si_addr",
            "pid",
            "timestamp",
            "frames",
            "app_version",
        ):
            payload = _sample_payload()
            del payload[field]
            assert CrashBlob.from_payload(payload) is None, f"missing {field} should reject"

    def test_returns_none_for_non_int_integer_field(self) -> None:
        for field in ("signal", "si_code", "si_addr", "pid", "timestamp"):
            payload = _sample_payload()
            payload[field] = "not-an-int"
            assert CrashBlob.from_payload(payload) is None, f"non-int {field} should reject"

    def test_returns_none_for_bool_integer_field(self) -> None:
        # bool is a subclass of int in Python; a crash signal / pid is never bool.
        payload = _sample_payload()
        payload["signal"] = True
        assert CrashBlob.from_payload(payload) is None

    def test_returns_none_for_non_string_app_version(self) -> None:
        payload = _sample_payload()
        payload["app_version"] = 108
        assert CrashBlob.from_payload(payload) is None

    def test_returns_none_for_non_list_frames(self) -> None:
        payload = _sample_payload()
        payload["frames"] = "not-a-list"
        assert CrashBlob.from_payload(payload) is None

    def test_returns_none_for_non_int_frame_element(self) -> None:
        payload = _sample_payload()
        payload["frames"] = [0xDEADBEEF, "not-an-int", 0x12345678]
        assert CrashBlob.from_payload(payload) is None

    def test_returns_none_for_frames_exceeding_max(self) -> None:
        payload = _sample_payload()
        payload["frames"] = list(range(MAX_FRAMES + 1))
        assert CrashBlob.from_payload(payload) is None

    def test_accepts_frames_at_max_boundary(self) -> None:
        payload = _sample_payload()
        payload["frames"] = list(range(MAX_FRAMES))
        blob = CrashBlob.from_payload(payload)
        assert blob is not None
        assert len(blob.frames) == MAX_FRAMES


class TestJsonRoundTrip:
    """End-to-end: payload survives stdlib ``json.dumps`` / ``json.loads``."""

    def test_json_dumps_loads_round_trips(self) -> None:
        blob = _sample_blob()
        text = json.dumps(blob.as_payload())
        assert CrashBlob.from_payload(json.loads(text)) == blob
