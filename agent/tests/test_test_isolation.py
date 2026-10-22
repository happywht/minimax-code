"""Verify the offline suite cannot silently use local credentials or the internet."""

import socket

import httpx
import keyring
import pytest

from minimax_code import secrets


def test_keyring_is_ephemeral_and_credentials_are_absent():
    keyring.set_password("minimax-code", "api_key", "not-a-real-key")
    assert keyring.get_password("minimax-code", "api_key") is None
    assert secrets.get_api_key() is None
    assert secrets.get_provider_key("builtin-minimax") is None


def test_offline_suite_rejects_external_dns_and_raw_sockets():
    with pytest.raises(RuntimeError, match="External DNS"):
        socket.getaddrinfo("example.com", 443)
    with socket.socket() as sock:
        with pytest.raises(RuntimeError, match="External network"):
            sock.connect(("192.0.2.1", 443))
        with pytest.raises(RuntimeError, match="External network"):
            sock.connect_ex(("192.0.2.1", 443))


def test_offline_suite_allows_loopback():
    with socket.socket() as server, socket.socket() as client:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        client.settimeout(1)
        client.connect(server.getsockname())
        connection, _ = server.accept()
        connection.close()


def test_http_replay_does_not_require_network():
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True}))) as client:
        assert client.get("https://example.com/recorded-response").json() == {"ok": True}
