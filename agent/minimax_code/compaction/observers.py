"""Compaction observability seam (R30).

Ports the three seam types of grok-build's ``xai-grok-compaction`` that let
the shared compaction engine report outcomes without depending on a metrics
backend:

* ``CompactionTarget``       — which conversation segment a pass acted on
  (the stable metric label).
* ``IntraCompactionObserver`` — intra (within-turn) pass outcomes.
* ``InterCompactionObserver`` — inter (between-turn) pipeline events.

Each harness (Grok chat, grok-build) implements its own observer to emit its
own metrics; the shared crate stays backend-free. This is the compaction-side
mirror of the resilience observer stack built in R20-R21 (circuit-breaker /
retry → ``TelemetryEngine``): a future host-wiring round routes these
callbacks into the same ``TelemetryEngine`` so compaction events reach the
progress pane.

Trait → base-class mapping
--------------------------
grok traits with default no-op methods are *interfaces with default
implementations*, not pure abstract interfaces — a harness may override only
the events it cares about, and the unit type ``()`` satisfies the whole
trait by taking every default. The faithful Python analogue is a plain
base class whose methods default to no-op (the pattern used by
``logging.Handler``, ``BaseHTTPRequestHandler``, Django signal receivers):
it is instantiable without overrides, and subclasses override selectively.
We therefore do **not** inherit ``abc.ABC`` — that would force
``@abstractmethod`` and forbid the null-observer use case that grok's
``impl … for ()`` exists to serve.
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "CompactionTarget",
    "IntraCompactionObserver",
    "InterCompactionObserver",
    "NULL_INTRA_OBSERVER",
    "NULL_INTER_OBSERVER",
]


class CompactionTarget(Enum):
    """Which conversation segment a single compaction pass acts on.

    Distinct from R28's ``IntraCompactionMode`` (full_replace / steps_only /
    history_only / history_then_steps): *Mode* is the trigger policy
    ("how to compact"), *Target* is the runtime label ("which segment got
    compacted"). A ``HistoryThenSteps`` run fires two passes, each reporting
    its own Target; a single ``FullReplace`` pass reports ``FullReplace``.
    Three members, not four — there is no "combined" target.
    """

    STEPS = "steps"
    HISTORY = "history"
    FULL_REPLACE = "full_replace"

    def label(self) -> str:
        """Stable, low-cardinality metric label for this target.

        Used as the ``target`` dimension on compaction metrics and as the
        ``on_success`` argument, so observers never need the enum itself.
        """
        if self is CompactionTarget.STEPS:
            return "steps"
        if self is CompactionTarget.HISTORY:
            return "history"
        return "full_replace"


class IntraCompactionObserver:
    """Receives intra-compaction pass outcomes. All methods default to no-ops.

    Override only the events of interest; the bare class is a valid null
    observer (the Python analogue of grok's ``impl IntraCompactionObserver
    for ()``). See :data:`NULL_INTRA_OBSERVER` for the canonical instance.
    """

    def on_error(self, status: str) -> None:
        """A pass ended in an error. ``status`` is the stable, low-cardinality
        label from the error-classification helper (never a raw message)."""

    def on_success(
        self,
        target: CompactionTarget,
        tokens_before: int,
        tokens_after: int,
        turns_compacted: int,
        elapsed: float,
    ) -> None:
        """A single pass succeeded — called once per successful pass.

        ``elapsed`` is seconds (grok passes ``Duration``). A ``HistoryThenSteps``
        run calls this twice: once with ``CompactionTarget.STEPS`` then once
        with ``CompactionTarget.HISTORY`` (or vice versa).
        """


class InterCompactionObserver:
    """Receives inter-compaction pipeline events. All methods default to no-ops.

    Same rationale as :class:`IntraCompactionObserver`: the shared pipeline
    reports events; each harness emits its own metrics. Emission points and
    label values are part of the behavior contract.
    """

    def on_recompaction(self, strategy: str) -> None:
        """A prior compaction summary was found in the input (re-compaction).

        ``strategy`` is the stable label from the strategy enum's ``label()``
        (e.g. ``"basic"`` / ``"divide_and_conquer"``), passed as a plain string
        so this seam does not depend on the ``CompactionStrategy`` enum.
        """

    def on_chunk_sampled(self, success: bool, elapsed: float) -> None:
        """One chunk's LLM call finished (success or error). ``elapsed`` in seconds."""

    def on_chunk_count(self, num_chunks: int) -> None:
        """The whole pipeline finished assembling ``num_chunks`` chunk summaries."""


# Null singletons — grok's ``impl … for ()`` analogue. Tests and harnesses
# without metrics pass these to satisfy an observer parameter without
# subclassing. The bare base class is instantiable because no method is
# abstract.
NULL_INTRA_OBSERVER = IntraCompactionObserver()
NULL_INTER_OBSERVER = InterCompactionObserver()
