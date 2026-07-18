"""Tests for the compaction observability seam (R30).

Mirrors the contract encoded in grok-build's ``CompactionTarget`` +
``IntraCompactionObserver`` + ``InterCompactionObserver`` (the trait default
no-op behaviour and the ``impl … for ()`` null implementation), then adds
Python-specific guards: the bare-ABC-is-instantiable property and a
template-method subclass that records calls.
"""

from __future__ import annotations

import pytest

from minimax_code.compaction import CompactionTarget as ReexportedTarget
from minimax_code.compaction.observers import (
    NULL_INTER_OBSERVER,
    NULL_INTRA_OBSERVER,
    CompactionTarget,
    InterCompactionObserver,
    IntraCompactionObserver,
)

# =========================================================================
# CompactionTarget
# =========================================================================


@pytest.mark.parametrize(
    ("target", "label"),
    [
        (CompactionTarget.STEPS, "steps"),
        (CompactionTarget.HISTORY, "history"),
        (CompactionTarget.FULL_REPLACE, "full_replace"),
    ],
)
def test_compaction_target_label(target: CompactionTarget, label: str):
    """label() is the stable metric dimension — and matches the enum value."""
    assert target.label() == label
    assert target.value == label


def test_compaction_target_has_three_members():
    """Target (which segment, 3) ≠ Mode (how to compact, 4) — no 'combined' target."""
    members = list(CompactionTarget)
    assert len(members) == 3
    assert set(members) == {
        CompactionTarget.STEPS,
        CompactionTarget.HISTORY,
        CompactionTarget.FULL_REPLACE,
    }


def test_compaction_target_is_hashable_and_distinct():
    """Targets are usable as dict keys / set members (Copy enum in grok)."""
    mapping = {CompactionTarget.STEPS: "s", CompactionTarget.HISTORY: "h"}
    assert mapping[CompactionTarget.STEPS] == "s"
    assert CompactionTarget.STEPS is not CompactionTarget.HISTORY


def test_compaction_target_reexported_from_package():
    assert ReexportedTarget is CompactionTarget


# =========================================================================
# IntraCompactionObserver — default no-ops + null singleton
# =========================================================================


def test_intra_observer_defaults_are_noops():
    """Bare observer: every method callable, returns None, never raises."""
    obs = IntraCompactionObserver()
    assert obs.on_error("some_status") is None
    assert (
        obs.on_success(
            target=CompactionTarget.STEPS,
            tokens_before=1000,
            tokens_after=400,
            turns_compacted=5,
            elapsed=1.25,
        )
        is None
    )


def test_null_intra_observer_is_singleton_instance():
    assert isinstance(NULL_INTRA_OBSERVER, IntraCompactionObserver)
    assert NULL_INTRA_OBSERVER.on_error("x") is None
    assert (
        NULL_INTRA_OBSERVER.on_success(CompactionTarget.HISTORY, 500, 200, 3, 0.9)
        is None
    )


def test_intra_subclass_records_success_and_inherits_error():
    """A subclass overriding on_success still inherits the on_error no-op."""

    class Recorder(IntraCompactionObserver):
        def __init__(self) -> None:
            self.successes: list[tuple] = []

        def on_success(
            self,
            target: CompactionTarget,
            tokens_before: int,
            tokens_after: int,
            turns_compacted: int,
            elapsed: float,
        ) -> None:
            self.successes.append(
                (target, tokens_before, tokens_after, turns_compacted, elapsed)
            )

    rec = Recorder()
    # on_error not overridden → inherited no-op.
    assert rec.on_error("e") is None
    rec.on_success(CompactionTarget.HISTORY, 500, 200, 3, 0.9)
    assert rec.successes == [(CompactionTarget.HISTORY, 500, 200, 3, 0.9)]


# =========================================================================
# InterCompactionObserver — default no-ops + null singleton
# =========================================================================


def test_inter_observer_defaults_are_noops():
    obs = InterCompactionObserver()
    assert obs.on_recompaction("basic") is None
    assert obs.on_chunk_sampled(True, 2.5) is None
    assert obs.on_chunk_count(3) is None


def test_null_inter_observer_is_singleton_instance():
    assert isinstance(NULL_INTER_OBSERVER, InterCompactionObserver)
    assert NULL_INTER_OBSERVER.on_recompaction("divide_and_conquer") is None
    assert NULL_INTER_OBSERVER.on_chunk_sampled(False, 0.1) is None
    assert NULL_INTER_OBSERVER.on_chunk_count(0) is None


def test_inter_subclass_records_all_three_events():
    """Override all three; verify call signatures (esp. the str strategy label)."""

    class Recorder(InterCompactionObserver):
        def __init__(self) -> None:
            self.recompactions: list[str] = []
            self.samples: list[tuple[bool, float]] = []
            self.counts: list[int] = []

        def on_recompaction(self, strategy: str) -> None:
            self.recompactions.append(strategy)

        def on_chunk_sampled(self, success: bool, elapsed: float) -> None:
            self.samples.append((success, elapsed))

        def on_chunk_count(self, num_chunks: int) -> None:
            self.counts.append(num_chunks)

    rec = Recorder()
    rec.on_recompaction("basic")
    rec.on_chunk_sampled(True, 1.0)
    rec.on_chunk_sampled(False, 0.5)
    rec.on_chunk_count(2)
    assert rec.recompactions == ["basic"]
    assert rec.samples == [(True, 1.0), (False, 0.5)]
    assert rec.counts == [2]
