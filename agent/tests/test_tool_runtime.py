"""Tests for the R107 tool_runtime error module.

Covers:

- ``ToolErrorKind`` StrEnum values (snake_case = ``as_str``) for all 19
  variants
- ``ToolError`` core constructor + ``with_details`` / ``with_source``
  builders
- all 17 static kind constructors (kind + detail + details injection)
- ``variant_name`` / ``__str__`` / ``__repr__`` / ``from_json_error``
- ``to_wire`` projection for all 19 kinds (9 typed variants + 9
  Custom-subcoded + the Custom-details arm), including ``tool_id`` /
  ``elapsed_ms`` / ``requested`` / ``card_id`` extraction from details
- ``_custom_details_with_code`` merge semantics (None / non-dict
  pass-through / existing-code-wins / subcode-merge / no-mutation)

The four wire-bridge properties are ported verbatim from the Rust
``wire_bridge_tests`` module (``error.rs``): ``service_unavailable``
details survive the projection, the five rate/usage kinds merge the
subcode uniformly, an existing ``code`` key wins, and None / non-object
details pass through unchanged.
"""

from __future__ import annotations

import json

import pytest

from minimax_code.tool_protocol.error_wire import (
    BehaviorVersionUnsupported as BehaviorVersionUnsupportedWire,
)
from minimax_code.tool_protocol.error_wire import (
    Cancelled as CancelledWire,
)
from minimax_code.tool_protocol.error_wire import (
    Custom as CustomWire,
)
from minimax_code.tool_protocol.error_wire import (
    Execution as ExecutionWire,
)
from minimax_code.tool_protocol.error_wire import (
    InvalidArguments as InvalidArgumentsWire,
)
from minimax_code.tool_protocol.error_wire import (
    PermissionDenied as PermissionDeniedWire,
)
from minimax_code.tool_protocol.error_wire import (
    RenderLimited as RenderLimitedWire,
)
from minimax_code.tool_protocol.error_wire import (
    TerminalError as TerminalErrorWire,
)
from minimax_code.tool_protocol.error_wire import (
    Timeout as TimeoutWire,
)
from minimax_code.tool_protocol.error_wire import (
    ToolNotFound as ToolNotFoundWire,
)
from minimax_code.tool_protocol.ids import ToolId
from minimax_code.tool_runtime import ToolError, ToolErrorKind
from minimax_code.tool_runtime.error import _custom_details_with_code

# ---------------------------------------------------------------------------
# ToolErrorKind
# ---------------------------------------------------------------------------


def test_kind_values_are_snake_case_as_str_tags():
    cases = {
        ToolErrorKind.NOT_IMPLEMENTED: "not_implemented",
        ToolErrorKind.INVALID_ARGUMENTS: "invalid_arguments",
        ToolErrorKind.NOT_FOUND: "not_found",
        ToolErrorKind.PERMISSION_DENIED: "permission_denied",
        ToolErrorKind.UNAUTHORIZED: "unauthorized",
        ToolErrorKind.TIMEOUT: "timeout",
        ToolErrorKind.CANCELLED: "cancelled",
        ToolErrorKind.RATE_LIMITED: "rate_limited",
        ToolErrorKind.USAGE_POOL_EXHAUSTED: "usage_pool_exhausted",
        ToolErrorKind.USAGE_LIMIT_REACHED: "usage_limit_reached",
        ToolErrorKind.GLOBAL_RATE_LIMIT: "global_rate_limit",
        ToolErrorKind.CONCURRENCY_LIMIT: "concurrency_limit",
        ToolErrorKind.SERVICE_UNAVAILABLE: "service_unavailable",
        ToolErrorKind.NETWORK_ERROR: "network_error",
        ToolErrorKind.EXECUTION: "execution",
        ToolErrorKind.BEHAVIOR_VERSION_UNSUPPORTED: "behavior_version_unsupported",
        ToolErrorKind.RENDER_LIMITED: "render_limited",
        ToolErrorKind.TERMINAL_ERROR: "terminal_error",
        ToolErrorKind.CUSTOM: "custom",
    }
    assert len(cases) == 19
    for kind, tag in cases.items():
        assert kind.value == tag
        assert kind.as_str() == tag
        # StrEnum: str(member) is the value (the metrics/log tag).
        assert str(kind) == tag


def test_kind_is_str_enum_for_tag_use():
    # StrEnum members ARE str — usable directly as the snake_case tag
    # wherever the crate passes `kind.as_str()` to a string field.
    assert isinstance(ToolErrorKind.TIMEOUT, str)


# ---------------------------------------------------------------------------
# Core constructor + builders
# ---------------------------------------------------------------------------


def test_new_sets_required_fields_only():
    e = ToolError.new(ToolErrorKind.TIMEOUT, "ran out")
    assert e.kind is ToolErrorKind.TIMEOUT
    assert e.detail == "ran out"
    assert e.source is None
    assert e.details is None


def test_with_details_builder_returns_self_for_chaining():
    e = ToolError.rate_limited("slow").with_details({"retry_after_ms": 250})
    assert e.details == {"retry_after_ms": 250}


def test_with_source_builder_attaches_cause():
    cause = RuntimeError("upstream")
    e = ToolError.execution(ToolId("fs:read"), "boom").with_source(cause)
    assert e.source is cause


# ---------------------------------------------------------------------------
# Static constructors (kind + detail + details injection)
# ---------------------------------------------------------------------------


def test_simple_constructors_set_kind_and_detail():
    assert ToolError.not_implemented("ni").kind is ToolErrorKind.NOT_IMPLEMENTED
    assert ToolError.permission_denied("pd").kind is ToolErrorKind.PERMISSION_DENIED
    assert ToolError.unauthorized("ua").kind is ToolErrorKind.UNAUTHORIZED
    assert ToolError.rate_limited("rl").kind is ToolErrorKind.RATE_LIMITED
    assert ToolError.usage_pool_exhausted("up").kind is ToolErrorKind.USAGE_POOL_EXHAUSTED
    assert ToolError.usage_limit_reached("ul").kind is ToolErrorKind.USAGE_LIMIT_REACHED
    assert ToolError.global_rate_limit("gl").kind is ToolErrorKind.GLOBAL_RATE_LIMIT
    assert ToolError.concurrency_limit("cl").kind is ToolErrorKind.CONCURRENCY_LIMIT
    assert ToolError.service_unavailable("su").kind is ToolErrorKind.SERVICE_UNAVAILABLE
    assert ToolError.network_error("ne").kind is ToolErrorKind.NETWORK_ERROR
    assert ToolError.not_implemented("ni").detail == "ni"


@pytest.mark.parametrize(
    "ctor",
    [
        ToolError.not_found,
        ToolError.timeout,
        ToolError.cancelled,
        ToolError.execution,
        ToolError.terminal_error,
    ],
)
def test_tool_id_constructors_inject_tool_id_detail(ctor):
    tid = ToolId("fs:read")
    e = ctor(tid, "msg")
    assert e.details == {"tool_id": "fs:read"}
    assert e.detail == "msg"


def test_custom_constructor_injects_code():
    e = ToolError.custom("workspace_unavailable", "boom")
    assert e.kind is ToolErrorKind.CUSTOM
    assert e.details == {"code": "workspace_unavailable"}


# ---------------------------------------------------------------------------
# Introspection + dunder
# ---------------------------------------------------------------------------


def test_variant_name_delegates_to_kind_as_str():
    assert ToolError.timeout(ToolId("fs:read"), "x").variant_name() == "timeout"
    assert ToolError.custom("c", "d").variant_name() == "custom"


def test_str_is_detail():
    e = ToolError.not_found(ToolId("bash"), "missing")
    assert str(e) == "missing"


def test_repr_includes_kind_detail_and_optionals():
    bare = ToolError.rate_limited("slow")
    assert repr(bare) == "ToolError(kind=rate_limited, detail='slow')"
    full = ToolError.rate_limited("slow").with_details({"a": 1})
    full.source = ValueError("cause")
    r = repr(full)
    assert "kind=rate_limited" in r
    assert "detail='slow'" in r
    assert "source=ValueError('cause')" in r
    assert "details={'a': 1}" in r


def test_from_json_error_is_invalid_arguments_with_message():
    try:
        json.loads("{bad}")
    except json.JSONDecodeError as je:
        e = ToolError.from_json_error(je)
        assert e.kind is ToolErrorKind.INVALID_ARGUMENTS
        assert str(je) in e.detail
    else:
        pytest.fail("expected JSONDecodeError")


# ---------------------------------------------------------------------------
# to_wire — typed variants (9 kinds -> typed ToolErrorWire variants)
# ---------------------------------------------------------------------------


def test_to_wire_not_found_extracts_tool_id():
    w = ToolError.not_found(ToolId("fs:read"), "missing").to_wire()
    assert isinstance(w, ToolNotFoundWire)
    assert w.tool_id == "fs:read"


def test_to_wire_permission_denied_uses_detail_as_reason():
    w = ToolError.permission_denied("nope").to_wire()
    assert isinstance(w, PermissionDeniedWire)
    assert w.reason == "nope"


def test_to_wire_timeout_extracts_tool_id_and_elapsed_ms():
    # `with_details` REPLACES the whole dict (Rust semantics:
    # `self.details = Some(details)`), so the tool_id the `timeout()`
    # constructor injected would be lost if a caller chained
    # with_details over it. The wire bridge reads BOTH fields off the
    # single `details` dict, so we exercise the multi-field extraction
    # the real runtime hits (tool_id + elapsed_ms together).
    e = ToolError.new(ToolErrorKind.TIMEOUT, "slow").with_details(
        {"tool_id": "bash", "elapsed_ms": 1500}
    )
    w = e.to_wire()
    assert isinstance(w, TimeoutWire)
    assert w.tool_id == "bash"
    assert w.elapsed_ms == 1500


def test_to_wire_timeout_defaults_elapsed_ms_to_zero():
    w = ToolError.timeout(ToolId("bash"), "slow").to_wire()
    assert isinstance(w, TimeoutWire)
    assert w.elapsed_ms == 0


def test_to_wire_cancelled_extracts_tool_id():
    w = ToolError.cancelled(ToolId("bash"), "aborted").to_wire()
    assert isinstance(w, CancelledWire)
    assert w.tool_id == "bash"


def test_to_wire_invalid_arguments_passes_details_through():
    e = ToolError.invalid_arguments("bad").with_details({"field": "x"})
    w = e.to_wire()
    assert isinstance(w, InvalidArgumentsWire)
    assert w.message == "bad"
    assert w.details == {"field": "x"}


def test_to_wire_execution_extracts_tool_id_and_message():
    w = ToolError.execution(ToolId("fs:read"), "boom").to_wire()
    assert isinstance(w, ExecutionWire)
    assert w.tool_id == "fs:read"
    assert w.message == "boom"


def test_to_wire_behavior_version_unsupported_extracts_requested():
    e = ToolError.new(
        ToolErrorKind.BEHAVIOR_VERSION_UNSUPPORTED, "nope"
    ).with_details({"tool_id": "fs:read", "requested": "v2"})
    w = e.to_wire()
    assert isinstance(w, BehaviorVersionUnsupportedWire)
    assert w.tool_id == "fs:read"
    assert w.requested == "v2"


def test_to_wire_behavior_version_unsupported_defaults_requested():
    e = ToolError.new(
        ToolErrorKind.BEHAVIOR_VERSION_UNSUPPORTED, "nope"
    ).with_details({"tool_id": "fs:read"})
    w = e.to_wire()
    assert isinstance(w, BehaviorVersionUnsupportedWire)
    assert w.requested == "unknown"


def test_to_wire_render_limited_extracts_card_id():
    e = ToolError.new(ToolErrorKind.RENDER_LIMITED, "too big").with_details(
        {"tool_id": "render:card", "card_id": "c1"}
    )
    w = e.to_wire()
    assert isinstance(w, RenderLimitedWire)
    assert w.tool_id == "render:card"
    assert w.reason == "too big"
    assert w.card_id == "c1"


def test_to_wire_render_limited_omits_card_id_when_absent():
    e = ToolError.new(ToolErrorKind.RENDER_LIMITED, "too big").with_details(
        {"tool_id": "render:card"}
    )
    w = e.to_wire()
    assert isinstance(w, RenderLimitedWire)
    assert w.card_id is None


def test_to_wire_terminal_error_extracts_tool_id_and_message():
    w = ToolError.terminal_error(ToolId("bash"), "exit 1").to_wire()
    assert isinstance(w, TerminalErrorWire)
    assert w.tool_id == "bash"
    assert w.message == "exit 1"


def test_to_wire_tool_id_defaults_to_unknown_when_malformed():
    # A malformed tool_id in details falls back to "unknown" rather than
    # raising — the wire shape requires a concrete id.
    e = ToolError.new(ToolErrorKind.NOT_FOUND, "x").with_details(
        {"tool_id": "not:valid:tool:id"}
    )
    w = e.to_wire()
    assert isinstance(w, ToolNotFoundWire)
    assert w.tool_id == "unknown"


def test_to_wire_tool_id_defaults_to_unknown_when_missing():
    e = ToolError.new(ToolErrorKind.NOT_FOUND, "x")
    w = e.to_wire()
    assert isinstance(w, ToolNotFoundWire)
    assert w.tool_id == "unknown"


# ---------------------------------------------------------------------------
# to_wire — Custom-subcoded kinds (9 kinds -> Custom with fixed subcode)
# ---------------------------------------------------------------------------

# (constructor name, expected subcode) for the nine Custom-mapped kinds.
_CUSTOM_SUBCODED: list[tuple[str, str]] = [
    ("not_implemented", "not_implemented"),
    ("unauthorized", "unauthorized"),
    ("rate_limited", "rate_limited"),
    ("usage_pool_exhausted", "usage_pool_exhausted"),
    ("usage_limit_reached", "usage_limit_reached"),
    ("global_rate_limit", "global_rate_limit"),
    ("concurrency_limit", "concurrency_limit"),
    ("service_unavailable", "service_unavailable"),
    ("network_error", "network_error"),
]


@pytest.mark.parametrize("ctor_name,subcode", _CUSTOM_SUBCODED)
def test_to_wire_custom_subcoded_kinds(ctor_name, subcode):
    ctor = getattr(ToolError, ctor_name)
    e = ctor(f"{ctor_name} detail")
    w = e.to_wire()
    assert isinstance(w, CustomWire), f"{ctor_name} should map to Custom"
    assert w.subcode == subcode
    assert w.message == f"{ctor_name} detail"
    # No details supplied -> details is None.
    assert w.details is None


def test_to_wire_custom_kind_reads_subcode_from_details_code():
    # CUSTOM arm: subcode comes from details["code"], not a fixed string.
    e = ToolError.custom("workspace_unavailable", "boom")
    w = e.to_wire()
    assert isinstance(w, CustomWire)
    assert w.subcode == "workspace_unavailable"
    assert w.message == "boom"
    assert w.details == {"code": "workspace_unavailable"}


def test_to_wire_custom_kind_defaults_subcode_when_no_code():
    # A CUSTOM error whose details lacks "code" -> subcode "custom".
    e = ToolError.new(ToolErrorKind.CUSTOM, "x")
    w = e.to_wire()
    assert isinstance(w, CustomWire)
    assert w.subcode == "custom"


# ---------------------------------------------------------------------------
# _custom_details_with_code merge semantics
# ---------------------------------------------------------------------------


def test_custom_details_with_code_does_not_clobber_existing_code():
    merged = _custom_details_with_code(
        {"code": "workspace_unavailable", "retryable": True},
        "service_unavailable",
    )
    assert merged["code"] == "workspace_unavailable"  # existing wins
    assert merged["retryable"] is True


def test_custom_details_with_code_passes_none_and_non_objects_through():
    assert _custom_details_with_code(None, "network_error") is None
    arr = [1, 2, 3]
    assert _custom_details_with_code(arr, "network_error") is arr


def test_custom_details_with_code_merges_into_object_without_code():
    merged = _custom_details_with_code({"retry_after_ms": 1500}, "service_unavailable")
    assert merged == {"retry_after_ms": 1500, "code": "service_unavailable"}


def test_custom_details_with_code_does_not_mutate_caller_dict():
    d = {"retry_after_ms": 1500}
    _custom_details_with_code(d, "service_unavailable")
    assert "code" not in d  # caller's dict untouched


# ---------------------------------------------------------------------------
# Wire-bridge properties (ported verbatim from Rust wire_bridge_tests)
# ---------------------------------------------------------------------------


def test_service_unavailable_details_survive_wire_projection():
    # Structured details used to be dropped for Custom-mapped kinds; they
    # must now ride the wire with the subcode merged in so recognizers
    # keying on details.code keep working.
    err = ToolError.service_unavailable("sandbox not ready").with_details(
        {"retry_after_ms": 1500}
    )
    wire = err.to_wire()
    assert isinstance(wire, CustomWire)
    assert wire.subcode == "service_unavailable"
    assert wire.message == "sandbox not ready"
    assert wire.details is not None
    assert wire.details["retry_after_ms"] == 1500
    assert wire.details["code"] == "service_unavailable"


def test_rate_limit_and_usage_kinds_merge_subcode_uniformly():
    # Same property as service_unavailable, applied to every Custom-mapped
    # kind: object details without a `code` key gain the subcode.
    cases = [
        (ToolError.rate_limited("slow down"), "rate_limited"),
        (ToolError.usage_pool_exhausted("pool empty"), "usage_pool_exhausted"),
        (ToolError.usage_limit_reached("limit hit"), "usage_limit_reached"),
        (ToolError.global_rate_limit("global limit"), "global_rate_limit"),
        (ToolError.concurrency_limit("too many in flight"), "concurrency_limit"),
    ]
    for err, subcode in cases:
        err = err.with_details({"retry_after_ms": 250})
        wire = err.to_wire()
        assert isinstance(wire, CustomWire), f"expected Custom for {subcode}"
        assert wire.subcode == subcode
        assert wire.details is not None
        assert wire.details["code"] == subcode, f"subcode merged for {subcode}"
        assert wire.details["retry_after_ms"] == 250
