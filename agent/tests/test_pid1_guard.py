"""Pure envelope validation plus fail-closed execution outside a PID namespace.

Real guard execution and fault tests require the separately controlled Linux
container harness; running a unit test must never create a local namespace.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import pytest

from minimax_code.pid1_guard import (
    GUARD_KIND,
    GUARD_SHA256,
    GUARD_SOURCE,
    MAX_DEADLINE_NS,
    PYTHON_PATH,
    build_guard_argv,
)

EXEC_ID = "a" * 32
BOOT_ID = "12345678-1234-1234-1234-123456789abc"


def build(**changes):
    return build_guard_argv(**{"exec_id": EXEC_ID, "boot_id": BOOT_ID,
                              "deadline_boottime_ns": MAX_DEADLINE_NS,
                              "argv": ["/bin/echo", "资料", ""], **changes})


@pytest.fixture(scope="module")
def decode():
    namespace = {"__name__": "guard_decode_test"}
    exec(compile(GUARD_SOURCE, "<fixed-guard>", "exec"), namespace)
    return namespace["_decode"]


def test_fixed_source_hash_entrypoint_and_canonical_envelope(decode):
    args = build()
    assert args[:4] == (PYTHON_PATH, "-I", "-S", "-c")
    assert args[4] == GUARD_SOURCE
    assert GUARD_KIND == "pid1-boottime-v1"
    assert hashlib.sha256(args[4].encode()).hexdigest() == GUARD_SHA256
    assert len(GUARD_SHA256) == 64
    value = decode(args[5])
    assert value == {"exec_id": EXEC_ID, "boot_id": BOOT_ID,
                     "deadline_boottime_ns": MAX_DEADLINE_NS,
                     "argv": ["/bin/echo", "资料", ""]}
    assert args[5] == json.dumps(value, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=False, allow_nan=False)
    assert len(args) == 6


@pytest.mark.parametrize("changes", [
    {"exec_id": None}, {"exec_id": "A" * 32}, {"exec_id": "../outside"},
    {"boot_id": "not-a-boot"}, {"boot_id": BOOT_ID.upper()},
    {"deadline_boottime_ns": True}, {"deadline_boottime_ns": 1.0},
    {"deadline_boottime_ns": 0}, {"deadline_boottime_ns": -1},
    {"deadline_boottime_ns": 1 << 63}, {"argv": "echo"}, {"argv": []},
    {"argv": [""]}, {"argv": ["/bin/echo", None]},
    {"argv": ["/bin/echo", "nul\0byte"]},
    {"argv": ["/bin/echo", "x" * 4097]},
    {"argv": ["/bin/echo", "字" * 1400]},
    {"argv": ["/bin/echo", "\ud800"]},
    {"argv": ["/bin/echo", *(["x"] * 100)]},
    {"argv": ["/bin/echo", *(["x" * 4096] * 20)]},
])
def test_builder_rejects_incompatible_or_unbounded_input(changes):
    with pytest.raises(ValueError):
        build(**changes)


@pytest.mark.parametrize("raw", [
    "null", "[]", "{}", "{bad", "x" * 65537,
    '{"exec_id":"' + EXEC_ID + '","exec_id":"' + EXEC_ID + '"}',
], ids=["null", "array", "missing-fields", "invalid-json", "oversize", "duplicate-field"])
def test_guard_rejects_raw_invalid_and_duplicate_input(decode, raw):
    with pytest.raises(ValueError):
        decode(raw)


@pytest.mark.parametrize("field,value", [
    ("unexpected", "extension"), ("deadline_boottime_ns", True),
    ("deadline_boottime_ns", float("nan")), ("deadline_boottime_ns", float("inf")),
    ("deadline_boottime_ns", 1 << 63), ("argv", ["/bin/echo", 3]),
    ("argv", [""]), ("argv", ["/bin/echo", "a\0b"]),
    ("exec_id", "A" * 32), ("boot_id", BOOT_ID.upper()),
])
def test_guard_independently_rejects_direct_envelope_changes(decode, field, value):
    envelope = json.loads(build()[-1])
    envelope[field] = value
    with pytest.raises(ValueError):
        decode(json.dumps(envelope))


def test_plain_host_execution_fails_without_running_payload(tmp_path):
    canary = tmp_path / "must-not-exist"
    payload = [sys.executable, "-c", "from pathlib import Path;Path(__import__('sys').argv[1]).touch()",
               str(canary)]
    envelope = build(argv=payload)[-1]
    # The test subprocess owns no namespace and must fail before any fork or
    # Linux-specific mutation, including on Windows and non-Linux hosts.
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "PATH") if key in os.environ}
    result = subprocess.run([sys.executable, "-I", "-S", "-c", GUARD_SOURCE, envelope],
                            cwd=tmp_path, env=env, stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=5)
    assert result.returncode == 125
    assert result.stdout == result.stderr == b""
    assert not canary.exists()
