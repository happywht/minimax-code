"""Tests for the session-history IPC surface.

Covers the four pieces added by the session-history task:

* :class:`SessionsDAO.set_archived` — convenience wrapper around
  :meth:`update` with the canonical flip semantics.
* :class:`SessionsDAO.get_messages` — JOIN-style read that
  returns the most recent ``limit`` messages for a session.
* ``session.*`` IPC handlers — round-trip through the
  in-process :class:`~minimax_code.ipc.client.IPCClient`.
* Concurrent archive / unarchive races — 100 racing writes
  must not corrupt the row.

The tests share a fresh migrated DB per test function; the
in-process IPC client is wired against the same DB so the
handler round-trips exercise the real call path.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from minimax_code.app import set_projects_dao, set_sessions_dao
from minimax_code.ipc.client import IPCClient
from minimax_code.storage.dao.messages import MessagesDAO
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Fresh temp DB path per test."""
    return make_temp_database_path(tmp_path)


@pytest.fixture
async def async_db(db_path: Path) -> AsyncDatabase:
    """A migrated async Database."""
    db = AsyncDatabase(db_path)
    await db.connect()
    await db.migrate()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
def id_factory() -> Any:
    """Generate prefixed unique ids so test output is greppable."""

    def _make(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:10]}"

    return _make


@pytest.fixture
async def sessions_dao(async_db: AsyncDatabase) -> SessionsDAO:
    """Plain :class:`SessionsDAO` bound to the test DB."""
    return SessionsDAO(async_db)


@pytest.fixture
async def seeded_sessions(
    async_db: AsyncDatabase, sessions_dao: SessionsDAO, id_factory: Any
) -> list[str]:
    """Insert five sessions with a known title pattern; return the ids."""
    ids = []
    for i in range(5):
        sid = id_factory("ses")
        await sessions_dao.create(id=sid, title=f"seed-{i:02d}")
        ids.append(sid)
    return ids


# ---------------------------------------------------------------------------
# SessionsDAO.set_archived
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_archived_true_flips_flag(
    sessions_dao: SessionsDAO, seeded_sessions: list[str]
) -> None:
    """``set_archived(id, True)`` writes the flag and returns the row."""
    sid = seeded_sessions[0]
    row = await sessions_dao.set_archived(sid, True)
    assert row is not None
    assert row["id"] == sid
    assert row["archived"] is True
    # Persisted across a fresh fetch.
    again = await sessions_dao.get(sid)
    assert again is not None
    assert again["archived"] is True


@pytest.mark.asyncio
async def test_set_archived_false_unflips_flag(
    sessions_dao: SessionsDAO, seeded_sessions: list[str]
) -> None:
    """``set_archived(id, False)`` clears the flag."""
    sid = seeded_sessions[0]
    await sessions_dao.set_archived(sid, True)
    row = await sessions_dao.set_archived(sid, False)
    assert row is not None
    assert row["archived"] is False
    again = await sessions_dao.get(sid)
    assert again is not None
    assert again["archived"] is False


@pytest.mark.asyncio
async def test_set_archived_unknown_id_returns_none(
    sessions_dao: SessionsDAO,
) -> None:
    """A missing session_id yields ``None``, not an exception."""
    assert await sessions_dao.set_archived("ses_nope", True) is None


# ---------------------------------------------------------------------------
# SessionsDAO.count — search kwarg (regression for the previous bug)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_with_search_filters_substring(
    sessions_dao: SessionsDAO, id_factory: Any
) -> None:
    """``count(search=...)`` mirrors the ``list`` search filter.

    Three sessions: two titles contain "alpha" (case-insensitive),
    one does not. ``count(search="alpha")`` must return ``2``,
    not the unfiltered total ``3``.
    """
    await sessions_dao.create(id=id_factory("ses"), title="Alpha Bravo")
    await sessions_dao.create(id=id_factory("ses"), title="Bravo Charlie")
    await sessions_dao.create(id=id_factory("ses"), title="Charlie alpha")
    assert await sessions_dao.count() == 3
    assert await sessions_dao.count(search="alpha") == 2
    # Empty string is treated as "no filter" (same as list()).
    assert await sessions_dao.count(search="") == 3
    # No match.
    assert await sessions_dao.count(search="zzz_nope") == 0


@pytest.mark.asyncio
async def test_count_with_archived_and_search_combine(
    sessions_dao: SessionsDAO, id_factory: Any
) -> None:
    """``count(archived=, search=)`` must AND the filters like
    :meth:`list` does — not drop one of them."""
    a1 = id_factory("ses")
    a2 = id_factory("ses")
    a3 = id_factory("ses")
    a4 = id_factory("ses")
    await sessions_dao.create(id=a1, title="Alpha open")
    await sessions_dao.create(id=a2, title="Alpha archived")
    await sessions_dao.create(id=a3, title="Beta open")
    await sessions_dao.create(id=a4, title="Beta archived")
    await sessions_dao.set_archived(a2, True)
    await sessions_dao.set_archived(a4, True)

    # "alpha" + archived=True: 1 row (Alpha archived)
    assert await sessions_dao.count(search="alpha", archived=True) == 1
    # "alpha" + archived=False: 1 row (Alpha open)
    assert await sessions_dao.count(search="alpha", archived=False) == 1
    # "alpha" with no archive filter: 2 rows
    assert await sessions_dao.count(search="alpha") == 2
    # no search, archived=True: 2 rows
    assert await sessions_dao.count(archived=True) == 2
    # no filters: 4 rows
    assert await sessions_dao.count() == 4


# ---------------------------------------------------------------------------
# SessionsDAO.get_messages (JOIN)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_messages_empty_when_no_messages(
    sessions_dao: SessionsDAO, seeded_sessions: list[str]
) -> None:
    """A session with no messages returns an empty list, not ``None``."""
    out = await sessions_dao.get_messages(seeded_sessions[0])
    assert out == []


@pytest.mark.asyncio
async def test_get_messages_returns_newest_first(
    async_db: AsyncDatabase,
    sessions_dao: SessionsDAO,
    seeded_sessions: list[str],
) -> None:
    """``get_messages`` orders by ``created_at DESC`` so the latest
    message is at index 0."""
    mdao = MessagesDAO(async_db)
    sid = seeded_sessions[0]
    for i in range(3):
        await mdao.create(
            id=f"msg_{sid}_{i}",
            session_id=sid,
            role="user",
            content=f"hello-{i}",
        )
    out = await sessions_dao.get_messages(sid)
    assert len(out) == 3
    # Latest first.
    assert out[0]["content"] == "hello-2"
    assert out[1]["content"] == "hello-1"
    assert out[2]["content"] == "hello-0"
    # tool_calls is decoded back to a Python object (None here).
    assert all(m["tool_calls"] is None for m in out)


@pytest.mark.asyncio
async def test_get_messages_respects_limit(
    async_db: AsyncDatabase,
    sessions_dao: SessionsDAO,
    seeded_sessions: list[str],
) -> None:
    """``limit`` clamps the result count."""
    mdao = MessagesDAO(async_db)
    sid = seeded_sessions[0]
    for i in range(10):
        await mdao.create(
            id=f"msg_{sid}_{i:02d}",
            session_id=sid,
            role="assistant",
            content=f"reply-{i:02d}",
        )
    out = await sessions_dao.get_messages(sid, limit=3)
    assert len(out) == 3
    # Default sort is newest first.
    assert out[0]["content"] == "reply-09"


@pytest.mark.asyncio
async def test_get_messages_decodes_tool_calls(
    async_db: AsyncDatabase,
    sessions_dao: SessionsDAO,
    seeded_sessions: list[str],
) -> None:
    """The JSON ``tool_calls`` column comes back as a Python list."""
    mdao = MessagesDAO(async_db)
    sid = seeded_sessions[0]
    await mdao.create(
        id=f"msg_tc_{sid}",
        session_id=sid,
        role="assistant",
        content="",
        tool_calls=[{"name": "search", "args": {"q": "x"}}],
    )
    out = await sessions_dao.get_messages(sid)
    assert len(out) == 1
    assert out[0]["tool_calls"] == [{"name": "search", "args": {"q": "x"}}]


# ---------------------------------------------------------------------------
# IPC handler round-trip — message.list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_message_list_restores_tool_display_details(
    async_db: AsyncDatabase,
    sessions_dao: SessionsDAO,
    id_factory: Any,
) -> None:
    """``message.list`` keeps tool rows useful after history reload.

    Streaming events carry ``tool_name`` and ``tool_args`` directly, but
    persisted tool result rows only store ``tool_call_id``. The history
    API must recover display metadata from the assistant ``tool_calls``
    payload so reloaded chats still show the concrete tool card instead
    of a generic "tool" row with missing parameters.
    """
    sid = id_factory("ses")
    await sessions_dao.create(id=sid, title="tool history")
    mdao = MessagesDAO(async_db)
    await mdao.create(
        id=id_factory("msg"),
        session_id=sid,
        role="assistant",
        content="I will inspect the file.",
        tool_calls=[
            {
                "id": "call_read_1",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path":"app.py","start":1}',
                },
            }
        ],
        created_at="2026-01-01T00:00:01+00:00",
    )
    await mdao.create(
        id=id_factory("msg"),
        session_id=sid,
        role="tool",
        content="file contents",
        tool_call_id="call_read_1",
        created_at="2026-01-01T00:00:02+00:00",
    )

    async def fake_init_runtime() -> object:
        return object()

    with (
        patch("minimax_code.app.init_runtime", side_effect=fake_init_runtime),
        patch("minimax_code.app.get_db", return_value=async_db),
    ):
        client = IPCClient()
        reply = await client.request("message.list", {"session_id": sid})

    tool_rows = [m for m in reply["messages"] if m["role"] == "tool"]
    assert len(tool_rows) == 1
    assert tool_rows[0]["tool_call_id"] == "call_read_1"
    assert tool_rows[0]["tool_name"] == "read_file"
    assert tool_rows[0]["tool_args"] == {"path": "app.py", "start": 1}


@pytest.mark.asyncio
async def test_message_list_returns_newest_window(
    async_db: AsyncDatabase,
    sessions_dao: SessionsDAO,
    id_factory: Any,
) -> None:
    """``message.list`` returns the *newest* N messages, not the oldest.

    Sessions longer than the default limit used to lose their latest
    turns: ``ORDER BY created_at ASC LIMIT n`` fetched the first N rows
    ever written, so the chat panel repopulated with the session's
    opening messages instead of the current conversation. The wire list
    stays ascending; only the window moves to the tail.
    """
    sid = id_factory("ses")
    await sessions_dao.create(id=sid, title="long history")
    mdao = MessagesDAO(async_db)
    for i in range(120):
        await mdao.create(
            id=id_factory("msg"),
            session_id=sid,
            role="user" if i % 2 == 0 else "assistant",
            content=f"msg-{i:03d}",
            created_at=f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}+00:00",
        )

    async def fake_init_runtime() -> object:
        return object()

    with (
        patch("minimax_code.app.init_runtime", side_effect=fake_init_runtime),
        patch("minimax_code.app.get_db", return_value=async_db),
    ):
        client = IPCClient()
        reply = await client.request("message.list", {"session_id": sid})

    texts = [m["text"] for m in reply["messages"]]
    assert len(texts) == 100
    assert texts[0] == "msg-020"
    assert texts[-1] == "msg-119"


@pytest.mark.asyncio
async def test_message_list_before_pagination_walks_backwards_from_cursor(
    async_db: AsyncDatabase,
    sessions_dao: SessionsDAO,
    id_factory: Any,
) -> None:
    """``before`` pagination returns the N rows closest to the cursor.

    The window must walk backwards from the cursor (fetch DESC, reverse
    to ASC on the wire), not jump to the session start.
    """
    sid = id_factory("ses")
    await sessions_dao.create(id=sid, title="paged history")
    mdao = MessagesDAO(async_db)
    for i in range(90):
        await mdao.create(
            id=id_factory("msg"),
            session_id=sid,
            role="user",
            content=f"msg-{i:03d}",
            created_at=f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}+00:00",
        )

    async def fake_init_runtime() -> object:
        return object()

    # msg-059 was written at 00:00:59; rows strictly before it are
    # msg-000..msg-058, and the 30 closest to the cursor are msg-029..msg-058.
    with (
        patch("minimax_code.app.init_runtime", side_effect=fake_init_runtime),
        patch("minimax_code.app.get_db", return_value=async_db),
    ):
        client = IPCClient()
        reply = await client.request(
            "message.list",
            {"session_id": sid, "limit": 30, "before": "2026-01-01T00:00:59+00:00"},
        )

    texts = [m["text"] for m in reply["messages"]]
    assert len(texts) == 30
    assert texts[0] == "msg-029"
    assert texts[-1] == "msg-058"


# ---------------------------------------------------------------------------
# Concurrency: archive / unarchive race
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_archive_unarchive_does_not_corrupt(
    sessions_dao: SessionsDAO, seeded_sessions: list[str]
) -> None:
    """100 racing flip calls leave the row consistent.

    The final state is *some* boolean (not ``None``); the row is
    still queryable; no exception leaks out. The exact boolean
    depends on the order of write commits — we don't try to
    predict it.
    """
    sid = seeded_sessions[0]

    async def flip(value: bool) -> None:
        await sessions_dao.set_archived(sid, value)

    results = await asyncio.gather(
        *([flip(True), flip(False)] * 50),
        return_exceptions=True,
    )
    errors = [r for r in results if isinstance(r, BaseException)]
    assert not errors, f"concurrent set_archived raised: {errors!r}"

    final = await sessions_dao.get(sid)
    assert final is not None
    assert isinstance(final["archived"], bool)


# ---------------------------------------------------------------------------
# IPC handler round-trip — session.create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_ipc_create_returns_id(
    sessions_dao: SessionsDAO,
) -> None:
    """``session.create`` with no params returns a ``session_id`` and
    synthesizes a default title like ``New chat — 2026-06-02 15:30``.

    The default title is generated from ``datetime.now()`` so we
    only assert the ``New chat —`` prefix is present and the
    row is persisted — not the exact timestamp.
    """
    set_sessions_dao(sessions_dao)
    try:
        client = IPCClient()
        reply = await client.request("session.create", {})
        assert "session_id" in reply
        assert reply["session_id"], "session_id must be a non-empty string"
        assert reply["session_id"].startswith("ses_"), (
            f"session_id should follow the ses_<hex> convention, "
            f"got {reply['session_id']!r}"
        )
        # The reply echoes the title and created_at the UI displays.
        assert reply["title"].startswith("New chat —"), (
            f"default title should be 'New chat — <timestamp>', "
            f"got {reply['title']!r}"
        )
        assert reply["created_at"], "created_at should be a non-empty string"
        # The row is actually persisted: session.get must return it.
        fetched = await client.request(
            "session.get", {"session_id": reply["session_id"]}
        )
        assert fetched["session"]["id"] == reply["session_id"]
        assert fetched["session"]["title"] == reply["title"]
    finally:
        set_sessions_dao(None)


@pytest.mark.asyncio
async def test_session_ipc_create_with_title(
    sessions_dao: SessionsDAO,
) -> None:
    """``session.create { title: 'foo' }`` uses the supplied title
    verbatim and the new row is immediately visible in
    ``session.list``.

    This is the core regression we're guarding: the sidebar's
    "new task" button called ``session.create`` (or used to be
    wired to it) and the row should appear in the history list
    without a refresh. The follow-up ``session.list`` call
    exercises that path end-to-end.
    """
    set_sessions_dao(sessions_dao)
    try:
        client = IPCClient()
        reply = await client.request(
            "session.create", {"title": "smoke-test"}
        )
        assert reply["title"] == "smoke-test"
        new_id = reply["session_id"]

        # session.list must see the row without a manual refresh.
        list_reply = await client.request("session.list", {})
        ids = [s["id"] for s in list_reply["sessions"]]
        assert new_id in ids, (
            f"newly created session {new_id!r} should be visible in "
            f"session.list, got {ids}"
        )
        # And the listed row's title matches what we sent.
        created_row = next(s for s in list_reply["sessions"] if s["id"] == new_id)
        assert created_row["title"] == "smoke-test"
        assert created_row["archived"] is False
    finally:
        set_sessions_dao(None)


# ---------------------------------------------------------------------------
# IPC handler round-trip — session.list / get / archive / unarchive / delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_ipc_list_returns_seeded_sessions(
    async_db: AsyncDatabase,
    sessions_dao: SessionsDAO,
    seeded_sessions: list[str],
) -> None:
    """``session.list`` returns the seeded sessions and the right total."""
    set_sessions_dao(sessions_dao)
    try:
        client = IPCClient()
        reply = await client.request("session.list", {})
        assert "sessions" in reply
        assert "total" in reply
        assert reply["total"] == 5
        assert len(reply["sessions"]) == 5
        assert {s["id"] for s in reply["sessions"]} == set(seeded_sessions)
    finally:
        set_sessions_dao(None)


@pytest.mark.asyncio
async def test_session_ipc_list_filters_archived(
    async_db: AsyncDatabase,
    sessions_dao: SessionsDAO,
    seeded_sessions: list[str],
) -> None:
    """``session.list { archived: true }`` returns only archived rows.

    Default is "no archive filter" (all rows). ``archived: False``
    returns only active sessions. ``archived: True`` returns only
    archived sessions.
    """
    # Archive two of the five.
    await sessions_dao.set_archived(seeded_sessions[0], True)
    await sessions_dao.set_archived(seeded_sessions[1], True)
    set_sessions_dao(sessions_dao)
    try:
        client = IPCClient()

        all_rows = (await client.request("session.list", {}))["sessions"]
        assert len(all_rows) == 5

        active = (await client.request("session.list", {"archived": False}))[
            "sessions"
        ]
        assert len(active) == 3
        assert all(s["archived"] is False for s in active)

        archived = (await client.request("session.list", {"archived": True}))[
            "sessions"
        ]
        assert len(archived) == 2
        assert all(s["archived"] is True for s in archived)
        assert {s["id"] for s in archived} == {
            seeded_sessions[0],
            seeded_sessions[1],
        }
    finally:
        set_sessions_dao(None)


@pytest.mark.asyncio
async def test_session_ipc_list_search_fuzzy(
    async_db: AsyncDatabase,
    sessions_dao: SessionsDAO,
    id_factory: Any,
) -> None:
    """``session.list { search: 'foo' }`` returns only matching titles.

    SQLite's ``LIKE`` is case-insensitive when we add
    ``COLLATE NOCASE``; this test exercises both case-folded
    and substring matches. It also asserts ``total`` matches
    the page length — the previous attempt shipped a bug where
    ``total`` was computed without the search filter, and this
    test missed it because it never checked ``total``.
    """
    await sessions_dao.create(id=id_factory("ses"), title="Alpha Bravo")
    await sessions_dao.create(id=id_factory("ses"), title="Bravo Charlie")
    await sessions_dao.create(id=id_factory("ses"), title="Charlie alpha")
    set_sessions_dao(sessions_dao)
    try:
        client = IPCClient()
        # Lowercase needle matches both "Alpha Bravo" and "Charlie alpha"
        # because the DAO uses COLLATE NOCASE.
        reply = await client.request("session.list", {"search": "alpha"})
        matches = reply["sessions"]
        assert len(matches) == 2
        assert reply["total"] == 2, (
            f"total must reflect the search filter, got {reply['total']!r} "
            f"with {len(matches)} matches"
        )
        titles = sorted(s["title"] for s in matches)
        assert titles == ["Alpha Bravo", "Charlie alpha"]
    finally:
        set_sessions_dao(None)


@pytest.mark.asyncio
async def test_session_ipc_get_includes_recent_messages(
    async_db: AsyncDatabase,
    sessions_dao: SessionsDAO,
    seeded_sessions: list[str],
) -> None:
    """``session.get`` returns ``session`` + ``recent_messages``."""
    mdao = MessagesDAO(async_db)
    sid = seeded_sessions[0]
    await mdao.create(
        id=f"msg_{sid}_a", session_id=sid, role="user", content="hi"
    )
    await mdao.create(
        id=f"msg_{sid}_b",
        session_id=sid,
        role="assistant",
        content="hello!",
    )
    set_sessions_dao(sessions_dao)
    try:
        client = IPCClient()
        reply = await client.request("session.get", {"session_id": sid})
        assert reply["session"]["id"] == sid
        assert len(reply["recent_messages"]) == 2
        # Newest first.
        assert reply["recent_messages"][0]["content"] == "hello!"
    finally:
        set_sessions_dao(None)


@pytest.mark.asyncio
async def test_session_ipc_archive_round_trip(
    sessions_dao: SessionsDAO, seeded_sessions: list[str]
) -> None:
    """``session.archive`` flips the flag and returns the row.

    Then ``session.list { archived: True }`` includes the row,
    and ``session.unarchive`` flips it back.
    """
    sid = seeded_sessions[0]
    set_sessions_dao(sessions_dao)
    try:
        client = IPCClient()
        # archive
        reply = await client.request("session.archive", {"session_id": sid})
        assert reply["ok"] is True
        assert reply["session"]["archived"] is True
        # Confirm via the list filter
        archived = (await client.request("session.list", {"archived": True}))[
            "sessions"
        ]
        assert any(s["id"] == sid for s in archived)
        # unarchive
        reply = await client.request(
            "session.unarchive", {"session_id": sid}
        )
        assert reply["ok"] is True
        assert reply["session"]["archived"] is False
        # Confirm it moved back to active
        active = (await client.request("session.list", {"archived": False}))[
            "sessions"
        ]
        assert any(s["id"] == sid for s in active)
    finally:
        set_sessions_dao(None)


@pytest.mark.asyncio
async def test_session_ipc_delete_removes_row(
    sessions_dao: SessionsDAO, seeded_sessions: list[str]
) -> None:
    """``session.delete`` removes the row and a follow-up ``get``
    returns an ``unknown session_id`` error."""
    sid = seeded_sessions[0]
    set_sessions_dao(sessions_dao)
    try:
        client = IPCClient()
        reply = await client.request("session.delete", {"session_id": sid})
        assert reply["ok"] is True
        with pytest.raises(Exception) as excinfo:
            await client.request("session.get", {"session_id": sid})
        assert "unknown session_id" in str(excinfo.value)
    finally:
        set_sessions_dao(None)


@pytest.mark.asyncio
async def test_session_ipc_unknown_session_returns_error(
    sessions_dao: SessionsDAO,
) -> None:
    """All ``session.*`` methods that need a session_id return a
    clear ``INVALID_PARAMS`` error for an unknown id."""
    set_sessions_dao(sessions_dao)
    try:
        client = IPCClient()
        for method, params in (
            ("session.get", {"session_id": "ses_nope"}),
            ("session.archive", {"session_id": "ses_nope"}),
            ("session.unarchive", {"session_id": "ses_nope"}),
            ("session.delete", {"session_id": "ses_nope"}),
        ):
            with pytest.raises(Exception) as excinfo:
                await client.request(method, params)
            assert "unknown session_id" in str(excinfo.value), (
                f"{method} should mention unknown session_id, "
                f"got: {excinfo.value!r}"
            )
    finally:
        set_sessions_dao(None)


@pytest.mark.asyncio
async def test_session_ipc_missing_session_id_param_returns_error(
    sessions_dao: SessionsDAO,
) -> None:
    """Omitting ``session_id`` yields a JSON-RPC ``INVALID_PARAMS``
    error envelope, not a stack trace."""
    set_sessions_dao(sessions_dao)
    try:
        client = IPCClient()
        with pytest.raises(Exception) as excinfo:
            await client.request("session.archive", {})
        assert "session_id" in str(excinfo.value)
    finally:
        set_sessions_dao(None)


@pytest.mark.asyncio
async def test_session_ipc_list_total_reflects_all_filters(
    async_db: AsyncDatabase,
    sessions_dao: SessionsDAO,
    id_factory: Any,
) -> None:
    """Regression test: ``session.list`` ``total`` must be filter-aware.

    The previous attempt shipped a bug where ``count()`` did not
    accept a ``search`` kwarg, so the ``total`` returned to the
    UI ignored the search filter (it only reflected the
    ``archived`` filter, if any). This test seeds a mix of
    titles and archive states and asserts ``total`` agrees with
    the page contents for every filter combination — search
    alone, archive alone, both, and neither.
    """
    a_open = id_factory("ses")
    a_arch = id_factory("ses")
    b_open = id_factory("ses")
    b_arch = id_factory("ses")
    await sessions_dao.create(id=a_open, title="Alpha active")
    await sessions_dao.create(id=a_arch, title="Alpha archived")
    await sessions_dao.create(id=b_open, title="Bravo active")
    await sessions_dao.create(id=b_arch, title="Bravo archived")
    await sessions_dao.set_archived(a_arch, True)
    await sessions_dao.set_archived(b_arch, True)
    set_sessions_dao(sessions_dao)
    try:
        client = IPCClient()

        # No filters: total = 4
        reply = await client.request("session.list", {})
        assert reply["total"] == 4
        assert len(reply["sessions"]) == 4

        # search=alpha only: 2 rows match (case-insensitive),
        # total must also be 2 (not 4).
        reply = await client.request("session.list", {"search": "alpha"})
        assert len(reply["sessions"]) == 2
        assert reply["total"] == 2, (
            f"search filter must be applied to total, got {reply['total']!r}"
        )

        # archived=true only: 2 rows, total = 2.
        reply = await client.request("session.list", {"archived": True})
        assert len(reply["sessions"]) == 2
        assert reply["total"] == 2

        # search=alpha + archived=true: 1 row (Alpha archived),
        # total = 1.
        reply = await client.request(
            "session.list", {"search": "alpha", "archived": True}
        )
        assert len(reply["sessions"]) == 1
        assert reply["total"] == 1, (
            f"combined search+archive must AND the filters, "
            f"got total={reply['total']!r}"
        )
        assert reply["sessions"][0]["id"] == a_arch

        # search=alpha + archived=false: 1 row (Alpha active),
        # total = 1.
        reply = await client.request(
            "session.list", {"search": "alpha", "archived": False}
        )
        assert len(reply["sessions"]) == 1
        assert reply["total"] == 1
        assert reply["sessions"][0]["id"] == a_open

        # No matches: empty list, total = 0.
        reply = await client.request("session.list", {"search": "zzz_nope"})
        assert reply["sessions"] == []
        assert reply["total"] == 0
    finally:
        set_sessions_dao(None)


@pytest.mark.asyncio
async def test_session_ipc_list_pagination_total_stable(
    sessions_dao: SessionsDAO, id_factory: Any
) -> None:
    """``total`` is the unfiltered-row count for the same query
    regardless of which page we ask for.

    A subtle bug: if the handler computes ``total`` from the
    already-paginated list, the page-2 reply would have
    ``total == page_size`` instead of the real total. This
    test makes that mistake loud.
    """
    # Seed 12 sessions.
    ids = []
    for i in range(12):
        sid = id_factory("ses")
        await sessions_dao.create(id=sid, title=f"page-{i:02d}")
        ids.append(sid)
    set_sessions_dao(sessions_dao)
    try:
        client = IPCClient()
        # First page: 5 rows
        page1 = await client.request("session.list", {"limit": 5, "offset": 0})
        assert len(page1["sessions"]) == 5
        assert page1["total"] == 12
        # Second page: 5 rows, same total
        page2 = await client.request("session.list", {"limit": 5, "offset": 5})
        assert len(page2["sessions"]) == 5
        assert page2["total"] == 12
        # Third page: 2 rows, same total
        page3 = await client.request("session.list", {"limit": 5, "offset": 10})
        assert len(page3["sessions"]) == 2
        assert page3["total"] == 12
        # No overlap between pages.
        all_returned = (
            {s["id"] for s in page1["sessions"]}
            | {s["id"] for s in page2["sessions"]}
            | {s["id"] for s in page3["sessions"]}
        )
        assert all_returned == set(ids)
    finally:
        set_sessions_dao(None)


# ---------------------------------------------------------------------------
# SessionsDAO.stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_counts_sessions_and_messages(
    async_db: AsyncDatabase,
    sessions_dao: SessionsDAO,
    id_factory: Any,
) -> None:
    """``stats`` returns total sessions, archived sessions, and total messages."""
    mdao = MessagesDAO(async_db)
    s1 = id_factory("ses")
    s2 = id_factory("ses")
    await sessions_dao.create(id=s1, title="open")
    await sessions_dao.create(id=s2, title="archived")
    await sessions_dao.set_archived(s2, True)
    await mdao.create(id=id_factory("msg"), session_id=s1, role="user", content="hello")
    await mdao.create(id=id_factory("msg"), session_id=s1, role="assistant", content="hi")
    stats = await sessions_dao.stats()
    assert stats == {
        "total_sessions": 2,
        "archived_sessions": 1,
        "total_messages": 2,
    }


# ---------------------------------------------------------------------------
# IPC handler round-trip — session.stats / session.export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_ipc_stats(
    async_db: AsyncDatabase,
    sessions_dao: SessionsDAO,
    id_factory: Any,
) -> None:
    """``session.stats`` returns aggregate counts over the wire."""
    mdao = MessagesDAO(async_db)
    s1 = id_factory("ses")
    s2 = id_factory("ses")
    await sessions_dao.create(id=s1, title="stats-open")
    await sessions_dao.create(id=s2, title="stats-archived")
    await sessions_dao.set_archived(s2, True)
    await mdao.create(id=id_factory("msg"), session_id=s1, role="user", content="a")

    set_sessions_dao(sessions_dao)
    try:
        client = IPCClient()
        reply = await client.request("session.stats", {})
        assert reply["total_sessions"] == 2
        assert reply["archived_sessions"] == 1
        assert reply["total_messages"] == 1
    finally:
        set_sessions_dao(None)


@pytest.mark.asyncio
async def test_session_ipc_export_markdown(
    async_db: AsyncDatabase,
    sessions_dao: SessionsDAO,
    id_factory: Any,
) -> None:
    """``session.export`` returns a Markdown rendering of the conversation."""
    mdao = MessagesDAO(async_db)
    sid = id_factory("ses")
    await sessions_dao.create(id=sid, title="export-me")
    await mdao.create(
        id=id_factory("msg"),
        session_id=sid,
        role="user",
        content="Hello",
        created_at="2026-01-01T00:00:00+00:00",
    )
    await mdao.create(
        id=id_factory("msg"),
        session_id=sid,
        role="assistant",
        content="World",
        created_at="2026-01-01T00:00:01+00:00",
    )

    set_sessions_dao(sessions_dao)
    try:
        client = IPCClient()
        reply = await client.request("session.export", {"session_id": sid})
        markdown = reply["markdown"]
        assert "# export-me" in markdown
        assert "## User" in markdown
        assert "Hello" in markdown
        assert "## Assistant" in markdown
        assert "World" in markdown
        assert f"<!-- session_id: {sid} -->" in markdown
    finally:
        set_sessions_dao(None)


@pytest.mark.asyncio
async def test_session_ipc_export_unknown_session_raises(
    sessions_dao: SessionsDAO,
) -> None:
    """Exporting an unknown session raises a RuntimeError from the JSON-RPC error."""
    set_sessions_dao(sessions_dao)
    try:
        client = IPCClient()
        with pytest.raises(RuntimeError):
            await client.request("session.export", {"session_id": "ses_nope"})
    finally:
        set_sessions_dao(None)


@pytest.mark.asyncio
async def test_session_ipc_update_project_moves_session(
    async_db: AsyncDatabase,
    sessions_dao: SessionsDAO,
    id_factory: Any,
) -> None:
    """``session.updateProject`` moves a session to another project and
    validates that the target project exists.
    """
    proj_dao = ProjectsDAO(async_db)
    inbox = await proj_dao.ensure_inbox()
    project = await proj_dao.create(
        id=id_factory("proj"), name="Target Project", description=""
    )
    sid = id_factory("ses")
    await sessions_dao.create(id=sid, title="move-me", project_id=inbox["id"])

    set_sessions_dao(sessions_dao)
    set_projects_dao(proj_dao)
    try:
        client = IPCClient()
        reply = await client.request(
            "session.updateProject",
            {"session_id": sid, "project_id": project["id"]},
        )
        assert reply["ok"] is True
        assert reply["session"]["project_id"] == project["id"]

        # Moving to an unknown project fails cleanly.
        with pytest.raises(Exception) as excinfo:
            await client.request(
                "session.updateProject",
                {"session_id": sid, "project_id": "proj_nope"},
            )
        assert "unknown project_id" in str(excinfo.value)

        # Moving an unknown session fails cleanly.
        with pytest.raises(Exception) as excinfo:
            await client.request(
                "session.updateProject",
                {"session_id": "ses_nope", "project_id": inbox["id"]},
            )
        assert "unknown session_id" in str(excinfo.value)
    finally:
        set_sessions_dao(None)
        set_projects_dao(None)


# ---------------------------------------------------------------------------
# SessionsDAO batch operations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_update_project_moves_sessions(
    async_db: AsyncDatabase,
    sessions_dao: SessionsDAO,
    id_factory: Any,
) -> None:
    """``batch_update_project`` moves multiple sessions atomically."""
    from minimax_code.storage.dao.projects import ProjectsDAO

    proj_dao = ProjectsDAO(async_db)
    project = await proj_dao.create(id=id_factory("proj"), name="Target")
    sid1 = id_factory("ses")
    sid2 = id_factory("ses")
    sid3 = id_factory("ses")
    await sessions_dao.create(id=sid1, title="a")
    await sessions_dao.create(id=sid2, title="b")
    await sessions_dao.create(id=sid3, title="c")
    rows = await sessions_dao.batch_update_project([sid1, sid2], project["id"])
    assert len(rows) == 2
    assert {r["id"] for r in rows} == {sid1, sid2}
    assert all(r["project_id"] == project["id"] for r in rows)
    # Empty list is a no-op.
    assert await sessions_dao.batch_update_project([], project["id"]) == []


@pytest.mark.asyncio
async def test_batch_set_archived_flips_many_sessions(
    sessions_dao: SessionsDAO,
    id_factory: Any,
) -> None:
    """``batch_set_archived`` archives or unarchives multiple sessions."""
    sid1 = id_factory("ses")
    sid2 = id_factory("ses")
    await sessions_dao.create(id=sid1, title="a")
    await sessions_dao.create(id=sid2, title="b")
    rows = await sessions_dao.batch_set_archived([sid1, sid2], True)
    assert len(rows) == 2
    assert all(r["archived"] is True for r in rows)
    rows = await sessions_dao.batch_set_archived([sid1], False)
    assert len(rows) == 1
    assert rows[0]["archived"] is False


# ---------------------------------------------------------------------------
# IPC handler round-trip — session.batchArchive / batchUpdateProject
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_ipc_batch_archive_round_trip(
    async_db: AsyncDatabase,
    sessions_dao: SessionsDAO,
    seeded_sessions: list[str],
) -> None:
    """``session.batchArchive`` archives many sessions and returns updated rows."""
    set_sessions_dao(sessions_dao)
    try:
        client = IPCClient()
        reply = await client.request(
            "session.batchArchive",
            {"session_ids": seeded_sessions[:3], "archived": True},
        )
        assert reply["ok"] is True
        assert reply["updated"] == 3
        assert len(reply["sessions"]) == 3
        assert all(s["archived"] is True for s in reply["sessions"])
    finally:
        set_sessions_dao(None)


@pytest.mark.asyncio
async def test_session_ipc_batch_update_project_validates_project(
    async_db: AsyncDatabase,
    sessions_dao: SessionsDAO,
    id_factory: Any,
) -> None:
    """``session.batchUpdateProject`` validates the target project exists."""
    from minimax_code.storage.dao.projects import ProjectsDAO

    proj_dao = ProjectsDAO(async_db)
    await proj_dao.ensure_inbox()
    project = await proj_dao.create(id=id_factory("proj"), name="Target")
    sid = id_factory("ses")
    await sessions_dao.create(id=sid, title="move-me")

    set_sessions_dao(sessions_dao)
    set_projects_dao(proj_dao)
    try:
        client = IPCClient()
        reply = await client.request(
            "session.batchUpdateProject",
            {"session_ids": [sid], "project_id": project["id"]},
        )
        assert reply["ok"] is True
        assert reply["updated"] == 1
        assert reply["sessions"][0]["project_id"] == project["id"]

        with pytest.raises(Exception) as excinfo:
            await client.request(
                "session.batchUpdateProject",
                {"session_ids": [sid], "project_id": "proj_nope"},
            )
        assert "unknown project_id" in str(excinfo.value)
    finally:
        set_sessions_dao(None)
        set_projects_dao(None)
