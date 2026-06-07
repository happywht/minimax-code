"""Tests for the ``audit.*`` namespace + AuditLogDAO.

Coverage:

* :class:`TestAuditLogDAO` — DAO round-trip against a real SQLite file.
  Asserts record / list / count / stats / purge cycle.
* :class:`TestAuditIPC` — drives the ``audit.*`` handlers in-process.
* :class:`TestCoreAuditHook` — verifies ``AgentCore._dispatch_tool``
  writes an audit record when ``audit_dao`` is wired.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from minimax_code.agent.core import AgentCore, _sanitize_args
from minimax_code.agent.tools import ToolRegistry, ToolResult
from minimax_code.agent.tools.base import Tool
from minimax_code.ipc.client import IPCClient
from minimax_code.storage.dao.audit import AuditLogDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return make_temp_database_path(tmp_path)


@pytest.fixture
async def async_db(db_path: Path) -> AsyncDatabase:
    db = AsyncDatabase(db_path)
    await db.connect()
    await db.migrate()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
def audit_dao(async_db: AsyncDatabase) -> AuditLogDAO:
    return AuditLogDAO(async_db)


# ---------------------------------------------------------------------------
# DAO
# ---------------------------------------------------------------------------


class TestAuditLogDAO:
    @pytest.mark.asyncio
    async def test_record_and_list(self, audit_dao: AuditLogDAO) -> None:
        row = await audit_dao.record(
            tool_name="read_file",
            tool_args={"path": "/etc/passwd"},
            result_status="success",
            duration_ms=42,
        )
        assert row["id"].startswith("aud_")
        assert row["tool_name"] == "read_file"
        assert row["result_status"] == "success"
        assert row["duration_ms"] == 42

        entries = await audit_dao.list_recent()
        assert len(entries) == 1
        assert entries[0]["id"] == row["id"]

    @pytest.mark.asyncio
    async def test_record_with_session(self, audit_dao: AuditLogDAO) -> None:
        row = await audit_dao.record(
            session_id="ses_abc123",
            tool_name="exec_command",
            result_status="fail",
            error="non-zero exit code",
            exit_code=1,
            duration_ms=150,
        )
        assert row["session_id"] == "ses_abc123"
        assert row["exit_code"] == 1
        assert row["error"] == "non-zero exit code"

    @pytest.mark.asyncio
    async def test_list_filter_by_tool(self, audit_dao: AuditLogDAO) -> None:
        await audit_dao.record(tool_name="read_file", result_status="success")
        await audit_dao.record(tool_name="exec_command", result_status="success")
        await audit_dao.record(tool_name="read_file", result_status="fail")

        entries = await audit_dao.list_recent(tool_name="read_file")
        assert len(entries) == 2
        assert all(e["tool_name"] == "read_file" for e in entries)

    @pytest.mark.asyncio
    async def test_list_filter_by_session(self, audit_dao: AuditLogDAO) -> None:
        await audit_dao.record(
            session_id="ses_a", tool_name="read_file", result_status="success",
        )
        await audit_dao.record(
            session_id="ses_b", tool_name="read_file", result_status="success",
        )
        entries = await audit_dao.list_recent(session_id="ses_a")
        assert len(entries) == 1
        assert entries[0]["session_id"] == "ses_a"

    @pytest.mark.asyncio
    async def test_count(self, audit_dao: AuditLogDAO) -> None:
        await audit_dao.record(tool_name="read_file", result_status="success")
        await audit_dao.record(tool_name="exec_command", result_status="fail")
        assert await audit_dao.count() == 2
        assert await audit_dao.count(tool_name="read_file") == 1

    @pytest.mark.asyncio
    async def test_stats(self, audit_dao: AuditLogDAO) -> None:
        await audit_dao.record(tool_name="read_file", result_status="success")
        await audit_dao.record(tool_name="read_file", result_status="success")
        await audit_dao.record(tool_name="exec_command", result_status="fail")

        s = await audit_dao.stats()
        assert s["total"] == 3
        assert s["by_tool"]["read_file"] == 2
        assert s["by_tool"]["exec_command"] == 1
        assert s["by_status"]["success"] == 2
        assert s["by_status"]["fail"] == 1

    @pytest.mark.asyncio
    async def test_purge(self, audit_dao: AuditLogDAO) -> None:
        await audit_dao.record(
            tool_name="old_tool", result_status="success",
            created_at="2020-01-01T00:00:00",
        )
        await audit_dao.record(
            tool_name="new_tool", result_status="success",
            created_at="2099-12-31T23:59:59",
        )
        deleted = await audit_dao.purge_before("2025-01-01T00:00:00")
        assert deleted == 1
        entries = await audit_dao.list_recent()
        assert len(entries) == 1
        assert entries[0]["tool_name"] == "new_tool"

    @pytest.mark.asyncio
    async def test_list_pagination(self, audit_dao: AuditLogDAO) -> None:
        for i in range(5):
            await audit_dao.record(
                tool_name=f"tool_{i}", result_status="success",
            )
        page1 = await audit_dao.list_recent(limit=2, offset=0)
        assert len(page1) == 2
        page2 = await audit_dao.list_recent(limit=2, offset=2)
        assert len(page2) == 2
        page3 = await audit_dao.list_recent(limit=2, offset=4)
        assert len(page3) == 1


# ---------------------------------------------------------------------------
# IPC handlers
# ---------------------------------------------------------------------------


def _make_client_with_audit_dao(audit_dao: AuditLogDAO) -> IPCClient:
    """Build an IPCClient with the audit DAO pre-injected."""
    client = IPCClient()
    setattr(client.server, "_audit_log_dao", audit_dao)
    setattr(client.server, "_audit_log_dao_lock", asyncio.Lock())
    return client


class TestAuditIPC:
    @pytest.mark.asyncio
    async def test_list_empty(self, audit_dao: AuditLogDAO) -> None:
        client = _make_client_with_audit_dao(audit_dao)
        result = await client.request("audit.list", {})
        assert result["entries"] == []
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_list_with_data(self, audit_dao: AuditLogDAO) -> None:
        await audit_dao.record(tool_name="read_file", result_status="success")
        client = _make_client_with_audit_dao(audit_dao)
        result = await client.request("audit.list", {})
        assert len(result["entries"]) == 1
        assert result["total"] == 1
        assert result["entries"][0]["tool_name"] == "read_file"

    @pytest.mark.asyncio
    async def test_stats(self, audit_dao: AuditLogDAO) -> None:
        await audit_dao.record(tool_name="read_file", result_status="success")
        await audit_dao.record(tool_name="exec_command", result_status="fail")
        client = _make_client_with_audit_dao(audit_dao)
        result = await client.request("audit.stats", {})
        assert result["total"] == 2
        assert result["by_tool"]["read_file"] == 1

    @pytest.mark.asyncio
    async def test_purge(self, audit_dao: AuditLogDAO) -> None:
        await audit_dao.record(
            tool_name="old", result_status="success",
            created_at="2020-01-01T00:00:00",
        )
        client = _make_client_with_audit_dao(audit_dao)
        result = await client.request(
            "audit.purge", {"before_iso": "2025-01-01T00:00:00"}
        )
        assert result["deleted"] == 1

    @pytest.mark.asyncio
    async def test_purge_requires_before_iso(self, audit_dao: AuditLogDAO) -> None:
        client = _make_client_with_audit_dao(audit_dao)
        with pytest.raises(RuntimeError) as ei:
            await client.request("audit.purge", {})
        assert "before_iso" in str(ei.value)


# ---------------------------------------------------------------------------
# Core audit hook
# ---------------------------------------------------------------------------


class TestCoreAuditHook:
    @pytest.mark.asyncio
    async def test_dispatch_records_audit(
        self, audit_dao: AuditLogDAO
    ) -> None:
        """When audit_dao is wired, _dispatch_tool writes an audit row."""
        registry = ToolRegistry()

        class HelloTool(Tool):
            name = "hello"
            description = "test tool"

            async def run(self, args: dict) -> ToolResult:  # type: ignore[override]
                return ToolResult.ok("hello world")

        registry.register(HelloTool())
        core = AgentCore(registry=registry)
        core.audit_dao = audit_dao
        core._audit_session_id = "ses_test"

        result = await core._dispatch_tool(
            {"id": "call_1", "name": "hello", "arguments": {}}
        )
        assert result.ok

        entries = await audit_dao.list_recent()
        assert len(entries) == 1
        e = entries[0]
        assert e["tool_name"] == "hello"
        assert e["result_status"] == "success"
        assert e["session_id"] == "ses_test"
        assert e["duration_ms"] is not None
        assert e["duration_ms"] >= 0


# ---------------------------------------------------------------------------
# _sanitize_args helper
# ---------------------------------------------------------------------------


class TestSanitizeArgs:
    def test_leaves_normal_keys(self) -> None:
        out = _sanitize_args({"path": "/tmp", "mode": "r"}, AgentCore._SANITIZE_KEYS)
        assert out == {"path": "/tmp", "mode": "r"}

    def test_masks_sensitive_keys(self) -> None:
        out = _sanitize_args(
            {"api_key": "sk-123", "path": "/tmp"},
            AgentCore._SANITIZE_KEYS,
        )
        assert out["api_key"] == "***"
        assert out["path"] == "/tmp"

    def test_masks_nested(self) -> None:
        out = _sanitize_args(
            {"config": {"token": "abc", "name": "test"}},
            AgentCore._SANITIZE_KEYS,
        )
        assert out["config"]["token"] == "***"
        assert out["config"]["name"] == "test"

    def test_handles_non_dict(self) -> None:
        assert _sanitize_args("not a dict", AgentCore._SANITIZE_KEYS) == {}

    def test_all_sensitive_patterns(self) -> None:
        keys = {
            "api_key": "v", "token": "v", "secret": "v",
            "password": "v", "Authorization": "v",
            "access_token": "v", "private_key": "v",
        }
        out = _sanitize_args(keys, AgentCore._SANITIZE_KEYS)
        for v in out.values():
            assert v == "***"
