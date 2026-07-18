"""Tests for the STT language layer (R31).

Mirrors grok-build's ``language.rs`` ``#[cfg(test)]`` module case-for-case —
every Rust test becomes a Python test with the same inputs and the same
assertions — then adds Python-specific guards: frozen-dataclass value
semantics and environment-driven locale resolution (monkeypatched for
determinism, since Windows hosts usually lack POSIX locale vars).
"""

from __future__ import annotations

import pytest

from minimax_code.voice import (
    STT_LANGUAGE_AUTO,
    STT_LANGUAGE_DEFAULT,
    STT_LANGUAGES,
    SttLanguage,
    canonicalize_stt_language,
    language_for_api,
    stt_language_by_code,
)

# Pins the public docs catalog (25 languages, docs last-updated May 2026).
DOCS_CODES = frozenset(
    {
        "ar", "cs", "da", "nl", "en", "fil", "fr", "de", "hi", "id", "it", "ja",
        "ko", "mk", "ms", "fa", "pl", "pt", "ro", "ru", "es", "sv", "th", "tr",
        "vi",
    }
)


# =========================================================================
# Catalog invariants (mirror grok's catalog_* tests)
# =========================================================================


def test_catalog_matches_public_docs_exactly():
    ours = {lang.code for lang in STT_LANGUAGES}
    assert ours == DOCS_CODES, "STT_LANGUAGES drifted from docs.x.ai supported languages"


def test_catalog_codes_are_unique_and_names_nonempty():
    seen: set[str] = set()
    for lang in STT_LANGUAGES:
        assert lang.code not in seen, f"duplicate STT language code {lang.code}"
        seen.add(lang.code)
        assert lang.name, f"empty name for {lang.code}"
        assert lang.code, "empty code"
        assert "-" not in lang.code, f"use primary codes only: {lang.code}"


def test_catalog_sorted_by_english_name():
    names = [lang.name for lang in STT_LANGUAGES]
    assert names == sorted(names), "STT_LANGUAGES must stay sorted by English name"


# =========================================================================
# canonicalize_stt_language (mirror grok's canonicalize_known_and_unknown)
# =========================================================================


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "en"),
        ("", "en"),
        ("  ", "en"),
        ("en", "en"),
        ("ES", "es"),
        ("  fr ", "fr"),
        ("auto", "auto"),
        ("AUTO", "auto"),
        ("en-US", "en"),
        ("pt_BR.UTF-8", "pt"),
        ("fil", "fil"),
        ("tl", "fil"),
        ("tl-PH", "fil"),
        # Chinese is not in the STT formatting catalog.
        ("zh", "en"),
        ("zh-Hans", "en"),
        ("nope", "en"),
    ],
)
def test_canonicalize_known_and_unknown(value, expected):
    assert canonicalize_stt_language(value) == expected


# =========================================================================
# language_for_api (mirror grok + Python locale-resolution guards)
# =========================================================================


@pytest.fixture
def _no_posix_locale(monkeypatch):
    """Strip POSIX locale vars so resolution is deterministic → default."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)


def test_language_for_api_never_returns_auto(_no_posix_locale):
    assert language_for_api("auto") != "auto"
    assert language_for_api("auto") == "en"
    assert language_for_api("ja") == "ja"
    assert language_for_api("EN") == "en"
    assert language_for_api("") == "en"
    assert language_for_api("xx") == "en"


def test_language_for_api_resolves_auto_from_locale(monkeypatch):
    """auto + LANG=ja_JP.UTF-8 → ja (POSIX precedence)."""
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "ja_JP.UTF-8")
    assert language_for_api("auto") == "ja"


def test_language_for_api_empty_lcall_falls_through_to_lang(monkeypatch):
    """An empty LC_ALL must not mask a usable LANG."""
    monkeypatch.setenv("LC_ALL", "")
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    assert language_for_api("auto") == "fr"


def test_language_for_api_lcall_beats_lang(monkeypatch):
    """LC_ALL wins over LANG when both are set and non-empty."""
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    assert language_for_api("auto") == "de"


def test_language_for_api_posix_locale_is_default(monkeypatch):
    """C / POSIX locales resolve to the default, not a code."""
    monkeypatch.setenv("LC_ALL", "POSIX")
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.delenv("LANG", raising=False)
    assert language_for_api("auto") == "en"


def test_language_for_api_alias_locale(monkeypatch):
    """A Tagalog system locale (tl) resolves to Filipino (fil) via the alias."""
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "tl_PH.UTF-8")
    assert language_for_api("auto") == "fil"


# =========================================================================
# stt_language_by_code (mirror grok's lookup_is_exact_code)
# =========================================================================


@pytest.mark.parametrize(
    ("code", "found"),
    [
        ("en", True),
        ("EN", False),  # exact match is case-sensitive
        ("auto", False),
        ("zh", False),
    ],
)
def test_lookup_is_exact_code(code, found):
    assert (stt_language_by_code(code) is not None) is found


def test_lookup_returns_matching_entry():
    lang = stt_language_by_code("ja")
    assert lang is not None
    assert lang.code == "ja"
    assert lang.name == "Japanese"


# =========================================================================
# Python-specific guards — frozen dataclass + constants
# =========================================================================


def test_stt_language_is_frozen_and_hashable():
    """Frozen dataclass: immutable, hashable, value-equal (Rust Copy + Eq)."""
    a = SttLanguage(code="en", name="English")
    b = SttLanguage(code="en", name="English")
    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b}) == 1  # hashable + value-equal → dedup in a set
    with pytest.raises((AttributeError, TypeError)):
        a.code = "fr"  # type: ignore[misc]


def test_stt_languages_is_immutable_tuple_of_25():
    assert isinstance(STT_LANGUAGES, tuple)
    assert len(STT_LANGUAGES) == 25


def test_constants():
    assert STT_LANGUAGE_AUTO == "auto"
    assert STT_LANGUAGE_DEFAULT == "en"
