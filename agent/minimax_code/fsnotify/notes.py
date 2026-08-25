"""Ephemeral foreign-change notes (v1.5.1).

Bridges the fs-bus into the agent loop as an *ephemeral* note: while a
run is iterating, files written by *other* in-flight runs (or, for a
sub-agent, by the main agent) are aggregated into one short system note
appended to the LLM payload only — never merged into the persisted
message history. This mirrors the v1.1.1 nudge's wording contract: the
note must not be acknowledged in replies (acknowledgements get persisted
and pollute later turns).

The caller (``AgentCore.run``) drives this by polling ``bus.recent()``
once per iteration against a seq watermark captured at run start —
history is never replayed, and no subscribe/unsubscribe lifecycle needs
managing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — import-time only
    from collections.abc import Sequence

    from .events import FsEvent

#: Cap on files listed per note — the note stays a nudge, not a diff.
_MAX_LISTED = 8

#: Sandbox mirror prefix in emitted paths (see tools/sandbox.py).
_SANDBOX_MARKER = ".minimax/sandboxes/"


def _display_path(path: str) -> str:
    """Project a sandbox mirror path back to its workspace-relative form.

    ``<root>/.minimax/sandboxes/<run_id>/src/a.py`` reads as
    ``src/a.py (sandboxed by <run_id>)`` so the note stays meaningful
    without leaking the sandbox layout into the prompt.
    """
    normalized = path.replace("\\", "/")
    idx = normalized.find(_SANDBOX_MARKER)
    if idx == -1:
        return path
    rest = normalized[idx + len(_SANDBOX_MARKER):]
    run_id, sep, rel = rest.partition("/")
    if not run_id or not sep or not rel:
        return path
    return f"{rel} (sandboxed by {run_id})"


def foreign_change_note(
    events: Sequence[FsEvent],
    *,
    self_run_id: str | None,
    watermark: int,
) -> tuple[str | None, int]:
    """Aggregate foreign file changes into one note.

    Returns ``(note, new_watermark)``:

    * events with ``seq <= watermark`` are skipped (already seen);
    * events whose ``run_id`` attribute equals *self_run_id* are the
      caller's own writes — skipped (both ``None`` means the main agent,
      whose unattributed writes are exactly the ones to hide);
    * a sub-agent therefore *does* see the main agent's writes, and vice
      versa — the two-way awareness is the point;
    * remaining paths are de-duplicated per path (latest entry wins) and
      capped at :data:`_MAX_LISTED` entries.

    ``new_watermark`` is the highest seq observed regardless of
    filtering — skipped events must still advance the watermark or they
    would be re-scanned every iteration. When nothing foreign happened
    the note is ``None``.
    """
    max_seq = watermark
    latest: dict[str, tuple[str, str]] = {}
    for event in events:
        if event.seq <= watermark:
            continue
        if event.seq > max_seq:
            max_seq = event.seq
        if event.attribute("run_id") == self_run_id:
            continue  # our own write
        kind = getattr(event.kind, "value", str(event.kind))
        for path in event.paths:
            latest[str(path)] = (kind, event.cause)

    if not latest:
        return None, max_seq

    listed = sorted(latest.items())
    lines = [
        f"- {_display_path(path)} ({kind} via {cause})"
        for path, (kind, cause) in listed[:_MAX_LISTED]
    ]
    overflow = len(listed) - _MAX_LISTED
    if overflow > 0:
        lines.append(f"- … and {overflow} more file(s)")
    note = (
        "[system note] Files changed by other agents while you work:\n"
        + "\n".join(lines)
        + "\nThis note is informational: do not acknowledge it in your "
        "reply — if a listed path affects your task, re-read it before "
        "editing, and prefer expected_sha256 when writing."
    )
    return note, max_seq


def drain_foreign_changes(
    bus: Any,
    *,
    self_run_id: str | None,
    watermark: int | None,
    limit: int = 64,
) -> tuple[str | None, int | None]:
    """Poll *bus* once and return ``(note, new_watermark)``.

    The convenience wrapper ``AgentCore`` calls at the top of each
    iteration. ``watermark=None`` initialises from the bus's current
    maximum seq — i.e. the run starts at "now" and never replays
    history. A ``None`` bus or a failure degrades to "no note, watermark
    unchanged" (fail-open: awareness must never break the run loop).
    """
    if bus is None:
        return None, watermark
    try:
        events = bus.recent(limit=limit)
        if watermark is None:
            # First poll: initialise past everything already on the bus.
            watermark = max((e.seq for e in events), default=0)
            return None, watermark
        return foreign_change_note(
            events, self_run_id=self_run_id, watermark=watermark
        )
    except Exception:  # noqa: BLE001 — awareness must never break the loop
        return None, watermark


__all__ = ["drain_foreign_changes", "foreign_change_note"]
