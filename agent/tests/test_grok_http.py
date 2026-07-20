"""Tests for grok_http (R193, ``xai-grok-http`` lib.rs pure-logic subset).

Mirrors grok-build's ``lib.rs`` inline ``#[cfg(test)]`` suite's four
pure-logic tests (the ``error_cause_chain`` reqwest test is YAGNI) plus
platform structural coverage. Product-fusion renames (``grok-shell`` ->
``minimax-code``; ``GROK_CLIENT_NAME`` -> ``MINIMAX_CODE_CLIENT_NAME``) are
asserted at the assertion level so drift is caught.
"""

from __future__ import annotations

import minimax_code.grok_http as grok_http
from minimax_code.grok_http import (
    CLIENT_MODE_HEADER,
    OriginClientInfo,
    PlatformInfo,
    TransportFailureKind,
    UserAgent,
    merge_origin_client_info,
    origin_client_info_from_env,
    origin_client_info_from_meta,
    process_client_identifier,
    process_user_agent_string,
    session_user_agent_string,
    user_agent_string_for,
)

# ---------------------------------------------------------------------------
# Structural: barrel surface + identity renames.
# ---------------------------------------------------------------------------


def test_barrel_exposes_fifteen_symbols() -> None:
    """15 migrated symbols (4 reqwest client builders + classify + escape-loop +
    error_cause_chain + startup_timer + 4 ClientType symbols all YAGNI)."""
    assert len(grok_http.__all__) == 15
    assert set(grok_http.__all__) == {
        "CLIENT_MODE_HEADER",
        "OriginClientInfo",
        "PlatformInfo",
        "TransportFailureKind",
        "UserAgent",
        "agent_version",
        "merge_origin_client_info",
        "origin_client_info_from_env",
        "origin_client_info_from_meta",
        "process_client_identifier",
        "process_client_mode",
        "process_user_agent_string",
        "session_user_agent_string",
        "set_process_client_mode_headless",
        "user_agent_string_for",
    }


def test_client_mode_header_renamed_to_platform_identity() -> None:
    """grok ``x-grok-client-mode`` -> ``x-minimax-code-client-mode``."""
    assert CLIENT_MODE_HEADER == "x-minimax-code-client-mode"


def test_transport_failure_kind_labels() -> None:
    """Bare label enum; ``classify`` (reqwest-coupled) is YAGNI."""
    assert TransportFailureKind.UNREACHABLE == "unreachable"
    assert TransportFailureKind.INTERRUPTED == "interrupted"
    assert TransportFailureKind.PERMANENT == "permanent"
    assert {kind.value for kind in TransportFailureKind} == {
        "unreachable",
        "interrupted",
        "permanent",
    }


# ---------------------------------------------------------------------------
# Rust inline test: origin_client_info_from_meta (identifier path + clientType
# fallback YAGNI). Mirrors lib.rs tests 2 + 3.
# ---------------------------------------------------------------------------


def test_origin_client_info_from_meta_extracts_identifier_and_version() -> None:
    meta = {"clientIdentifier": "grok-desktop", "clientVersion": "1.2.3"}
    assert origin_client_info_from_meta(meta) == OriginClientInfo(
        product="grok-desktop", version="1.2.3"
    )


def test_origin_client_info_from_meta_strips_non_string_version() -> None:
    """clientVersion must be a JSON string; numeric/null values drop to None."""
    assert origin_client_info_from_meta({"clientIdentifier": "x", "clientVersion": 1}) == (
        OriginClientInfo(product="x", version=None)
    )


def test_origin_client_info_from_meta_client_type_fallback_is_yagni() -> None:
    """grok falls back to deserializing ``clientType`` via the ``ClientType`` enum
    when ``clientIdentifier`` is absent; ``ClientType`` is not migrated, so an
    absent identifier yields ``None`` even with ``clientType`` present."""
    meta = {"clientType": "grok_pager", "clientVersion": "0.1.2"}
    assert origin_client_info_from_meta(meta) is None


def test_origin_client_info_from_meta_none_meta_returns_none() -> None:
    assert origin_client_info_from_meta(None) is None


# ---------------------------------------------------------------------------
# Rust inline test: merge_origin_client_info (lib.rs test 4).
# ---------------------------------------------------------------------------


def test_merge_origin_client_info_preserves_primary_product_and_backfills_version() -> None:
    merged = merge_origin_client_info(
        OriginClientInfo(product="grok-web", version=None),
        OriginClientInfo(product="grok-desktop", version="1.2.3"),
    )
    assert merged == OriginClientInfo(product="grok-web", version="1.2.3")


def test_merge_origin_client_info_passes_through_single_side() -> None:
    primary = OriginClientInfo(product="grok-web", version="9.9")
    fallback = OriginClientInfo(product="grok-desktop", version="1.2.3")
    assert merge_origin_client_info(primary, None) == primary
    assert merge_origin_client_info(None, fallback) == fallback
    assert merge_origin_client_info(None, None) is None


def test_merge_origin_client_info_keeps_primary_version_when_present() -> None:
    """Primary's own version wins; fallback is only consulted when primary is None."""
    merged = merge_origin_client_info(
        OriginClientInfo(product="grok-web", version="2.0"),
        OriginClientInfo(product="grok-desktop", version="1.2.3"),
    )
    assert merged == OriginClientInfo(product="grok-web", version="2.0")


# ---------------------------------------------------------------------------
# Rust inline test: session_user_agent_string (lib.rs test 5) + render collapse
# (lib.rs test 6). Agent product is the platform (``minimax-code``).
# ---------------------------------------------------------------------------


def test_session_user_agent_string_renders_expected_variants() -> None:
    with_version = session_user_agent_string(
        OriginClientInfo(product="grok-desktop", version="1.2.3")
    )
    assert with_version.startswith("grok-desktop/1.2.3 minimax-code/")
    assert " (" in with_version

    without_version = session_user_agent_string(
        OriginClientInfo(product="grok-web", version=None)
    )
    assert without_version.startswith("grok-web minimax-code/")
    assert not without_version.startswith("grok-web/")


def test_user_agent_render_collapses_duplicate_origin_and_agent_identity() -> None:
    """When origin == agent (product + version) the UA collapses to a single token."""
    ua = UserAgent(
        origin=OriginClientInfo(product="minimax-code", version="0.1.171"),
        agent_product="minimax-code",
        agent_version="0.1.171",
        platform=PlatformInfo(os="macos", arch="aarch64"),
    )
    assert ua.render() == "minimax-code/0.1.171 (macos; aarch64)"


def test_user_agent_string_for_aliases_session_user_agent_string() -> None:
    origin = OriginClientInfo(product="grok-desktop", version="1.2.3")
    assert user_agent_string_for(origin) == session_user_agent_string(origin)


# ---------------------------------------------------------------------------
# Platform identity: env vars + process identifier + PlatformInfo normalization.
# ---------------------------------------------------------------------------


def test_origin_client_info_from_env_reads_renamed_vars(monkeypatch) -> None:
    monkeypatch.delenv("MINIMAX_CODE_CLIENT_NAME", raising=False)
    assert origin_client_info_from_env() is None

    monkeypatch.setenv("MINIMAX_CODE_CLIENT_NAME", "grok-desktop")
    monkeypatch.delenv("MINIMAX_CODE_CLIENT_VERSION", raising=False)
    assert origin_client_info_from_env() == OriginClientInfo(
        product="grok-desktop", version=None
    )

    monkeypatch.setenv("MINIMAX_CODE_CLIENT_VERSION", "1.2.3")
    assert origin_client_info_from_env() == OriginClientInfo(
        product="grok-desktop", version="1.2.3"
    )


def test_process_client_identifier_defaults_to_platform_identity(monkeypatch) -> None:
    """grok defaulted to ``grok-shell``; platform defaults to ``minimax-code``."""
    monkeypatch.delenv("MINIMAX_CODE_CLIENT_NAME", raising=False)
    assert process_client_identifier() == "minimax-code"

    monkeypatch.setenv("MINIMAX_CODE_CLIENT_NAME", "grok-pager")
    assert process_client_identifier() == "grok-pager"


def test_process_user_agent_string_uses_platform_identity_when_env_unset(monkeypatch) -> None:
    """Origin env unset -> default ``minimax-code`` identifier collapses the UA
    (origin == agent product+version)."""
    monkeypatch.delenv("MINIMAX_CODE_CLIENT_NAME", raising=False)
    monkeypatch.delenv("MINIMAX_CODE_CLIENT_VERSION", raising=False)
    ua = process_user_agent_string()
    assert ua.startswith("minimax-code/")
    assert " (" in ua


def test_platform_info_current_normalizes_to_grok_labels() -> None:
    """``platform.system()`` / ``platform.machine()`` map onto grok's
    ``std::env::consts::{OS,ARCH}`` labels (macos/windows/linux; aarch64/x86_64)."""
    info = PlatformInfo.current()
    assert info.os in {"macos", "windows", "linux"}
    assert info.arch in {"aarch64", "x86_64"}


# ---------------------------------------------------------------------------
# OriginClientInfo value semantics.
# ---------------------------------------------------------------------------


def test_origin_client_info_is_frozen_and_hashable() -> None:
    a = OriginClientInfo(product="x", version="1")
    b = OriginClientInfo(product="x", version="1")
    assert a == b
    assert hash(a) == hash(b)
    assert a.version is not None
    assert OriginClientInfo(product="x").version is None
