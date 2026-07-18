"""Tests for the voice config table + TLS-only URL builder (R33).

Ports grok's 7 ``#[cfg(test)]`` cases from ``config.rs`` verbatim (using
``tomllib`` for the TOML fixtures) and adds Python-specific guards on the
``ws_url`` edge cases and the identity-field anti-spoof discipline.
"""

from __future__ import annotations

import tomllib

import pytest

from minimax_code.voice import (
    STT_LANGUAGE_DEFAULT,
    VoiceConfig,
    VoiceConfigError,
    from_config_table,
    ws_url,
)
from minimax_code.voice.config import VoiceConfig as VoiceConfigFromModule


def test_reexport():
    assert VoiceConfig is VoiceConfigFromModule


def test_default_stt_ws_uses_wss():
    assert VoiceConfig().stt_ws_url() == "wss://api.x.ai/v1/stt"


def test_scheme_less_api_base_uses_wss():
    cfg = VoiceConfig(api_base="api.x.ai")
    assert cfg.stt_ws_url() == "wss://api.x.ai/v1/stt"


def test_wss_api_base_is_not_doubled():
    cfg = VoiceConfig(api_base="wss://api.x.ai")
    assert cfg.stt_ws_url() == "wss://api.x.ai/v1/stt"


def test_https_api_base_strips_scheme():
    cfg = VoiceConfig(api_base="https://api.x.ai")
    assert cfg.stt_ws_url() == "wss://api.x.ai/v1/stt"


def test_http_api_base_is_rejected_not_downgraded():
    """TLS-only: http:// is rejected, never silently downgraded to wss://."""
    cfg = VoiceConfig(api_base="http://localhost:8080")
    with pytest.raises(VoiceConfigError) as exc_info:
        cfg.stt_ws_url()
    assert "insecure" in str(exc_info.value)


def test_ws_api_base_is_rejected():
    cfg = VoiceConfig(api_base="ws://localhost:8080")
    with pytest.raises(VoiceConfigError):
        cfg.stt_ws_url()


def test_trailing_slash_and_path_prefix_stripped():
    assert ws_url("https://api.x.ai/", "/v1/stt") == "wss://api.x.ai/v1/stt"


def test_ignores_additional_fields():
    """Legacy/unknown keys (removed `enabled` opt-out) drop silently — no deny_unknown_fields."""
    raw = """
[voice]
enabled = false
push_to_talk = true
language = "es"
"""
    table = tomllib.loads(raw)
    cfg = from_config_table(table)
    assert cfg.language == "es"
    assert cfg.sample_rate == 16_000


def test_identity_fields_are_not_parsed_from_config():
    """client_identifier / user_agent are #[serde(skip)] — anti-spoof: user can't forge them."""
    raw = """
[voice]
client_identifier = "spoofed"
user_agent = "malicious/9.9"
language = "es"
"""
    table = tomllib.loads(raw)
    cfg = from_config_table(table)
    assert cfg.language == "es"
    assert cfg.client_identifier == ""
    assert cfg.user_agent == ""


def test_missing_voice_table_returns_defaults():
    assert from_config_table({}) == VoiceConfig()


def test_partial_overrides_keep_other_defaults():
    cfg = from_config_table({"voice": {"sample_rate": 8000}})
    assert cfg.sample_rate == 8000
    assert cfg.language == STT_LANGUAGE_DEFAULT
    assert cfg.stt_endpointing_ms == 400


def test_default_fields_match_grok():
    cfg = VoiceConfig()
    assert cfg.api_base == "https://api.x.ai"
    assert cfg.stt_ws_path == "/v1/stt"
    assert cfg.language == "en"
    assert cfg.sample_rate == 16_000
    assert cfg.stt_endpointing_ms == 400
    assert cfg.stt_interim_results is True
    assert cfg.client_identifier == ""
    assert cfg.user_agent == ""
