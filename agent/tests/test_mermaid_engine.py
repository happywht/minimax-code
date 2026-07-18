"""Tests for the mermaid render guard layer (R38).

Mirrors grok's ``xai-grok-mermaid`` engine tests (``passes_through_engine_success``,
``oversized_source_rejected_before_engine_runs``, ``source_at_limit_is_accepted``,
``panicking_engine_becomes_panic_error``, ``engine_errors_pass_through_unchanged``,
``error_display_is_descriptive``) plus Python-specific assertions: byte-length
size semantics (Rust ``str::len`` vs Python ``len``), exception-chain
preservation (``__cause__``), and that ``BaseException`` subclasses are not
caught (grok ``catch_unwind`` does not intercept ``abort``).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code.mermaid import (
    MermaidEngine,
    MermaidError,
    MermaidLayoutError,
    MermaidPanicError,
    MermaidParseError,
    MermaidRasterizeError,
    MermaidTimeoutError,
    MermaidUnsupportedError,
    RenderedDiagram,
    RenderLimits,
    RenderParams,
    render_checked,
)
from minimax_code.mermaid.engine import (
    MermaidEngine as MermaidEngineFromModule,
)
from minimax_code.mermaid.engine import (
    RenderLimits as RenderLimitsFromModule,
)
from minimax_code.mermaid.engine import render_checked as render_checked_from_module


def test_reexport():
    assert RenderLimits is RenderLimitsFromModule
    assert MermaidEngine is MermaidEngineFromModule
    assert render_checked is render_checked_from_module


# --- helpers ---------------------------------------------------------------


def _ok_diagram() -> RenderedDiagram:
    return RenderedDiagram(png=b"\x01\x02\x03", width_px=10, height_px=20)


class _RecordingEngine:
    """Records whether ``render`` was invoked, to prove the early size check
    short-circuits before the engine runs (grok ``SpyEngine``)."""

    def __init__(self, outcome):
        self.called = False
        self._outcome = outcome

    def render(self, source, params):  # noqa: ARG002 — matches the protocol shape
        self.called = True
        return self._outcome()


class _OkEngine(_RecordingEngine):
    def __init__(self):
        super().__init__(_ok_diagram)


class _RaisingEngine:
    """Raises a fixed exception factory on every render."""

    def __init__(self, make):
        self._make = make

    def render(self, source, params):  # noqa: ARG002
        raise self._make()


class _PanickingEngine:
    """Raises a non-MermaidError exception (the "panic" channel)."""

    def __init__(self, exc):
        self._exc = exc

    def render(self, source, params):  # noqa: ARG002
        raise self._exc


# --- RenderLimits ----------------------------------------------------------


def test_render_limits_default_is_64kib():
    assert RenderLimits().max_source_bytes == 64 * 1024


def test_render_limits_value_equality():
    assert RenderLimits() == RenderLimits()
    assert RenderLimits(max_source_bytes=8) == RenderLimits(max_source_bytes=8)
    assert RenderLimits(max_source_bytes=8) != RenderLimits(max_source_bytes=9)


def test_render_limits_is_frozen():
    limits = RenderLimits()
    with pytest.raises(FrozenInstanceError):
        limits.max_source_bytes = 0  # type: ignore[misc]


# --- MermaidEngine protocol ------------------------------------------------


def test_engine_protocol_is_runtime_checkable():
    """``@runtime_checkable`` so isinstance dispatches on duck-typed engines."""
    assert isinstance(_OkEngine(), MermaidEngine)
    assert not isinstance(object(), MermaidEngine)


# --- render_checked: success path -----------------------------------------


def test_passes_through_engine_success():
    """Mirrors grok ``passes_through_engine_success``."""
    engine = _OkEngine()
    out = render_checked(engine, "flowchart LR; A-->B", RenderParams(), RenderLimits())
    assert out.width_px == 10
    assert engine.called is True


# --- render_checked: size cap ---------------------------------------------


def test_oversized_source_rejected_before_engine_runs():
    """Mirrors grok ``oversized_source_rejected_before_engine_runs``: the engine
    is never called when source exceeds the cap (the outcome would panic, which
    would surface as ``Panic`` not ``Unsupported`` if it ran)."""
    engine = _RecordingEngine(outcome=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    limits = RenderLimits(max_source_bytes=8)
    with pytest.raises(MermaidUnsupportedError):
        render_checked(
            engine,
            "this source is definitely longer than eight bytes",
            RenderParams(),
            limits,
        )
    assert engine.called is False, "engine must not run when the source is over the limit"


def test_oversized_error_message_reports_byte_count():
    limits = RenderLimits(max_source_bytes=8)
    with pytest.raises(MermaidUnsupportedError) as exc_info:
        render_checked(engine=_OkEngine(), source="x" * 20, params=RenderParams(), limits=limits)
    msg = str(exc_info.value)
    assert "20 bytes" in msg
    assert "8-byte limit" in msg


def test_source_at_limit_is_accepted():
    """Mirrors grok ``source_at_limit_is_accepted``: exactly-at-cap is OK."""
    engine = _OkEngine()
    src = "12345678"  # 8 bytes
    limits = RenderLimits(max_source_bytes=8)
    assert render_checked(engine, src, RenderParams(), limits).width_px == 10


def test_size_cap_uses_byte_length_not_char_count():
    """Rust ``str::len()`` counts bytes; a multi-byte UTF-8 char that fits in
    the char budget but exceeds the byte budget must be rejected."""
    limits = RenderLimits(max_source_bytes=3)
    # ASCII at the cap is accepted.
    assert render_checked(_OkEngine(), "abc", RenderParams(), limits).width_px == 10
    # "ä" is 2 UTF-8 bytes — under the cap, accepted.
    assert render_checked(_OkEngine(), "ä", RenderParams(), limits).width_px == 10
    # "äb" is 3 UTF-8 bytes (2 + 1) — at the cap, accepted.
    assert render_checked(_OkEngine(), "äb", RenderParams(), limits).width_px == 10
    # "äbc" is 4 UTF-8 bytes — over the cap, rejected even though it's 3 chars.
    with pytest.raises(MermaidUnsupportedError):
        render_checked(_OkEngine(), "äbc", RenderParams(), limits)


# --- render_checked: panic isolation --------------------------------------


def test_panicking_engine_becomes_panic_error():
    """Mirrors grok ``panicking_engine_becomes_panic_error``: a non-MermaidError
    exception is wrapped as ``MermaidPanicError`` carrying its message."""
    err = render_checked_raising(
        _PanickingEngine(RuntimeError("boom in layout")),
        "flowchart LR; A-->B",
    )
    assert isinstance(err, MermaidPanicError)
    assert "boom in layout" in str(err)


def test_panic_error_preserves_cause_chain():
    """The original exception is chained as ``__cause__`` (``raise … from exc``)."""
    original = ValueError("underlying")
    with pytest.raises(MermaidPanicError) as exc_info:
        render_checked(_PanickingEngine(original), "x", RenderParams(), RenderLimits())
    assert exc_info.value.__cause__ is original


def test_base_exception_is_not_caught():
    """grok ``catch_unwind`` does not intercept ``abort``; Python's analogue must
    not swallow ``BaseException`` subclasses (KeyboardInterrupt/SystemExit)."""
    with pytest.raises(KeyboardInterrupt):
        render_checked(
            _PanickyOnBase(KeyboardInterrupt()),
            "x",
            RenderParams(),
            RenderLimits(),
        )


class _PanickyOnBase:
    def __init__(self, exc):
        self._exc = exc

    def render(self, source, params):  # noqa: ARG002
        raise self._exc


# --- render_checked: expected errors pass through -------------------------


def test_engine_errors_pass_through_unchanged():
    """Mirrors grok ``engine_errors_pass_through_unchanged``: every MermaidError
    subclass passes through with its exact type and payload, not merely 'not Panic'."""
    cases = [
        (lambda: MermaidParseError("p-payload"), MermaidParseError),
        (lambda: MermaidLayoutError("l-payload"), MermaidLayoutError),
        (lambda: MermaidRasterizeError("r-payload"), MermaidRasterizeError),
        (lambda: MermaidTimeoutError(), MermaidTimeoutError),
        (lambda: MermaidUnsupportedError("u-payload"), MermaidUnsupportedError),
    ]
    for make, expected_type in cases:
        err = render_checked_raising(_RaisingEngine(make), "x")
        assert isinstance(err, expected_type), f"variant changed: got {type(err)!r}"
        # Payload round-trips verbatim (the message property is unchanged).
        if expected_type is not MermaidTimeoutError:
            assert err.message == make().message


def test_all_error_subclasses_are_mermaid_errors():
    for exc in [
        MermaidParseError("x"),
        MermaidLayoutError("x"),
        MermaidRasterizeError("x"),
        MermaidTimeoutError(),
        MermaidUnsupportedError("x"),
        MermaidPanicError("x"),
    ]:
        assert isinstance(exc, MermaidError)


# --- error Display (str) ---------------------------------------------------


def test_error_display_is_descriptive():
    """Mirrors grok ``error_display_is_descriptive``: each variant's str carries a
    distinguishing word, and payload variants interpolate their carried message."""
    assert "timed out" in str(MermaidTimeoutError())
    for err, word in [
        (MermaidParseError("PL"), "parse"),
        (MermaidLayoutError("PL"), "layout"),
        (MermaidRasterizeError("PL"), "rasterize"),
        (MermaidUnsupportedError("PL"), "unsupported"),
        (MermaidPanicError("PL"), "panicked"),
    ]:
        s = str(err)
        assert word in s, f"{s!r} missing {word!r}"
        assert "PL" in s, f"{s!r} missing interpolated payload"


# --- internal helper -------------------------------------------------------


def render_checked_raising(engine, source):
    """Call render_checked and return the raised MermaidError (fail-loud if it doesn't)."""
    with pytest.raises(MermaidError) as exc_info:
        render_checked(engine, source, RenderParams(), RenderLimits())
    return exc_info.value
