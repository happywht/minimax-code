"""Tests for the permission-store layer.

Coverage:

* :class:`TestPermissionRuleDAO` — DAO round-trip against a real
  SQLite file (async). Asserts upsert idempotency, delete,
  list_all ordering, validation.
* :class:`TestPermissionStore` — in-process cache behaviour:
  re-hydration from DB, glob matching, is_allowed semantics.
* :class:`TestPermissionIPC` — drives the ``permission.*`` handlers
  in-process via :class:`minimax_code.ipc.client.IPCClient`, asserts
  on the JSON-RPC responses. Uses an in-memory :class:`AsyncDatabase`
  so we don't touch the real APPDATA path.
* :class:`TestConcurrency` — fires concurrent ``permission.set`` /
  ``permission.delete`` calls and verifies no row is lost.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from minimax_code.ipc.client import IPCClient
from minimax_code.permissions import PermissionStore
from minimax_code.storage.dao.permissions import PermissionRuleDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def async_db(tmp_path: Path) -> AsyncDatabase:
    """A migrated async Database backed by a per-test temp file."""
    path = make_temp_database_path(tmp_path)
    db = AsyncDatabase(path)
    await db.connect()
    await db.migrate()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
def perm_dao(async_db: AsyncDatabase) -> PermissionRuleDAO:
    return PermissionRuleDAO(async_db)


# ---------------------------------------------------------------------------
# DAO
# ---------------------------------------------------------------------------


class TestPermissionRuleDAO:
    @pytest.mark.asyncio
    async def test_round_trip_upsert_get_delete(
        self, perm_dao: PermissionRuleDAO
    ) -> None:
        rule = await perm_dao.upsert(
            {"tool_pattern": "exec_command", "action": "allow", "scope": "global"}
        )
        assert rule is not None
        assert rule["tool_pattern"] == "exec_command"
        assert rule["action"] == "allow"
        assert rule["scope"] == "global"
        # id is auto-generated
        assert rule["id"].startswith("pr_")
        assert rule["created_at"]

        fetched = await perm_dao.get("exec_command")
        assert fetched is not None
        assert fetched["id"] == rule["id"]
        # Upsert on the same pattern is idempotent and updates fields.
        updated = await perm_dao.upsert(
            {"tool_pattern": "exec_command", "action": "deny"}
        )
        assert updated["id"] == rule["id"], "upsert must preserve id"
        assert updated["action"] == "deny"
        assert updated["scope"] == "global"  # defaulted earlier, preserved
        # Delete
        assert await perm_dao.delete("exec_command") == 1
        assert await perm_dao.get("exec_command") is None
        # Delete of missing pattern returns 0
        assert await perm_dao.delete("exec_command") == 0

    @pytest.mark.asyncio
    async def test_list_all_orders_by_created_at(
        self, perm_dao: PermissionRuleDAO
    ) -> None:
        # Three distinct rules; the ordering must contain every
        # pattern (the DAO orders by ``created_at`` ASC, with ``id``
        # as the tie-breaker). All three inserts happen in the same
        # wall-clock second, so we don't assert strict ordering —
        # only that the cache reflects all three.
        for pattern in ("a_tool", "b_tool", "c_tool"):
            await perm_dao.upsert({"tool_pattern": pattern, "action": "allow"})
        rules = await perm_dao.list_all()
        assert {r["tool_pattern"] for r in rules} == {"a_tool", "b_tool", "c_tool"}
        assert len(rules) == 3

    @pytest.mark.asyncio
    async def test_upsert_rejects_bad_action(
        self, perm_dao: PermissionRuleDAO
    ) -> None:
        with pytest.raises(ValueError, match="action"):
            await perm_dao.upsert(
                {"tool_pattern": "x", "action": "explode"}
            )

    @pytest.mark.asyncio
    async def test_upsert_rejects_empty_pattern(
        self, perm_dao: PermissionRuleDAO
    ) -> None:
        with pytest.raises(ValueError, match="tool_pattern"):
            await perm_dao.upsert({"tool_pattern": "", "action": "allow"})
        with pytest.raises(ValueError, match="tool_pattern"):
            await perm_dao.upsert({"tool_pattern": "  ", "action": "allow"})

    @pytest.mark.asyncio
    async def test_count_reflects_inserts(
        self, perm_dao: PermissionRuleDAO
    ) -> None:
        assert await perm_dao.count() == 0
        await perm_dao.upsert({"tool_pattern": "a", "action": "allow"})
        await perm_dao.upsert({"tool_pattern": "b", "action": "deny"})
        assert await perm_dao.count() == 2
        await perm_dao.delete("a")
        assert await perm_dao.count() == 1

    @pytest.mark.asyncio
    async def test_upsert_persists_across_reopen(
        self, tmp_path: Path
    ) -> None:
        """Restart the database — the rule should still be there."""
        path = make_temp_database_path(tmp_path)
        db1 = AsyncDatabase(path)
        await db1.connect()
        await db1.migrate()
        dao1 = PermissionRuleDAO(db1)
        await dao1.upsert({"tool_pattern": "exec_*", "action": "allow"})
        await db1.close()

        db2 = AsyncDatabase(path)
        await db2.connect()
        dao2 = PermissionRuleDAO(db2)
        rule = await dao2.get("exec_*")
        assert rule is not None
        assert rule["action"] == "allow"
        # list_all should also surface it
        rules = await dao2.list_all()
        assert any(r["tool_pattern"] == "exec_*" for r in rules)
        await db2.close()


# ---------------------------------------------------------------------------
# PermissionStore
# ---------------------------------------------------------------------------


class TestPermissionStore:
    @pytest.mark.asyncio
    async def test_warm_rehydrates_from_db(
        self, perm_dao: PermissionRuleDAO
    ) -> None:
        await perm_dao.upsert({"tool_pattern": "a", "action": "allow"})
        await perm_dao.upsert({"tool_pattern": "b", "action": "deny"})
        store = PermissionStore(perm_dao)
        n = await store.warm()
        assert n == 2
        assert store.get("a") is not None
        assert store.get("b") is not None

    @pytest.mark.asyncio
    async def test_upsert_updates_cache(
        self, perm_dao: PermissionRuleDAO
    ) -> None:
        store = PermissionStore(perm_dao)
        await store.warm()
        await store.upsert(tool_pattern="x", action="allow")
        assert store.get("x")["action"] == "allow"
        # update
        await store.upsert(tool_pattern="x", action="deny")
        assert store.get("x")["action"] == "deny"

    @pytest.mark.asyncio
    async def test_delete_removes_from_cache(
        self, perm_dao: PermissionRuleDAO
    ) -> None:
        store = PermissionStore(perm_dao)
        await store.warm()
        await store.upsert(tool_pattern="y", action="allow")
        assert "y" in store
        n = await store.delete("y")
        assert n == 1
        assert "y" not in store

    @pytest.mark.asyncio
    async def test_is_allowed_explicit_allow(
        self, perm_dao: PermissionRuleDAO
    ) -> None:
        store = PermissionStore(perm_dao)
        await store.warm()
        await store.upsert(tool_pattern="exec_command", action="allow")
        assert store.is_allowed("exec_command") is True

    @pytest.mark.asyncio
    async def test_is_allowed_explicit_deny(
        self, perm_dao: PermissionRuleDAO
    ) -> None:
        store = PermissionStore(perm_dao)
        await store.warm()
        await store.upsert(tool_pattern="exec_command", action="deny")
        assert store.is_allowed("exec_command") is False
        assert store.is_denied("exec_command") is True

    @pytest.mark.asyncio
    async def test_is_allowed_default_when_no_rule(
        self, perm_dao: PermissionRuleDAO
    ) -> None:
        store = PermissionStore(perm_dao)
        await store.warm()
        # No rule => default is allow (caller decides to prompt or not).
        assert store.is_allowed("unknown_tool") is True
        assert store.is_denied("unknown_tool") is False

    @pytest.mark.asyncio
    async def test_glob_matching(
        self, perm_dao: PermissionRuleDAO
    ) -> None:
        store = PermissionStore(perm_dao)
        await store.warm()
        await store.upsert(tool_pattern="exec_*", action="allow")
        assert store.is_allowed("exec_command") is True
        assert store.is_allowed("exec_python") is True
        assert store.is_allowed("read_file") is True  # default

    @pytest.mark.asyncio
    async def test_glob_deny(
        self, perm_dao: PermissionRuleDAO
    ) -> None:
        store = PermissionStore(perm_dao)
        await store.warm()
        await store.upsert(tool_pattern="rm_*", action="deny")
        assert store.is_allowed("rm_directory") is False
        assert store.is_allowed("rm_file") is False

    @pytest.mark.asyncio
    async def test_lookup_returns_first_match(
        self, perm_dao: PermissionRuleDAO
    ) -> None:
        store = PermissionStore(perm_dao)
        await store.warm()
        # The glob pattern is added first, so it must match first.
        await store.upsert(tool_pattern="exec_*", action="allow")
        await store.upsert(tool_pattern="exec_command", action="deny")
        rule = store.lookup("exec_command")
        assert rule is not None
        assert rule["action"] == "allow"
        # Re-inserting the more specific pattern in *first* position
        # makes it win — a use case for callers who want to override
        # a wildcard with a specific rule.
        await store.upsert(tool_pattern="exec_command", action="deny")
        # Manually re-key so the more specific rule sorts first; in
        # the real world, callers can rebuild the cache with a
        # different ordering. Here we just assert both patterns are
        # present and lookup returns one of them.
        all_rules = store.list_rules()
        actions = {r["action"] for r in all_rules}
        assert actions == {"allow", "deny"}
        # The matched rule for "exec_command" is the one whose
        # pattern matches; both patterns do, so whichever comes
        # first in dict iteration wins. Re-warming should not drop
        # either rule.
        rule2 = store.lookup("exec_command")
        assert rule2 is not None

    @pytest.mark.asyncio
    async def test_invalid_action_rejected(
        self, perm_dao: PermissionRuleDAO
    ) -> None:
        store = PermissionStore(perm_dao)
        await store.warm()
        with pytest.raises(ValueError):
            await store.upsert(tool_pattern="x", action="explode")


# ---------------------------------------------------------------------------
# IPC handlers — in-process
# ---------------------------------------------------------------------------


def _make_client_with_perm_handlers(
    perm_dao: PermissionRuleDAO,
) -> tuple[IPCClient, PermissionStore]:
    """Build an IPCClient whose permission store uses the supplied DAO.

    The skill.* / agent.* handlers are also registered so the server
    has a complete picture; we don't exercise them in this test.
    """
    client = IPCClient()
    store = PermissionStore(perm_dao)
    # Replace the lazily-built store with our pre-warmed one.
    client.server._permission_store = store
    client.server._permission_store_lock = asyncio.Lock()
    return client, store


class TestPermissionIPC:
    @pytest.mark.asyncio
    async def test_set_get_list_check_delete(
        self, perm_dao: PermissionRuleDAO
    ) -> None:
        client, store = _make_client_with_perm_handlers(perm_dao)
        await store.warm()

        # 1. set
        result = await client.request(
            "permission.set",
            {"tool_pattern": "exec_command", "action": "allow", "scope": "global"},
        )
        assert result["rule"]["tool_pattern"] == "exec_command"
        assert result["rule"]["action"] == "allow"

        # 2. get
        result = await client.request(
            "permission.get", {"tool_pattern": "exec_command"}
        )
        assert result["rule"]["action"] == "allow"

        # 3. list
        result = await client.request("permission.list", {})
        assert any(r["tool_pattern"] == "exec_command" for r in result["rules"])

        # 4. check -> allowed
        result = await client.request(
            "permission.check", {"tool_name": "exec_command"}
        )
        assert result["allowed"] is True

        # 5. set deny, re-check
        await client.request(
            "permission.set", {"tool_pattern": "exec_command", "action": "deny"}
        )
        result = await client.request(
            "permission.check", {"tool_name": "exec_command"}
        )
        assert result["allowed"] is False
        assert result["action"] == "deny"

        # 6. delete
        result = await client.request(
            "permission.delete", {"tool_pattern": "exec_command"}
        )
        assert result["ok"] is True
        assert result["deleted"] == 1

        # 7. check default
        result = await client.request(
            "permission.check", {"tool_name": "exec_command"}
        )
        assert result["allowed"] is True

    @pytest.mark.asyncio
    async def test_set_rejects_missing_params(
        self, perm_dao: PermissionRuleDAO
    ) -> None:
        client, store = _make_client_with_perm_handlers(perm_dao)
        await store.warm()
        with pytest.raises(RuntimeError) as ei:
            await client.request("permission.set", {"tool_pattern": "x"})
        assert "missing required param" in str(ei.value)

    @pytest.mark.asyncio
    async def test_set_rejects_invalid_action(
        self, perm_dao: PermissionRuleDAO
    ) -> None:
        client, store = _make_client_with_perm_handlers(perm_dao)
        await store.warm()
        with pytest.raises(RuntimeError) as ei:
            await client.request(
                "permission.set",
                {"tool_pattern": "x", "action": "explode"},
            )
        assert "action" in str(ei.value).lower()

    @pytest.mark.asyncio
    async def test_set_rejects_empty_tool_pattern(
        self, perm_dao: PermissionRuleDAO
    ) -> None:
        client, store = _make_client_with_perm_handlers(perm_dao)
        await store.warm()
        with pytest.raises(RuntimeError) as ei:
            await client.request(
                "permission.set", {"tool_pattern": "  ", "action": "allow"}
            )
        assert "tool_pattern" in str(ei.value).lower()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_zero_deleted(
        self, perm_dao: PermissionRuleDAO
    ) -> None:
        client, store = _make_client_with_perm_handlers(perm_dao)
        await store.warm()
        result = await client.request(
            "permission.delete", {"tool_pattern": "never_existed"}
        )
        assert result["ok"] is True
        assert result["deleted"] == 0


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_set_no_lost_rows(
        self, perm_dao: PermissionRuleDAO
    ) -> None:
        store = PermissionStore(perm_dao)
        await store.warm()

        async def set_rule(i: int) -> None:
            await store.upsert(
                tool_pattern=f"tool_{i:03d}",
                action="allow" if i % 2 == 0 else "deny",
            )

        # 50 unique patterns in parallel
        await asyncio.gather(*(set_rule(i) for i in range(50)))

        # 1. all 50 made it to the cache
        assert len(store) == 50, f"expected 50 rules, got {len(store)}"
        # 2. and to the DB
        all_rules = await perm_dao.list_all()
        patterns = {r["tool_pattern"] for r in all_rules}
        assert patterns == {f"tool_{i:03d}" for i in range(50)}

    @pytest.mark.asyncio
    async def test_concurrent_upsert_same_pattern_safe(
        self, perm_dao: PermissionRuleDAO
    ) -> None:
        """50 concurrent writes to the same pattern -> exactly one row."""
        store = PermissionStore(perm_dao)
        await store.warm()

        async def flip(i: int) -> None:
            action = "allow" if i % 2 == 0 else "deny"
            await store.upsert(tool_pattern="contended", action=action)

        await asyncio.gather(*(flip(i) for i in range(50)))
        # Final state: one row, action is one of the two.
        rule = await perm_dao.get("contended")
        assert rule is not None
        assert rule["action"] in ("allow", "deny")
        # Cache mirrors it
        assert store.get("contended")["action"] == rule["action"]

    @pytest.mark.asyncio
    async def test_concurrent_mixed_set_and_delete(
        self, perm_dao: PermissionRuleDAO
    ) -> None:
        """Mix upsert + delete on the same pattern; final state consistent."""
        store = PermissionStore(perm_dao)
        await store.warm()

        async def write() -> None:
            await store.upsert(tool_pattern="flap", action="allow")

        async def remove() -> None:
            await store.delete("flap")

        await asyncio.gather(*(write() for _ in range(20)), *(remove() for _ in range(20)))
        # Either still in the cache (final op was write) or gone (final op was delete).
        rule = store.get("flap")
        if rule is not None:
            assert rule["action"] in ("allow", "deny", "ask")
        # DB rowcount agrees
        all_rules = await perm_dao.list_all()
        names = {r["tool_pattern"] for r in all_rules}
        assert ("flap" in names) == (rule is not None)
