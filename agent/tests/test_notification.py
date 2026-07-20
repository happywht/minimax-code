"""Tests for ``minimax_code.computer_hub_sdk.notification`` (R144).

Mirrors grok-build's ``xai-computer-hub-sdk/src/notification.rs`` (362 lines) --
the SDK crate's 12th leaf. :func:`parse` classifies a raw JSON-RPC
notification by its ``method`` field into one of four typed shapes
(:class:`HubToolsChanged` / :class:`HubToolNotification` /
:class:`HubToolServerStatusChanged` / :class:`HubUnknown`); a missing
``method`` yields ``None``. The ten Rust ``#[test]``s port 1:1.

Python-specific adjustments (no behavior change):

* Rust ``serde_json::json!({ ... })`` -> a Python ``dict`` literal (the
  ``json!`` macro and a dict literal are both JSON-value literals).
* Rust ``match notif { Variant { .. } => .. }`` -> ``isinstance(notif,
  HubVariant)`` (the frozen-dataclass + Union translation of the Rust enum).
* Rust ``serde_json::from_value`` failure arms are exercised the same way:
  feed a ``params`` with the wrong shape and assert the Unknown fallback.
* ``SessionId`` / ``ToolId`` are str newtypes, so ``notif.session_id == "s1"``
  and ``added == ("echo", "add")`` compare against plain str literals
  (``SessionId("s1") == "s1"`` is ``True`` via the str base class).
"""

from __future__ import annotations

from typing import Any

from minimax_code.computer_hub_sdk.notification import (
    HubNotification,
    HubToolNotification,
    HubToolsChanged,
    HubToolServerStatusChanged,
    HubUnknown,
    parse,
)
from minimax_code.tool_protocol.frames import ToolServerLifecycleStatus


# ---------------------------------------------------------------------------
# tools_changed -- the three delta lists, session_id rides inside params
# ---------------------------------------------------------------------------
def test_parse_tools_changed() -> None:
    value: dict[str, Any] = {
        "jsonrpc": "2.0",
        "session_id": "s1",
        "method": "tools_changed",
        "params": {
            "session_id": "s1",
            "added": ["echo", "add"],
            "removed": [],
        },
    }

    notif = parse(value)

    assert isinstance(notif, HubToolsChanged)
    assert notif.session_id == "s1"
    assert notif.added == ("echo", "add")
    assert notif.removed == ()
    assert notif.updated == ()


def test_parse_tools_changed_with_updated() -> None:
    value: dict[str, Any] = {
        "jsonrpc": "2.0",
        "session_id": "s1",
        "method": "tools_changed",
        "params": {
            "session_id": "s1",
            "added": ["new_tool"],
            "removed": ["old_tool"],
            "updated": ["echo", "add"],
        },
    }

    notif = parse(value)

    assert isinstance(notif, HubToolsChanged)
    assert notif.session_id == "s1"
    assert notif.added == ("new_tool",)
    assert notif.removed == ("old_tool",)
    assert notif.updated == ("echo", "add")


# ---------------------------------------------------------------------------
# tool.notification (generic) -- session_id taken from the envelope
# ---------------------------------------------------------------------------
def test_parse_tool_notification_custom() -> None:
    value: dict[str, Any] = {
        "jsonrpc": "2.0",
        "session_id": "s1",
        "method": "tool.notification",
        "params": {
            "tool_id": "echo",
            "notification": {
                "shape": "custom",
                "value": {
                    "kind": "echo.status",
                    "payload": {"status": "idle"},
                },
            },
        },
    }

    notif = parse(value)

    assert isinstance(notif, HubToolNotification)
    assert notif.session_id == "s1"
    assert notif.frame.tool_id == "echo"


def test_parse_tool_notification_missing_session_id_falls_back_to_unknown() -> None:
    # No envelope ``session_id`` -> even though the frame parses, the envelope
    # lift fails and the whole notification degrades to Unknown (not None).
    value: dict[str, Any] = {
        "jsonrpc": "2.0",
        "method": "tool.notification",
        "params": {
            "tool_id": "echo",
            "notification": {
                "shape": "custom",
                "value": {"kind": "test", "payload": {}},
            },
        },
    }

    notif = parse(value)

    assert isinstance(notif, HubUnknown)
    assert notif.method == "tool.notification"


# ---------------------------------------------------------------------------
# unknown method + missing method -> Unknown / None
# ---------------------------------------------------------------------------
def test_parse_unknown_method() -> None:
    value: dict[str, Any] = {
        "jsonrpc": "2.0",
        "session_id": "s1",
        "method": "future.method",
        "params": {"key": "value"},
    }

    notif = parse(value)

    assert isinstance(notif, HubUnknown)
    assert notif.method == "future.method"
    assert notif.params["key"] == "value"


def test_parse_missing_method_returns_none() -> None:
    # A JSON-RPC response (no ``method``) is not a notification at all.
    value: dict[str, Any] = {"jsonrpc": "2.0", "id": "123", "result": {}}

    assert parse(value) is None


# ---------------------------------------------------------------------------
# malformed params -> Unknown (never None, never raises)
# ---------------------------------------------------------------------------
def test_parse_tools_changed_bad_params_falls_back_to_unknown() -> None:
    # ``params`` has the wrong shape (missing required ``session_id``) ->
    # fall back to Unknown instead of returning None and dropping the event.
    value: dict[str, Any] = {
        "jsonrpc": "2.0",
        "method": "tools_changed",
        "params": {"unexpected_field": True},
    }

    notif = parse(value)

    assert isinstance(notif, HubUnknown)
    assert notif.method == "tools_changed"


# ---------------------------------------------------------------------------
# tool_server_status -- the __tool_server_status / status_changed lift
# ---------------------------------------------------------------------------
def test_parse_tool_server_status_changed() -> None:
    value: dict[str, Any] = {
        "jsonrpc": "2.0",
        "session_id": "s1",
        "method": "tool.notification",
        "params": {
            "tool_id": "__tool_server_status",
            "notification": {
                "shape": "custom",
                "value": {
                    "kind": "status_changed",
                    "payload": {
                        "status": "busy",
                        "active_tool_calls": 2,
                        "active_tool_names": ["read_file", "grep"],
                        "background_tasks": 0,
                        "pending_tool_calls": 0,
                        "last_tool_call_started_ms": 100,
                        "last_tool_call_completed_ms": 0,
                        "uptime_ms": 5000,
                    },
                },
            },
        },
    }

    notif = parse(value)

    assert isinstance(notif, HubToolServerStatusChanged)
    assert notif.session_id == "s1"
    assert notif.status.status == ToolServerLifecycleStatus.Busy
    assert notif.status.active_tool_calls == 2


def test_parse_tool_server_status_non_status_tool_id_stays_generic() -> None:
    # A tool.notification with a different ``tool_id`` must remain a generic
    # ToolNotification, NOT be intercepted into ToolServerStatusChanged --
    # even though its custom ``kind`` happens to be ``status_changed``.
    value: dict[str, Any] = {
        "jsonrpc": "2.0",
        "session_id": "s1",
        "method": "tool.notification",
        "params": {
            "tool_id": "some_other_tool",
            "notification": {
                "shape": "custom",
                "value": {
                    "kind": "status_changed",
                    "payload": {"status": "ready"},
                },
            },
        },
    }

    notif = parse(value)

    assert isinstance(notif, HubToolNotification)


def test_parse_tool_notification_bad_params_falls_back_to_unknown() -> None:
    # ``params`` has the wrong shape (not a valid frame) -> Unknown.
    value: dict[str, Any] = {
        "jsonrpc": "2.0",
        "session_id": "s1",
        "method": "tool.notification",
        "params": {"not_a_valid_frame": True},
    }

    notif = parse(value)

    assert isinstance(notif, HubUnknown)
    assert notif.method == "tool.notification"


# ---------------------------------------------------------------------------
# parse() return type is the Union alias -- every variant satisfies it
# ---------------------------------------------------------------------------
def test_parse_returns_hub_notification_union_for_each_variant() -> None:
    # A static type-checker reads ``parse`` as returning
    # ``HubNotification | None``; at runtime every concrete result (whichever
    # of the four dataclasses, or None) is an instance of the Union alias's
    # arms. This guards against a future refactor that widens the return.
    tools_changed = parse(
        {"method": "tools_changed", "params": {"session_id": "s1"}}
    )
    tool_notif = parse(
        {
            "method": "tool.notification",
            "session_id": "s1",
            "params": {
                "tool_id": "t",
                "notification": {
                    "shape": "custom",
                    "value": {"kind": "k", "payload": {}},
                },
            },
        }
    )
    unknown = parse({"method": "future", "params": {}})
    none_case = parse({"id": "x", "result": {}})

    for n in (tools_changed, tool_notif, unknown):
        assert isinstance(n, (HubToolsChanged, HubToolNotification, HubToolServerStatusChanged, HubUnknown))
        assert isinstance(n, HubNotification.__args__)  # type: ignore[attr-defined]
    assert none_case is None
