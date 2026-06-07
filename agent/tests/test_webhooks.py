"""Tests for v0.5.0 Webhook framework.

Coverage:

* :class:`TestWebhookDAO` — CRUD round-trip, get_by_path, regenerate_secret
* :class:`TestWebhookReceiver` — HMAC signature verification, payload parsing
* :class:`TestWebhookIPC` — drives the ``webhook.*`` handlers in-process
* :class:`TestDispatchAction` — fire-and-forget action dispatch
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from pathlib import Path

import pytest

from minimax_code.ipc.client import IPCClient
from minimax_code.storage.dao.webhooks import WebhookDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path
from minimax_code.webhooks.dispatcher import dispatch_webhook_action
from minimax_code.webhooks.receiver import WebhookPayload, WebhookReceiver


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
def webhook_dao(async_db: AsyncDatabase) -> WebhookDAO:
    return WebhookDAO(async_db)


# ---------------------------------------------------------------------------
# DAO
# ---------------------------------------------------------------------------


class TestWebhookDAO:
    @pytest.mark.asyncio
    async def test_create_and_get(self, webhook_dao: WebhookDAO) -> None:
        row = await webhook_dao.create(name="GitHub Push", source="github")
        assert row["id"].startswith("wh_")
        assert row["name"] == "GitHub Push"
        assert row["source"] == "github"
        assert row["url_path"].startswith("/hooks/wh_")
        assert row["enabled"] is True

        fetched = await webhook_dao.get(row["id"])
        assert fetched is not None
        assert fetched["name"] == "GitHub Push"

    @pytest.mark.asyncio
    async def test_create_with_secret(self, webhook_dao: WebhookDAO) -> None:
        row = await webhook_dao.create(
            name="Secret Hook", source="custom", secret="my-hmac-secret"
        )
        assert row["secret"] == "my-hmac-secret"

    @pytest.mark.asyncio
    async def test_get_by_path(self, webhook_dao: WebhookDAO) -> None:
        row = await webhook_dao.create(name="Test", source="gitee")
        found = await webhook_dao.get_by_path(row["url_path"])
        assert found is not None
        assert found["id"] == row["id"]

        # Non-existent path.
        assert await webhook_dao.get_by_path("/hooks/nope") is None

    @pytest.mark.asyncio
    async def test_get_by_path_disabled(self, webhook_dao: WebhookDAO) -> None:
        row = await webhook_dao.create(name="Disabled", source="github")
        await webhook_dao.update(row["id"], enabled=False)
        assert await webhook_dao.get_by_path(row["url_path"]) is None

    @pytest.mark.asyncio
    async def test_list_all(self, webhook_dao: WebhookDAO) -> None:
        await webhook_dao.create(name="A", source="github")
        await webhook_dao.create(name="B", source="gitee")
        await webhook_dao.create(name="C", source="custom")
        entries = await webhook_dao.list_all()
        assert len(entries) == 3

    @pytest.mark.asyncio
    async def test_list_filter_by_source(self, webhook_dao: WebhookDAO) -> None:
        await webhook_dao.create(name="GH", source="github")
        await webhook_dao.create(name="GT", source="gitee")
        entries = await webhook_dao.list_all(source="github")
        assert len(entries) == 1
        assert entries[0]["source"] == "github"

    @pytest.mark.asyncio
    async def test_count(self, webhook_dao: WebhookDAO) -> None:
        await webhook_dao.create(name="A", source="github")
        await webhook_dao.create(name="B", source="github")
        assert await webhook_dao.count() == 2
        assert await webhook_dao.count(source="github") == 2
        assert await webhook_dao.count(source="gitee") == 0

    @pytest.mark.asyncio
    async def test_update(self, webhook_dao: WebhookDAO) -> None:
        row = await webhook_dao.create(name="Old", source="github")
        updated = await webhook_dao.update(row["id"], name="New", enabled=False)
        assert updated["name"] == "New"
        assert updated["enabled"] is False

    @pytest.mark.asyncio
    async def test_delete(self, webhook_dao: WebhookDAO) -> None:
        row = await webhook_dao.create(name="Delete Me", source="custom")
        ok = await webhook_dao.delete(row["id"])
        assert ok is True
        assert await webhook_dao.get(row["id"]) is None
        # Delete non-existent.
        assert await webhook_dao.delete("wh_noexist") is False

    @pytest.mark.asyncio
    async def test_regenerate_secret(self, webhook_dao: WebhookDAO) -> None:
        row = await webhook_dao.create(
            name="With Secret", source="github", secret="old-secret"
        )
        updated = await webhook_dao.regenerate_secret(row["id"])
        assert updated["secret"] != "old-secret"
        assert len(updated["secret"]) == 64  # hex of 32 bytes


# ---------------------------------------------------------------------------
# Receiver — signature verification
# ---------------------------------------------------------------------------


class TestWebhookReceiver:
    def _sign(self, body: bytes, secret: str) -> str:
        """Produce a GitHub-style ``sha256=<hex>`` signature."""
        mac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return f"sha256={mac}"

    @pytest.mark.asyncio
    async def test_valid_github_push(self) -> None:
        receiver = WebhookReceiver()
        secret = "test-secret"
        body = json.dumps({
            "ref": "refs/heads/main",
            "repository": {"full_name": "org/repo"},
            "commits": [{"message": "fix bug", "author": {"name": "dev"}}],
        }).encode()
        sig = self._sign(body, secret)
        cfg = {"id": "wh_1", "source": "github", "secret": secret}
        headers = {"x-hub-signature-256": sig, "x-github-event": "push"}
        payload = await receiver.handle_request(cfg, headers, body)
        assert payload is not None
        assert payload.source == "github"
        assert payload.event == "push"
        assert payload.repo == "org/repo"
        assert payload.branch == "main"
        assert len(payload.commits) == 1

    @pytest.mark.asyncio
    async def test_invalid_signature(self) -> None:
        receiver = WebhookReceiver()
        cfg = {"id": "wh_1", "source": "github", "secret": "real-secret"}
        headers = {"x-hub-signature-256": "sha256=badsig", "x-github-event": "push"}
        body = b"{}"
        payload = await receiver.handle_request(cfg, headers, body)
        assert payload is None

    @pytest.mark.asyncio
    async def test_no_secret_skips_verification(self) -> None:
        receiver = WebhookReceiver()
        cfg = {"id": "wh_1", "source": "github", "secret": None}
        headers = {"x-github-event": "ping"}
        body = json.dumps({"repository": {"full_name": "org/repo"}}).encode()
        payload = await receiver.handle_request(cfg, headers, body)
        assert payload is not None
        assert payload.event == "ping"

    @pytest.mark.asyncio
    async def test_gitee_push(self) -> None:
        receiver = WebhookReceiver()
        body = json.dumps({
            "ref": "refs/heads/dev",
            "repository": {"full_name": "user/proj"},
            "commits": [],
        }).encode()
        secret = "gitee-secret"
        sig = self._sign(body, secret)
        cfg = {"id": "wh_2", "source": "gitee", "secret": secret}
        headers = {"x-hub-signature-256": sig, "x-gitee-event": "push"}
        payload = await receiver.handle_request(cfg, headers, body)
        assert payload is not None
        assert payload.source == "gitee"
        assert payload.branch == "dev"

    @pytest.mark.asyncio
    async def test_custom_webhook(self) -> None:
        receiver = WebhookReceiver()
        cfg = {"id": "wh_3", "source": "custom"}
        headers: dict[str, str] = {}
        body = json.dumps({"action": "deploy"}).encode()
        payload = await receiver.handle_request(cfg, headers, body)
        assert payload is not None
        assert payload.source == "custom"
        assert payload.event == "custom"

    @pytest.mark.asyncio
    async def test_invalid_json_body(self) -> None:
        receiver = WebhookReceiver()
        cfg = {"id": "wh_4", "source": "custom"}
        payload = await receiver.handle_request(cfg, {}, b"not json{")
        assert payload is None


# ---------------------------------------------------------------------------
# IPC handlers
# ---------------------------------------------------------------------------


def _make_client_with_webhook_dao(dao: WebhookDAO) -> IPCClient:
    client = IPCClient()
    setattr(client.server, "_webhook_dao", dao)
    setattr(client.server, "_webhook_dao_lock", asyncio.Lock())
    return client


class TestWebhookIPC:
    @pytest.mark.asyncio
    async def test_list_empty(self, webhook_dao: WebhookDAO) -> None:
        client = _make_client_with_webhook_dao(webhook_dao)
        result = await client.request("webhook.list", {})
        assert result["entries"] == []
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_create(self, webhook_dao: WebhookDAO) -> None:
        client = _make_client_with_webhook_dao(webhook_dao)
        result = await client.request(
            "webhook.create",
            {"name": "Test Hook", "source": "github"},
        )
        assert result["name"] == "Test Hook"
        assert result["source"] == "github"

    @pytest.mark.asyncio
    async def test_update(self, webhook_dao: WebhookDAO) -> None:
        row = await webhook_dao.create(name="Old", source="github")
        client = _make_client_with_webhook_dao(webhook_dao)
        result = await client.request(
            "webhook.update",
            {"id": row["id"], "name": "Updated"},
        )
        assert result["name"] == "Updated"

    @pytest.mark.asyncio
    async def test_delete(self, webhook_dao: WebhookDAO) -> None:
        row = await webhook_dao.create(name="Delete", source="custom")
        client = _make_client_with_webhook_dao(webhook_dao)
        result = await client.request("webhook.delete", {"id": row["id"]})
        assert result["deleted"] is True

    @pytest.mark.asyncio
    async def test_regenerate_secret(self, webhook_dao: WebhookDAO) -> None:
        row = await webhook_dao.create(
            name="Secret", source="github", secret="old"
        )
        client = _make_client_with_webhook_dao(webhook_dao)
        result = await client.request(
            "webhook.regenerate_secret", {"id": row["id"]}
        )
        assert result["secret"] != "old"

    @pytest.mark.asyncio
    async def test_create_requires_name(self, webhook_dao: WebhookDAO) -> None:
        client = _make_client_with_webhook_dao(webhook_dao)
        with pytest.raises(RuntimeError) as ei:
            await client.request("webhook.create", {})
        assert "name" in str(ei.value).lower()


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class TestDispatchAction:
    @pytest.mark.asyncio
    async def test_send_message_no_error(self) -> None:
        payload = WebhookPayload(
            source="github",
            event="push",
            repo="org/repo",
            branch="main",
            commits=[{"message": "test", "author": {"name": "dev"}}],
        )
        # Should not raise.
        await dispatch_webhook_action(
            payload,
            action_type="send-message",
            action_config={},
            webhook_id="wh_test",
        )

    @pytest.mark.asyncio
    async def test_code_review_no_error(self) -> None:
        payload = WebhookPayload(
            source="github",
            event="push",
            repo="org/repo",
            branch="main",
            commits=[{"message": "fix", "added": ["a.py"], "modified": [], "removed": []}],
        )
        await dispatch_webhook_action(
            payload,
            action_type="code-review",
            action_config={},
            webhook_id="wh_test",
        )

    @pytest.mark.asyncio
    async def test_unknown_action_no_error(self) -> None:
        payload = WebhookPayload(source="custom", event="test")
        await dispatch_webhook_action(
            payload,
            action_type="unknown",
            action_config={},
            webhook_id="wh_test",
        )
