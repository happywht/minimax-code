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

import pytest

from minimax_code.app import set_sessions_dao
from minimax_code.ipc.client import IPCClient
from minimax_code.storage.dao.messages import MessagesDAO
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
    and substring matches.
    """
    await sessions_dao.create(id=id_factory("ses"), title="Alpha Bravo")
    await sessions_dao.create(id=id_factory("ses"), title="Bravo Charlie")
    await sessions_dao.create(id=id_factory("ses"), title="Charlie alpha")
    set_sessions_dao(sessions_dao)
    try:
        client = IPCClient()
        # Lowercase needle matches both "Alpha Bravo" and "Charlie alpha"
        # because the DAO uses COLLATE NOCASE.
        matches = (await client.request("session.list", {"search": "alpha"}))[
            "sessions"
        ]
        assert len(matches) == 2
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
