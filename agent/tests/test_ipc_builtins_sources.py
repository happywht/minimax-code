"""Tests for source-annotation extraction in the builtin IPC handlers."""

from __future__ import annotations

import pytest

from minimax_code.ipc.builtins import _extract_sources


def test_extract_sources_empty() -> None:
    assert _extract_sources(None) == []
    assert _extract_sources({}) == []
    assert _extract_sources([]) == []


def test_extract_sources_from_search_matches() -> None:
    output = {
        "query": "authenticate",
        "matches": [
            {
                "file_path": "auth.py",
                "start_line": 1,
                "end_line": 2,
                "source": "auth.py#L1-2",
                "snippet": "def auth(): ...",
            },
            {
                "file_path": "main.py",
                "start_line": 5,
                "end_line": 10,
                "source": "main.py#L5-10",
                "snippet": "auth()",
            },
        ],
    }
    assert _extract_sources(output) == [
        {"file_path": "auth.py", "line_range": "L1-2"},
        {"file_path": "main.py", "line_range": "L5-10"},
    ]


def test_extract_sources_from_summary() -> None:
    output = {
        "path": "auth.py",
        "kind": "file",
        "source": "auth.py#L1-2",
    }
    assert _extract_sources(output) == [
        {"file_path": "auth.py", "line_range": "L1-2"},
    ]


def test_extract_sources_from_symbol_result() -> None:
    output = {
        "symbol": "authenticate_user",
        "definitions": [
            {"file_path": "auth.py", "line": 1, "source": "auth.py#L1", "kind": "function"}
        ],
        "references": [
            {"file_path": "main.py", "line": 7, "source": "main.py#L7", "kind": "variable"}
        ],
    }
    assert _extract_sources(output) == [
        {"file_path": "auth.py", "line_range": "L1"},
        {"file_path": "main.py", "line_range": "L7"},
    ]


def test_extract_sources_deduplicates() -> None:
    output = {
        "matches": [
            {"source": "auth.py#L1-2"},
            {"source": "auth.py#L1-2"},
        ],
        "extra": {"source": "auth.py#L1-2"},
    }
    assert _extract_sources(output) == [
        {"file_path": "auth.py", "line_range": "L1-2"},
    ]


def test_extract_sources_handles_path_without_range() -> None:
    output = {"source": "README.md"}
    assert _extract_sources(output) == [
        {"file_path": "README.md", "line_range": None},
    ]


@pytest.mark.asyncio
async def test_system_prompt_extra_leads_with_stale_note_advisory() -> None:
    """v1.1.1: the system prompt always leads with the stale-note
    advisory. Acknowledgements of old run-loop nudges ("收到，立刻收尾…")
    persist in history and later turns imitate them; the advisory is the
    constant countermeasure and must be present even when no repo map
    and no memories are available."""
    from unittest.mock import patch

    from minimax_code.ipc.builtins import _build_system_prompt_extra

    async def _no_indexer() -> None:
        return None

    with (
        patch("minimax_code.app.ensure_repo_map_indexer", _no_indexer),
        patch("minimax_code.app.get_db", lambda: None),
    ):
        extra = await _build_system_prompt_extra(session_id="s", project_id=None)

    assert extra is not None
    assert extra.startswith("Stale-note advisory")
    assert "no longer apply" in extra
    assert "Do not imitate those phrases" in extra
