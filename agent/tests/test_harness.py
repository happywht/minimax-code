"""Tests for ``computer_hub_sdk.harness_types`` (R165, harness.rs leaf 1).

Covers the dependency-free vocabulary ported in R165: the two well-known-
kind constants, the three callback type-aliases, the ``CancelOnDrop``
opt-in flag newtype, and the ``SessionBindReport`` typed bind-contract
report. The harness actor itself (``ToolHarness`` / ``LocalRegistry`` /
``ToolHarnessBuilder``) lands in a later leaf and is not exercised here.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

from minimax_code.computer_hub_sdk.harness_types import (
    PERMISSION_REQUEST_KIND,
    PROGRESS_BUFFER,
    CancelOnDrop,
    HookRequestHandler,
    ModelOutputExtractor,
    SessionBindReport,
    TraceContextProvider,
)


def _stub_trace_provider() -> str | None:
    """Stand-in satisfying the ``() -> str | None`` traceparent shape."""
    return "traceparent"


# ===========================================================================
# Constants.
# ===========================================================================
def test_permission_request_kind_well_known_value():
    """PERMISSION_REQUEST_KIND is the server->harness permission hook kind."""
    assert PERMISSION_REQUEST_KIND == "permission_request"
    assert isinstance(PERMISSION_REQUEST_KIND, str)


def test_progress_buffer_is_64():
    """PROGRESS_BUFFER matches the per-call progress channel capacity."""
    assert PROGRESS_BUFFER == 64
    assert isinstance(PROGRESS_BUFFER, int)


# ===========================================================================
# CancelOnDrop newtype.
# ===========================================================================
def test_cancel_on_drop_positional_construct_and_value_access():
    """Rust tuple-struct positional construction mirrored; .value readable."""
    assert CancelOnDrop(True).value is True
    assert CancelOnDrop(False).value is False


def test_cancel_on_drop_is_frozen():
    """frozen mirrors Clone+Copy: post-construction mutation is rejected."""
    flag = CancelOnDrop(True)
    try:
        flag.value = False  # type: ignore[misc]
    except FrozenInstanceError:
        return
    raise AssertionError("CancelOnDrop must be frozen (Clone+Copy semantics)")


def test_cancel_on_drop_equality_and_hash():
    """frozen -> hashable + value equality (mirrors Rust Copy eq)."""
    assert CancelOnDrop(True) == CancelOnDrop(True)
    assert CancelOnDrop(True) != CancelOnDrop(False)
    assert hash(CancelOnDrop(True)) == hash(CancelOnDrop(True))


# ===========================================================================
# SessionBindReport dataclass.
# ===========================================================================
def test_session_bind_report_defaults_match_rust_default():
    """Field defaults reproduce Rust #[derive(Default)]."""
    report = SessionBindReport()
    assert report.binary_version is None
    assert report.unserved_tool_ids == []
    assert report.resolve_error is None


def test_session_bind_report_default_list_is_per_instance():
    """default_factory=list gives each instance its own list (no shared alias)."""
    a = SessionBindReport()
    b = SessionBindReport()
    a.unserved_tool_ids.append("tool-a")
    assert b.unserved_tool_ids == []


def test_session_bind_report_explicit_construction():
    """All three fields accept explicit values."""
    report = SessionBindReport(
        binary_version="1.2.3",
        unserved_tool_ids=["x", "y"],
        resolve_error="boom",
    )
    assert report.binary_version == "1.2.3"
    assert report.unserved_tool_ids == ["x", "y"]
    assert report.resolve_error == "boom"


# ===========================================================================
# Callback type-aliases.
# ===========================================================================
def test_callback_aliases_assignable_to_callable_shape():
    """Aliases resolve to Callable forms usable in signatures / annotations."""
    provider: TraceContextProvider = _stub_trace_provider
    assert provider() == "traceparent"
    # HookRequestHandler / ModelOutputExtractor are Callable-parameterised
    # forms; binding them as annotations must not raise.
    _handler: HookRequestHandler  # noqa: F841
    _extractor: ModelOutputExtractor  # noqa: F841
