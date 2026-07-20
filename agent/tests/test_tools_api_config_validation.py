"""Tests for tools_api.config_validation (R191).

Mirrors grok-build's ``config_validation.rs`` inline ``#[cfg(test)]`` suite
(9 tests) plus platform structural / display / equality coverage. Rust uses
``Result`` with ``.unwrap()`` / ``.unwrap_err()``; Python surfaces the error as
a raised :class:`ToolConfigEntryError`, so the suite uses ``pytest.raises``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

import minimax_code.tools_api as tools_api
from minimax_code.tools_api import config_validation
from minimax_code.tools_api.config_validation import (
    NameOverrideInvalid,
    ParamsJsonNotObject,
    ParamsJsonParse,
    ToolConfigEntryError,
    first_unknown_tool_id,
    parse_params_json,
    validate_name_override,
)


# Minimal ToolConfigEntry stub: the real type is a pb wire type (YAGNI on the
# platform); only .id is read, so a frozen dataclass stand-in exercises the
# duck-typed _ToolConfigEntryLike contract.
@dataclass(frozen=True)
class _Entry:
    id: str


def _allowed(ids: list[str]) -> set[str]:
    return set(ids)


# ---------------------------------------------------------------------------
# Structural: barrel exposes the submodule; __all__ surface.
# ---------------------------------------------------------------------------


def test_barrel_exposes_config_validation_submodule() -> None:
    assert tools_api.config_validation is config_validation
    assert "config_validation" in tools_api.__all__


def test_config_validation_all_surface_is_eight_symbols() -> None:
    assert len(config_validation.__all__) == 8
    assert set(config_validation.__all__) == {
        "NameOverrideInvalid",
        "ParamsJsonNotObject",
        "ParamsJsonParse",
        "ToolConfigEntryError",
        "ToolConfigEntryErrorKind",
        "first_unknown_tool_id",
        "parse_params_json",
        "validate_name_override",
    }


# ---------------------------------------------------------------------------
# parse_params_json (Rust 5 inline tests).
# ---------------------------------------------------------------------------


def test_unset_params_is_ok_none() -> None:
    assert parse_params_json(0, "GrokBuild:grep", None) is None


def test_valid_object_is_returned() -> None:
    parsed = parse_params_json(0, "GrokBuild:grep", '{"max_results":50}')
    assert parsed == {"max_results": 50}


def test_empty_string_is_a_parse_error() -> None:
    with pytest.raises(ToolConfigEntryError) as exc:
        parse_params_json(3, "GrokBuild:grep", "")
    err = exc.value
    assert err.index == 3
    assert err.field_path() == "tools[3].params_json"
    assert isinstance(err.kind, ParamsJsonParse)


def test_invalid_json_is_a_parse_error() -> None:
    with pytest.raises(ToolConfigEntryError) as exc:
        parse_params_json(0, "t", "{not json")
    err = exc.value
    assert isinstance(err.kind, ParamsJsonParse)
    assert err.kind.raw == "{not json"


def test_non_object_json_is_rejected() -> None:
    with pytest.raises(ToolConfigEntryError) as exc:
        parse_params_json(1, "t", "[1,2,3]")
    err = exc.value
    assert isinstance(err.kind, ParamsJsonNotObject)
    assert err.kind.value == [1, 2, 3]


# ---------------------------------------------------------------------------
# validate_name_override (Rust 2 inline tests).
# ---------------------------------------------------------------------------


def test_name_override_unset_or_valid_is_ok() -> None:
    assert validate_name_override(0, "GrokBuild:grep", None) is None
    for name in ["search", "GrokBuild:grep", "a-b_C9"]:
        assert validate_name_override(0, "GrokBuild:grep", name) is None


def test_name_override_outside_charset_is_rejected() -> None:
    for name in ["has space", "", "a:b:c", "dot.name"]:
        with pytest.raises(ToolConfigEntryError) as exc:
            validate_name_override(2, "GrokBuild:grep", name)
        err = exc.value
        assert err.index == 2
        assert err.field_path() == "tools[2].name_override"
        assert isinstance(err.kind, NameOverrideInvalid)
        assert err.kind.name == name


# ---------------------------------------------------------------------------
# first_unknown_tool_id (Rust 3 inline tests).
# ---------------------------------------------------------------------------


def test_all_ids_present_returns_none() -> None:
    entries = [_Entry("GrokBuild:grep"), _Entry("GrokBuild:read_file")]
    allowed = _allowed(["GrokBuild:grep", "GrokBuild:read_file", "GrokBuild:bash"])
    assert first_unknown_tool_id(entries, allowed) is None


def test_empty_entries_returns_none() -> None:
    assert first_unknown_tool_id([], _allowed(["GrokBuild:grep"])) is None


def test_first_unknown_id_is_returned_with_index() -> None:
    entries = [
        _Entry("GrokBuild:grep"),
        _Entry("GrokBuild:nonexistent"),
        _Entry("GrokBuild:also_missing"),
    ]
    allowed = _allowed(["GrokBuild:grep"])
    assert first_unknown_tool_id(entries, allowed) == (1, "GrokBuild:nonexistent")


# ---------------------------------------------------------------------------
# Platform: Display (Rust impl std::fmt::Display) + structural equality.
# ---------------------------------------------------------------------------


def test_display_formats_match_rust() -> None:
    parse_err = ToolConfigEntryError(0, "t", ParamsJsonParse(error="boom", raw="!"))
    assert str(parse_err) == "t: tools[0].params_json failed to parse JSON: boom"

    not_obj = ToolConfigEntryError(1, "t", ParamsJsonNotObject(value=[1]))
    assert str(not_obj) == "t: tools[1].params_json must be a JSON object"

    name_err = ToolConfigEntryError(
        2, "t", NameOverrideInvalid(name="bad name", error="fmt")
    )
    assert (
        str(name_err)
        == "t: tools[2].name_override is not a valid tool name ('bad name'): fmt"
    )


def test_error_equality_is_structural() -> None:
    a = ToolConfigEntryError(0, "t", ParamsJsonParse(error="e", raw="r"))
    b = ToolConfigEntryError(0, "t", ParamsJsonParse(error="e", raw="r"))
    c = ToolConfigEntryError(1, "t", ParamsJsonParse(error="e", raw="r"))
    assert a == b
    assert a != c
