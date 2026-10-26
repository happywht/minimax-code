"""Immutable boot deadlines and current-only start targets, without Podman."""
from __future__ import annotations

import math

import pytest

from minimax_code import process_deadline
from minimax_code.process_binding import validate_target
from minimax_code.process_types import ExecutionTargetSpec, ProcessBoundaryError, canonical_hash


def test_boottime_deadline_is_fixed_once_and_wall_clock_independent(monkeypatch):
    boot = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    clock = [1_000_000_000]
    monkeypatch.setattr(process_deadline, "read_clock", lambda: (boot, clock[0]))
    identity, deadline = process_deadline.new_deadline(3)
    row = {"deadline_boot_id": identity, "deadline_boottime_ns": deadline, "deadline": -math.inf}
    assert process_deadline.remaining_seconds(row) == 3
    clock[0] += 2_500_000_000
    assert process_deadline.remaining_seconds(row) == 0.5
    clock[0] += 1_000_000_000  # Simulated suspend includes elapsed BOOTTIME.
    assert process_deadline.remaining_seconds(row) == 0
    assert row["deadline_boottime_ns"] == deadline


@pytest.mark.parametrize("damage", [{}, {"deadline_boot_id": "another", "deadline_boottime_ns": 100},
    {"deadline_boot_id": "boot", "deadline_boottime_ns": True},
    {"deadline_boot_id": "boot", "deadline_boottime_ns": 1 << 63}])
def test_missing_or_changed_boot_deadline_is_cleanup_only(monkeypatch, damage):
    monkeypatch.setattr(process_deadline, "read_clock", lambda: ("boot", 10))
    with pytest.raises(ProcessBoundaryError, match="cleanup only"):
        process_deadline.remaining_seconds(damage)


@pytest.mark.parametrize("field", ["version", "policy_version"])
@pytest.mark.parametrize("value", [True, False, 1.0, 2.0])
def test_target_header_rejects_numeric_aliases(field, value):
    target = ExecutionTargetSpec("linux-podman-l1", "sha256:" + "a" * 64).to_dict()
    target[field] = value
    with pytest.raises(ProcessBoundaryError):
        validate_target(target)


def test_old_policy_or_missing_governor_cannot_be_started():
    target = ExecutionTargetSpec("linux-podman-l1", "sha256:" + "a" * 64).to_dict()
    target.pop("governor")
    with pytest.raises(ProcessBoundaryError):
        validate_target(target)
    target["policy_version"] = 1
    with pytest.raises(ProcessBoundaryError):
        validate_target(target)


def test_governor_is_immutable_and_hash_contains_each_asset():
    from dataclasses import FrozenInstanceError, replace
    target = ExecutionTargetSpec("linux-podman-l1", "sha256:" + "a" * 64)
    with pytest.raises(FrozenInstanceError):
        target.governor.guard_sha256 = "b" * 64
    changed = replace(target, governor=replace(target.governor, guard_sha256="b" * 64))
    assert target.fingerprint != changed.fingerprint


def test_canonical_hash_rejects_nonfinite_numbers():
    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            canonical_hash({"value": value})
