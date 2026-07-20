"""Tests for ``minimax_code.tracing.dispatch.dispatcher_active`` (R128).

Mirrors grok-build's ``xai-tracing/src/dispatch.rs`` in-memory tests
plus the Python-specific consumer-presence semantics. ``dispatcher_active``
reads the *root* logger's handler list via :func:`logging.getLogger`, so
the suite swaps :func:`logging.getLogger` to return a probe logger we
fully control (:func:`fake_root`). Clearing the *real* root logger's
handlers is not a reliable isolation strategy: pytest's logging plugin
installs its own capture handler there for the duration of the session,
so it reappears. Patching ``getLogger`` sidesteps that entirely —
``dispatcher_active`` calls ``logging.getLogger()`` internally, so it
reads the probe.

The suite asserts: inactive with no handlers; active with a real handler;
inactive with only a :class:`logging.NullHandler` (the ``NoSubscriber``
counterpart); active when a real handler sits alongside a
:class:`~logging.NullHandler`; that the *level* is irrelevant (handler
presence is the signal); that the read is dynamic (wiring a handler
mid-flight flips the result); and that a handler on a *named* child
logger does not satisfy the global check.
"""

from __future__ import annotations

import logging

import pytest

from minimax_code.tracing.dispatch import dispatcher_active


@pytest.fixture
def fake_root(monkeypatch: pytest.MonkeyPatch) -> logging.Logger:
    """A probe logger standing in for the root, via ``getLogger`` patch.

    pytest's logging plugin installs its own capture handler on the
    *real* root logger for the capture machinery, so clearing the real
    root's handlers is not a reliable way to isolate ``dispatcher_active``
    (the capture handler reappears). Instead we swap
    :func:`logging.getLogger` to return a fresh logger we fully control,
    and :func:`dispatcher_active` — which calls ``logging.getLogger()``
    internally — reads it. ``monkeypatch`` restores the real
    :func:`logging.getLogger` at teardown.

    Note: pytest's logging plugin reaches the probe *through the patch*
    (it calls ``logging.getLogger()`` to grab "the root" and attaches a
    ``LogCaptureHandler``). So each test re-clears the probe's handlers
    at the top of its body before asserting — see ``_clear()``.
    """
    probe = logging.getLogger(".__dispatch_probe_root__")

    def _clear() -> None:
        """Drop every handler pytest's logging plugin may have attached."""
        probe.handlers.clear()

    # expose the clearer on the probe so tests call fake_root._clear() at top
    probe._clear = _clear  # type: ignore[attr-defined]
    monkeypatch.setattr(logging, "getLogger", lambda *args, **kwargs: probe)
    return probe


# ---------------------------------------------------------------------------
# Inactive: no consumer wired (Rust without_dispatcher_inactive)
# ---------------------------------------------------------------------------


def test_dispatcher_inactive_when_root_has_no_handlers(fake_root: logging.Logger) -> None:
    fake_root._clear()  # type: ignore[attr-defined]
    assert dispatcher_active() is False


def test_dispatcher_inactive_with_only_null_handler(fake_root: logging.Logger) -> None:
    fake_root._clear()  # type: ignore[attr-defined]
    # NullHandler is the logging idiom for "no output configured" — the
    # counterpart of Rust's NoSubscriber sentinel, so it must NOT count.
    fake_root.addHandler(logging.NullHandler())
    assert dispatcher_active() is False


# ---------------------------------------------------------------------------
# Active: a real consumer is wired (Rust scoped_dispatcher_active)
# ---------------------------------------------------------------------------


def test_dispatcher_active_when_stream_handler_present(fake_root: logging.Logger) -> None:
    fake_root._clear()  # type: ignore[attr-defined]
    fake_root.addHandler(logging.StreamHandler())
    assert dispatcher_active() is True


def test_dispatcher_active_with_null_plus_real_handler(fake_root: logging.Logger) -> None:
    fake_root._clear()  # type: ignore[attr-defined]
    # a real handler sitting alongside a NullHandler still satisfies the
    # check — only one real consumer is needed
    fake_root.addHandler(logging.NullHandler())
    fake_root.addHandler(logging.StreamHandler())
    assert dispatcher_active() is True


# ---------------------------------------------------------------------------
# Level is irrelevant — this is a consumer-presence check
# ---------------------------------------------------------------------------


def test_dispatcher_reads_handlers_not_level(fake_root: logging.Logger) -> None:
    fake_root._clear()  # type: ignore[attr-defined]
    # a handler is wired but the root level is NOTSET (the most permissive
    # placeholder) — the check still sees the consumer and returns True
    fake_root.setLevel(logging.NOTSET)
    fake_root.addHandler(logging.StreamHandler())
    assert dispatcher_active() is True


def test_dispatcher_inactive_even_at_debug_level_without_handlers(
    fake_root: logging.Logger,
) -> None:
    fake_root._clear()  # type: ignore[attr-defined]
    # conversely, DEBUG level alone (a permissive level) with no handler
    # means no consumer is wired
    fake_root.setLevel(logging.DEBUG)
    assert dispatcher_active() is False


# ---------------------------------------------------------------------------
# The read is dynamic — wiring/removing handlers mid-flight
# ---------------------------------------------------------------------------


def test_dispatcher_is_dynamic_across_handler_changes(fake_root: logging.Logger) -> None:
    fake_root._clear()  # type: ignore[attr-defined]
    # mirrors the Rust scoped test's "active inside, inactive outside":
    # wiring a handler flips True, removing it flips back, no caching
    assert dispatcher_active() is False

    handler = logging.StreamHandler()
    fake_root.addHandler(handler)
    assert dispatcher_active() is True

    fake_root.removeHandler(handler)
    assert dispatcher_active() is False


# ---------------------------------------------------------------------------
# Global scope only — a named child logger's handler does not count
# ---------------------------------------------------------------------------


def test_dispatcher_ignores_named_child_logger_handlers(
    fake_root: logging.Logger,
) -> None:
    fake_root._clear()  # type: ignore[attr-defined]
    # handlers on a named child logger are NOT inspected — this answers
    # the global question only (see the module docstring's scope note).
    # Construct the child directly (logging.Logger, not getLogger) so the
    # patched getLogger does not redirect it to the probe; the child's
    # handler must stay invisible to dispatcher_active.
    child = logging.Logger(".__dispatch_probe_child__")
    child_handler = logging.StreamHandler()
    child.addHandler(child_handler)
    try:
        # the probe (standing in for root) still has no real handler, so
        # the global check is False even though a child logger carries one
        assert dispatcher_active() is False
    finally:
        child.removeHandler(child_handler)
