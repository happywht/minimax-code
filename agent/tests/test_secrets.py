"""Tests for :mod:`minimax_code.secrets`.

These tests stub the ``keyring`` module's three entry points
(``get_password`` / ``set_password`` / ``delete_password``) so
they don't touch the real OS keyring backend. A small in-process
dict backs the fake keyring — the test is asserting *our
secrets.py logic* (keyring-first, env-var fallback, error
silencing on keyring failure), not the keyring library itself.
"""

from __future__ import annotations

from typing import Iterator

import pytest

from minimax_code import secrets


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeKeyring:
    """In-process keyring replacement.

    A small dict keyed by ``(service, username)`` so the tests can
    introspect what was written. The ``fail`` flag lets us
    simulate a backend that always errors out (e.g. headless CI
    without libsecret).
    """

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self.fail: bool = False
        self.set_calls: list[tuple[str, str, str]] = []
        self.delete_calls: list[tuple[str, str]] = []
        self.get_calls: list[tuple[str, str]] = []

    # keyring's public surface
    def get_password(self, service: str, username: str) -> str | None:
        self.get_calls.append((service, username))
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
    """Patch the three keyring functions our module uses."""
    fake = FakeKeyring()
    import keyring
    import keyring.errors
    monkeypatch.setattr(keyring, "get_password", fake.get_password)
    monkeypatch.setattr(keyring, "set_password", fake.set_password)
    monkeypatch.setattr(keyring, "delete_password", fake.delete_password)
    # Make sure the errors module is re-importable from secrets.py
    # without surprises — it is the same object, so this is just a
    # smoke test.
    assert keyring.errors.KeyringError is not None
    assert keyring.errors.PasswordDeleteError is not None
    yield fake


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Strip MINIMAX_API_KEY from the environment for one test."""
    monkeypatch.delenv(secrets.ENV_VAR, raising=False)
    yield


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_get_api_key_returns_none_when_nothing_set(
    fake_keyring: FakeKeyring, clean_env: None
) -> None:
    """No keyring entry + no env var → ``None`` (mock mode)."""
    assert secrets.get_api_key() is None
    # We did try the keyring, we just got nothing back.
    assert fake_keyring.get_calls == [(secrets.KEYRING_SERVICE, secrets.KEYRING_USERNAME)]


def test_get_api_key_falls_back_to_env_var(
    fake_keyring: FakeKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keyring empty → env var wins."""
    monkeypatch.setenv(secrets.ENV_VAR, "sk-env-9876")
    assert secrets.get_api_key() == "sk-env-9876"


def test_keyring_value_takes_precedence_over_env_var(
    fake_keyring: FakeKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When *both* are set, keyring wins (we never read the env var)."""
    fake_keyring.store[(secrets.KEYRING_SERVICE, secrets.KEYRING_USERNAME)] = "sk-ring-111"
    monkeypatch.setenv(secrets.ENV_VAR, "sk-env-222")
    assert secrets.get_api_key() == "sk-ring-111"


def test_set_then_get_round_trip(
    fake_keyring: FakeKeyring, clean_env: None
) -> None:
    """``set_api_key`` writes, ``get_api_key`` reads it back."""
    secrets.set_api_key("sk-roundtrip-abc")
    assert fake_keyring.store[
        (secrets.KEYRING_SERVICE, secrets.KEYRING_USERNAME)
    ] == "sk-roundtrip-abc"
    assert secrets.get_api_key() == "sk-roundtrip-abc"


def test_clear_removes_keyring_entry(
    fake_keyring: FakeKeyring, clean_env: None
) -> None:
    """``clear_api_key`` deletes the entry; ``get_api_key`` returns ``None`` after."""
    secrets.set_api_key("sk-temp-555")
    assert secrets.get_api_key() == "sk-temp-555"
    secrets.clear_api_key()
    assert secrets.get_api_key() is None
    assert (secrets.KEYRING_SERVICE, secrets.KEYRING_USERNAME) not in fake_keyring.store


def test_clear_is_idempotent(fake_keyring: FakeKeyring) -> None:
    """Clearing a missing entry is a no-op (does not raise)."""
    # Nothing was ever set — clear must not throw.
    secrets.clear_api_key()


def test_set_api_key_rejects_empty(fake_keyring: FakeKeyring) -> None:
    """Empty / blank values are rejected up front, not silently stored."""
    with pytest.raises(ValueError):
        secrets.set_api_key("")


def test_keyring_failure_falls_back_to_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the keyring backend errors out, we silently use the env var."""
    # Build a fake keyring whose get_password always raises.
    fake = FakeKeyring()
    fake.fail = True
    import keyring
    monkeypatch.setattr(keyring, "get_password", fake.get_password)
    monkeypatch.setattr(keyring, "set_password", fake.set_password)
    monkeypatch.setattr(keyring, "delete_password", fake.delete_password)
    monkeypatch.setenv(secrets.ENV_VAR, "sk-from-env-fallback")
    assert secrets.get_api_key() == "sk-from-env-fallback"


def test_set_api_key_propagates_backend_failure(
    fake_keyring: FakeKeyring, clean_env: None
) -> None:
    """Backend errors on set are surfaced — the caller asked to persist."""
    fake_keyring.fail = True
    import keyring.errors
    with pytest.raises(keyring.errors.KeyringError):
        secrets.set_api_key("sk-will-fail")


def test_builtin_provider_key_falls_back_to_legacy_global_key(
    fake_keyring: FakeKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The built-in MiniMax provider remains compatible with the legacy key."""
    monkeypatch.setenv(secrets.ENV_VAR, "sk-legacy-minimax")
    assert secrets.get_provider_key("builtin-minimax") == "sk-legacy-minimax"
    assert secrets.has_provider_key("builtin-minimax") is True


def test_custom_provider_does_not_reuse_legacy_minimax_key(
    fake_keyring: FakeKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A custom provider must have its own credential."""
    monkeypatch.setenv(secrets.ENV_VAR, "sk-legacy-minimax")
    assert secrets.get_provider_key("custom-openai") is None
    assert secrets.has_provider_key("custom-openai") is False


def test_custom_provider_uses_provider_specific_key(
    fake_keyring: FakeKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider-specific credentials still take precedence."""
    monkeypatch.setenv(secrets.ENV_VAR, "sk-legacy-minimax")
    fake_keyring.store[
        (secrets.KEYRING_SERVICE, "provider:custom-openai")
    ] = "sk-custom"
    assert secrets.get_provider_key("custom-openai") == "sk-custom"


# ---------------------------------------------------------------------------
# LLM integration smoke test
# ---------------------------------------------------------------------------


def test_minimax_client_uses_keyring(
    fake_keyring: FakeKeyring, clean_env: None
) -> None:
    """The LLM client reads through ``secrets.get_api_key`` — verify the wire."""
    from minimax_code.agent.llm import MiniMaxClient
    fake_keyring.store[(secrets.KEYRING_SERVICE, secrets.KEYRING_USERNAME)] = "sk-from-ring"
    client = MiniMaxClient()
    assert client.api_key == "sk-from-ring"
    assert client.mock is False


def test_minimax_client_env_fallback(
    fake_keyring: FakeKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No keyring value → env var → client picks it up."""
    from minimax_code.agent.llm import MiniMaxClient
    monkeypatch.setenv(secrets.ENV_VAR, "sk-env-llm")
    client = MiniMaxClient()
    assert client.api_key == "sk-env-llm"
    assert client.mock is False


def test_minimax_client_empty_falls_back_to_mock(
    fake_keyring: FakeKeyring, clean_env: None
) -> None:
    """No keyring, no env → empty key → mock mode kicks in."""
    from minimax_code.agent.llm import MiniMaxClient
    client = MiniMaxClient()
    assert client.api_key == ""
    assert client.mock is True


def test_minimax_client_explicit_api_key_still_wins(
    fake_keyring: FakeKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit ``api_key=`` kwarg must override both keyring and env."""
    from minimax_code.agent.llm import MiniMaxClient
    fake_keyring.store[(secrets.KEYRING_SERVICE, secrets.KEYRING_USERNAME)] = "sk-ring"
    monkeypatch.setenv(secrets.ENV_VAR, "sk-env")
    client = MiniMaxClient(api_key="sk-explicit")
    assert client.api_key == "sk-explicit"
