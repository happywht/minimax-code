"""Tests for the ``secrets.*`` IPC handlers.

Mirrors the test style of :mod:`test_secrets`: a tiny in-process
fake keyring + a temp ``MINIMAX_API_KEY`` env, no real OS
keyring access. We assert:

* ``secrets.status`` reports the right ``source`` for each layer.
* ``secrets.set`` writes through to the keyring and updates the status.
* ``secrets.set`` rejects empty / non-string / missing ``value``.
* ``secrets.clear`` deletes the keyring entry and reports the
  downstream source (env if still set, else ``none``).
* Backend failures are surfaced as JSON-RPC ``-32603``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from minimax_code import secrets
from minimax_code.ipc.handlers_secrets import register_secret_handlers
from minimax_code.ipc.server import IPCServer

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeKeyring:
    """In-process keyring replacement, same shape as in test_secrets.py."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self.fail: bool = False
        self.set_calls: list[tuple[str, str, str]] = []
        self.delete_calls: list[tuple[str, str]] = []

    def get_password(self, service: str, username: str) -> str | None:
        if self.fail:
            import keyring.errors
            raise keyring.errors.KeyringError("fake backend failure")
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, value: str) -> None:
        self.set_calls.append((service, username, value))
        if self.fail:
            import keyring.errors
            raise keyring.errors.KeyringError("fake backend failure")
        self.store[(service, username)] = value

    def delete_password(self, service: str, username: str) -> None:
        self.delete_calls.append((service, username))
        if self.fail:
            import keyring.errors
            raise keyring.errors.KeyringError("fake backend failure")
        if (service, username) not in self.store:
            import keyring.errors
            raise keyring.errors.PasswordDeleteError("not found")
        del self.store[(service, username)]


@pytest.fixture
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeKeyring]:
    fake = FakeKeyring()
    import keyring
    monkeypatch.setattr(keyring, "get_password", fake.get_password)
    monkeypatch.setattr(keyring, "set_password", fake.set_password)
    monkeypatch.setattr(keyring, "delete_password", fake.delete_password)
    yield fake


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv(secrets.ENV_VAR, raising=False)
    yield


class _CapturedReply:
    """Stand-in for :class:`Context` so handlers can be unit-tested.

    The real ``Context`` writes to the IPC transport; we just
    want to capture the reply / error for assertion.
    """

    def __init__(self) -> None:
        self.reply_value: dict[str, Any] | None = None
        self.error_value: dict[str, Any] | None = None
        self._loop = asyncio.new_event_loop() if False else None  # placeholder

    async def reply(self, value: Any) -> None:
        self.reply_value = value

    async def reply_error(self, code: int, message: str, data: Any = None) -> None:
        self.error_value = {"code": code, "message": message, "data": data}

    async def emit(self, event: str, data: Any) -> None:  # pragma: no cover — unused
        return None


@pytest.fixture
def handlers(fake_keyring: FakeKeyring, clean_env: None) -> dict[str, Any]:
    """Build an IPCServer with the secrets handlers registered.

    Returns a dict with the server, the three handler callables,
    and a fresh capture context per handler.
    """
    import io

    from minimax_code.config import Config

    server = IPCServer(
        config=Config.from_env(),
        stdin=io.StringIO(),
        stdout=io.StringIO(),
    )
    register_secret_handlers(server)
    # Extract the registered callables by name.
    captured: dict[str, Any] = {
        "server": server,
        "status": server._handlers.get("secrets.status"),
        "set": server._handlers.get("secrets.set"),
        "clear": server._handlers.get("secrets.clear"),
    }
    assert captured["status"] is not None
    assert captured["set"] is not None
    assert captured["clear"] is not None
    return captured


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


from typing import Any  # noqa: E402  (placed after the fixture above for readability)


@pytest.mark.asyncio
async def test_status_reports_none_when_nothing_set(
    handlers: dict[str, Any], fake_keyring: FakeKeyring, clean_env: None
) -> None:
    ctx = _CapturedReply()
    await handlers["status"]({}, ctx)
    assert ctx.reply_value == {"configured": False, "source": "none"}


@pytest.mark.asyncio
async def test_status_reports_env_when_only_env_set(
    handlers: dict[str, Any], fake_keyring: FakeKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(secrets.ENV_VAR, "sk-env-1")
    ctx = _CapturedReply()
    await handlers["status"]({}, ctx)
    assert ctx.reply_value == {"configured": True, "source": "env"}


@pytest.mark.asyncio
async def test_status_reports_keyring_when_keyring_set(
    handlers: dict[str, Any], fake_keyring: FakeKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_keyring.store[(secrets.KEYRING_SERVICE, secrets.KEYRING_USERNAME)] = "sk-ring"
    monkeypatch.setenv(secrets.ENV_VAR, "sk-env")  # both set; keyring wins
    ctx = _CapturedReply()
    await handlers["status"]({}, ctx)
    assert ctx.reply_value == {"configured": True, "source": "keyring"}


@pytest.mark.asyncio
async def test_set_writes_to_keyring(
    handlers: dict[str, Any], fake_keyring: FakeKeyring, clean_env: None
) -> None:
    ctx = _CapturedReply()
    await handlers["set"]({"value": "sk-new-1"}, ctx)
    assert ctx.error_value is None
    assert ctx.reply_value == {"configured": True, "source": "keyring"}
    # The keyring should now hold the new value.
    assert fake_keyring.store[
        (secrets.KEYRING_SERVICE, secrets.KEYRING_USERNAME)
    ] == "sk-new-1"
    # And ``secrets.get_api_key`` returns it.
    assert secrets.get_api_key() == "sk-new-1"


@pytest.mark.asyncio
async def test_set_rebuilds_active_llm(
    handlers: dict[str, Any],
    fake_keyring: FakeKeyring,
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from minimax_code import app

    rebuild = AsyncMock()
    monkeypatch.setattr(app, "rebuild_subagent_llm", rebuild)
    ctx = _CapturedReply()
    await handlers["set"]({"value": "sk-live"}, ctx)
    rebuild.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_set_strips_whitespace(
    handlers: dict[str, Any], fake_keyring: FakeKeyring, clean_env: None
) -> None:
    """Leading / trailing whitespace is dropped before persisting."""
    ctx = _CapturedReply()
    await handlers["set"]({"value": "  sk-spaced  \n"}, ctx)
    assert ctx.error_value is None
    assert fake_keyring.store[
        (secrets.KEYRING_SERVICE, secrets.KEYRING_USERNAME)
    ] == "sk-spaced"


@pytest.mark.asyncio
async def test_set_rejects_missing_value(
    handlers: dict[str, Any], fake_keyring: FakeKeyring
) -> None:
    ctx = _CapturedReply()
    await handlers["set"]({}, ctx)
    assert ctx.reply_value is None
    assert ctx.error_value is not None
    assert ctx.error_value["code"] == -32602
    assert "value" in ctx.error_value["message"]


@pytest.mark.asyncio
async def test_set_rejects_non_string_value(
    handlers: dict[str, Any], fake_keyring: FakeKeyring
) -> None:
    ctx = _CapturedReply()
    await handlers["set"]({"value": 42}, ctx)
    assert ctx.reply_value is None
    assert ctx.error_value is not None
    assert ctx.error_value["code"] == -32602


@pytest.mark.asyncio
async def test_set_rejects_empty_value(
    handlers: dict[str, Any], fake_keyring: FakeKeyring
) -> None:
    """Whitespace-only also counts as empty after strip."""
    ctx = _CapturedReply()
    await handlers["set"]({"value": "   "}, ctx)
    assert ctx.reply_value is None
    assert ctx.error_value is not None
    assert ctx.error_value["code"] == -32602


@pytest.mark.asyncio
async def test_clear_removes_keyring_entry(
    handlers: dict[str, Any], fake_keyring: FakeKeyring, clean_env: None
) -> None:
    fake_keyring.store[(secrets.KEYRING_SERVICE, secrets.KEYRING_USERNAME)] = "sk-doomed"
    ctx = _CapturedReply()
    await handlers["clear"](None, ctx)
    assert ctx.error_value is None
    assert ctx.reply_value == {"configured": False, "source": "none"}
    assert (secrets.KEYRING_SERVICE, secrets.KEYRING_USERNAME) not in fake_keyring.store


@pytest.mark.asyncio
async def test_clear_rebuilds_active_llm(
    handlers: dict[str, Any],
    fake_keyring: FakeKeyring,
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from minimax_code import app

    fake_keyring.store[(secrets.KEYRING_SERVICE, secrets.KEYRING_USERNAME)] = "sk-live"
    rebuild = AsyncMock()
    monkeypatch.setattr(app, "rebuild_subagent_llm", rebuild)
    ctx = _CapturedReply()
    await handlers["clear"](None, ctx)
    rebuild.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_clear_falls_through_to_env(
    handlers: dict[str, Any], fake_keyring: FakeKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clearing the keyring does NOT touch the env var — if it's
    still set, status should report ``source: env`` so the UI
    keeps the user informed about where the active key is
    coming from."""
    fake_keyring.store[(secrets.KEYRING_SERVICE, secrets.KEYRING_USERNAME)] = "sk-ring"
    monkeypatch.setenv(secrets.ENV_VAR, "sk-env-still-here")
    ctx = _CapturedReply()
    await handlers["clear"](None, ctx)
    assert ctx.reply_value == {"configured": True, "source": "env"}


@pytest.mark.asyncio
async def test_clear_is_idempotent(
    handlers: dict[str, Any], fake_keyring: FakeKeyring, clean_env: None
) -> None:
    """Clearing when nothing is stored is a no-op (status: none)."""
    ctx = _CapturedReply()
    await handlers["clear"](None, ctx)
    assert ctx.reply_value == {"configured": False, "source": "none"}


@pytest.mark.asyncio
async def test_set_propagates_backend_failure(
    handlers: dict[str, Any], fake_keyring: FakeKeyring, clean_env: None
) -> None:
    """A keyring write error is surfaced as a JSON-RPC ``-32603``
    so the UI can show a real error rather than silently
    dropping the key."""
    fake_keyring.fail = True
    ctx = _CapturedReply()
    await handlers["set"]({"value": "sk-will-fail"}, ctx)
    assert ctx.reply_value is None
    assert ctx.error_value is not None
    assert ctx.error_value["code"] == -32603
    assert "keyring" in ctx.error_value["message"].lower()


@pytest.mark.asyncio
async def test_set_then_status_round_trip(
    handlers: dict[str, Any], fake_keyring: FakeKeyring, clean_env: None
) -> None:
    """Full happy path: set → status reflects the new state."""
    set_ctx = _CapturedReply()
    await handlers["set"]({"value": "sk-rt"}, set_ctx)
    assert set_ctx.error_value is None

    status_ctx = _CapturedReply()
    await handlers["status"]({}, status_ctx)
    assert status_ctx.reply_value == {"configured": True, "source": "keyring"}
