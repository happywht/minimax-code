"""Tests for the prompt-queue wire types (R40).

Mirrors grok's ``xai-prompt-queue`` wire tests (round-trip, golden JSON, required
session id, sparse defaults, unknown-field tolerance, Default derive) and pins
the pydantic-mapping specifics: camelCase aliases, ``None``-exclusion on
serialization, pydantic ``ValidationError`` on missing required fields, and the
frozen-dataclass behavior of the actor-internal meta type.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from minimax_code.prompt_queue import QueueChanged, QueueEntryMeta, QueueEntryWire


def _sample_entry(
    *,
    id="p1",
    version=3,
    owner="alice",
    last_editor="bob",
    kind="prompt",
    text="fix the bug",
    position=0,
):
    return QueueEntryWire(
        id=id,
        version=version,
        owner=owner,
        last_editor=last_editor,
        kind=kind,
        text=text,
        position=position,
    )


# --- QueueChanged wire round-trip (mirror grok) -----------------------------


def test_queue_changed_full_round_trip():
    original = QueueChanged(
        session_id="sess-42",
        entries=[
            _sample_entry(
                id="p1",
                version=3,
                owner="alice",
                last_editor="bob",
                kind="prompt",
                text="fix the bug",
                position=0,
            ),
            QueueEntryWire(id="p2", version=0, kind="bash", text="ls -la", position=1),
        ],
        running_prompt_id="p0",
    )
    data = json.loads(original.to_wire_json())
    assert data["sessionId"] == "sess-42"
    assert data["entries"][0]["lastEditor"] == "bob"
    assert data["runningPromptId"] == "p0"
    # owner/last_editor None on the second entry → omitted from the wire.
    assert "owner" not in data["entries"][1]
    assert "lastEditor" not in data["entries"][1]
    # Round-trip back through the wire.
    round_trip = QueueChanged.model_validate(data)
    assert round_trip == original


def test_queue_changed_golden_wire_json():
    """Pins the exact wire JSON; a key rename here breaks deployed clients."""
    payload = QueueChanged(
        session_id="s1",
        entries=[
            _sample_entry(
                id="p1",
                version=2,
                owner="alice",
                last_editor="bob",
                kind="prompt",
                text="hi",
                position=0,
            )
        ],
        running_prompt_id="p0",
    )
    expected = {
        "sessionId": "s1",
        "entries": [
            {
                "id": "p1",
                "version": 2,
                "owner": "alice",
                "lastEditor": "bob",
                "kind": "prompt",
                "text": "hi",
                "position": 0,
            }
        ],
        "runningPromptId": "p0",
    }
    assert json.loads(payload.to_wire_json()) == expected


def test_queue_changed_requires_session_id():
    """A broadcast without sessionId must fail to parse, not apply under the wrong key."""
    with pytest.raises(ValidationError):
        QueueChanged.model_validate({"entries": []})


def test_sparse_payload_deserializes_with_defaults():
    sparse = {"sessionId": "s1", "entries": [{"id": "p1"}]}
    parsed = QueueChanged.model_validate(sparse)
    assert parsed.entries[0].version == 0
    assert parsed.entries[0].kind == ""
    assert parsed.entries[0].text == ""
    assert parsed.entries[0].position == 0
    assert parsed.entries[0].owner is None
    assert parsed.running_prompt_id is None


def test_extra_unknown_fields_ignored():
    data = {
        "sessionId": "s1",
        "entries": [],
        "runningPromptId": None,
        "futureField": "should be ignored",
    }
    parsed = QueueChanged.model_validate(data)
    assert parsed.session_id == "s1"


def test_queue_changed_default():
    """grok ``#[derive(Default)]`` → default() classmethod: empty session, no entries."""
    d = QueueChanged.default()
    assert d.session_id == ""
    assert d.entries == []
    assert d.running_prompt_id is None


# --- QueueEntryWire specifics -----------------------------------------------


def test_queue_entry_wire_omits_none_fields():
    e = QueueEntryWire(id="p1")  # all optionals default
    data = json.loads(e.to_wire_json())
    assert data == {"id": "p1", "version": 0, "kind": "", "text": "", "position": 0}
    assert "owner" not in data
    assert "lastEditor" not in data


def test_queue_entry_wire_accepts_python_field_names():
    """populate_by_name=True: can construct with snake_case names, not just aliases."""
    e = QueueEntryWire(id="p1", last_editor="bob", position=2)
    assert e.last_editor == "bob"
    assert e.position == 2


def test_queue_entry_wire_requires_id():
    with pytest.raises(ValidationError):
        QueueEntryWire()  # type: ignore[call-arg]


def test_queue_entry_wire_accepts_camel_case_alias():
    """Wire input uses camelCase aliases."""
    e = QueueEntryWire.model_validate({"id": "p1", "lastEditor": "carol", "position": 5})
    assert e.last_editor == "carol"
    assert e.position == 5


# --- QueueEntryMeta (frozen value type) -------------------------------------


def test_queue_entry_meta_value_equality():
    a = QueueEntryMeta(
        id="p1", version=1, owner="a", last_editor="b", kind="prompt", text="hi"
    )
    b = QueueEntryMeta(
        id="p1", version=1, owner="a", last_editor="b", kind="prompt", text="hi"
    )
    assert a == b
    c = QueueEntryMeta(
        id="p1", version=2, owner="a", last_editor="b", kind="prompt", text="hi"
    )
    assert a != c  # version differs


def test_queue_entry_meta_is_frozen():
    m = QueueEntryMeta(id="p1", version=1, owner=None, last_editor=None, kind="", text="")
    with pytest.raises(FrozenInstanceError):
        m.version = 99  # type: ignore[misc]


def test_queue_entry_meta_is_hashable():
    m = QueueEntryMeta(id="p1", version=1, owner=None, last_editor=None, kind="", text="")
    assert {m: "v"}[m] == "v"
