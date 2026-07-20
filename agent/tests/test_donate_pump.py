"""Tests for ``minimax_code.computer_hub_sdk.donate_pump`` (R136).

Mirrors grok-build's ``xai-computer-hub-sdk/src/donate_pump.rs`` test suite
(the two ``#[tokio::test]`` cases) plus a small unit cover of the OTLP wire
helpers, which the Rust suite takes from the proto crate rather than
exercising directly.

The two pump cases pin the two observable contracts the donation clients
depend on:

* :func:`test_pump_retries_failed_payloads_across_reconnect` -- the pump
  retains payloads across a failed-send window and drains them in order once
  the link recovers, acking each barrier even while down;
* :func:`test_pump_retry_buffer_drops_oldest_beyond_cap` -- the retry buffer
  is bounded at :data:`RETRY_CAP`, evicting the OLDEST payload on overflow
  (FIFO drop, newest retained).

The donate closure is faked with an asyncio-captured ``healthy`` flag +
``sent`` list -- single-threaded asyncio needs no ``AtomicBool`` /
``Mutex<Vec<String>>`` the way tokio's multi-thread runtime does. The pump
is driven as an :class:`asyncio.Task` over an :class:`asyncio.Queue`,
mirroring the Rust ``tokio::spawn(run_pump(..))``; :data:`_CLOSE` stands in
for ``drop(tx)`` (see the module docstring's channel-close section).
"""

from __future__ import annotations

import asyncio

from minimax_code.computer_hub_sdk.donate_pump import (
    _CLOSE,
    RETRY_CAP,
    OtlpAnyValue,
    OtlpResource,
    _PayloadMsg,
    drain_via,
    make_resource,
    now_unix_nanos,
    run_pump,
    string_kv,
    string_value,
)


# ---------------------------------------------------------------------------
# OTLP helpers -- pure wire-shape unit cover.
# ---------------------------------------------------------------------------
def test_now_unix_nanos_is_nonneg_and_nondecreasing() -> None:
    a = now_unix_nanos()
    b = now_unix_nanos()
    assert a >= 0
    # wall-clock is monotonic enough across two adjacent reads.
    assert b >= a


def test_string_value_emits_stringvalue_tag() -> None:
    assert string_value("hello").value == {"stringValue": "hello"}


def test_string_kv_pairs_key_and_value() -> None:
    kv = string_kv("service.name", "grok")
    assert kv.key == "service.name"
    assert kv.value == OtlpAnyValue(value={"stringValue": "grok"})


def test_make_resource_carries_service_name_only() -> None:
    res = make_resource("agent")
    assert isinstance(res, OtlpResource)
    assert len(res.attributes) == 1
    assert res.attributes[0].key == "service.name"
    assert res.attributes[0].value == OtlpAnyValue(value={"stringValue": "agent"})


# ---------------------------------------------------------------------------
# Donate-closure factory.
#
# `healthy` / `sent` are captured as single-element lists so the closure can
# flip health between drains. asyncio's single-threaded model makes a plain
# list safe where the Rust suite needed Arc<AtomicBool> + Arc<Mutex<Vec>>.
# ---------------------------------------------------------------------------
def _make_donate(healthy: list[bool], sent: list[str]):
    """Return a donate closure reading ``healthy[0]`` and appending to ``sent``."""

    async def donate(payload: str) -> tuple[bool, str]:
        if healthy[0]:
            sent.append(payload)
            return True, payload
        return False, payload

    return donate


def _payload(tag: int) -> _PayloadMsg:
    return _PayloadMsg(f"payload-{tag}")


# ---------------------------------------------------------------------------
# pump_retries_failed_payloads_across_reconnect
# ---------------------------------------------------------------------------
async def test_pump_retries_failed_payloads_across_reconnect() -> None:
    healthy = [False]
    sent: list[str] = []
    tx: asyncio.Queue = asyncio.Queue(maxsize=8)
    pump = asyncio.create_task(run_pump(tx, _make_donate(healthy, sent)))

    await tx.put(_payload(1))
    await tx.put(_payload(2))
    await drain_via(tx)
    assert sent == [], "nothing sent while link is down"

    healthy[0] = True
    await drain_via(tx)
    assert sent == ["payload-1", "payload-2"]

    await tx.put(_CLOSE)
    await pump


# ---------------------------------------------------------------------------
# pump_retry_buffer_drops_oldest_beyond_cap
# ---------------------------------------------------------------------------
async def test_pump_retry_buffer_drops_oldest_beyond_cap() -> None:
    healthy = [False]
    sent: list[str] = []
    # capacity = RETRY_CAP + 2 so the RETRY_CAP+1 payloads never block on put.
    tx: asyncio.Queue = asyncio.Queue(maxsize=RETRY_CAP + 2)
    pump = asyncio.create_task(run_pump(tx, _make_donate(healthy, sent)))

    for i in range(RETRY_CAP + 1):  # 0..=RETRY_CAP inclusive -> RETRY_CAP+1 payloads
        await tx.put(_payload(i + 1))
    await drain_via(tx)

    healthy[0] = True
    await drain_via(tx)
    assert len(sent) == RETRY_CAP, "buffer bounded at RETRY_CAP"
    assert sent[0] == "payload-2", "oldest payload evicted first"

    await tx.put(_CLOSE)
    await pump
