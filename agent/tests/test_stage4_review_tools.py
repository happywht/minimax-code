"""Tests for Stage 4 multi-dimensional code review tools.

Covers:
- SecurityScanTool (AST-based: secrets, eval, pickle, shell=True)
- PerformanceCheckTool (sync-in-async, string concat, N+1)
- TypeCheckTool (AST annotation coverage fallback)
- TestCoverageTool (heuristic fallback)
- Migration 011 (seed review agents)
- Updated code_review Provider (6 tools)

v0.8.0 — Enterprise Multi-Agent.
"""

from __future__ import annotations

import textwrap

import pytest

from minimax_code.agent.skills._builtin.security import SecurityScanTool
from minimax_code.agent.skills._builtin.performance import PerformanceCheckTool
from minimax_code.agent.skills._builtin.type_check import TypeCheckTool
from minimax_code.agent.skills._builtin.test_coverage import TestCoverageTool
from minimax_code.agent.skills._builtin.code_review import Provider as CodeReviewProvider
from minimax_code.agent.tools.base import ToolRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_py_file(tmp_path, name: str, content: str) -> str:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return str(p)


@pytest.fixture(autouse=True)
def _set_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(tmp_path))


# ---------------------------------------------------------------------------
# SecurityScanTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_security_hardcoded_secret(tmp_path):
    _make_py_file(
        tmp_path,
        "secrets.py",
        textwrap.dedent("""\
        api_key = "sk-1234567890abcdef"
        password = "super_secret_pass"
        """),
    )
    tool = SecurityScanTool()
    result = await tool.run(path=str(tmp_path))
    assert result.success
    codes = [f["code"] for f in result.output["findings"]]
    assert "SEC001" in codes


@pytest.mark.asyncio
async def test_security_eval_exec(tmp_path):
    _make_py_file(
        tmp_path,
        "danger.py",
        textwrap.dedent("""\
        def run_code(code):
            eval(code)
            exec(code)
        """),
    )
    tool = SecurityScanTool()
    result = await tool.run(path=str(tmp_path))
    assert result.success
    codes = [f["code"] for f in result.output["findings"]]
    assert "SEC003" in codes


@pytest.mark.asyncio
async def test_security_pickle(tmp_path):
    _make_py_file(
        tmp_path,
        "pickle_usage.py",
        textwrap.dedent("""\
        import pickle
        data = pickle.loads(raw_bytes)
        """),
    )
    tool = SecurityScanTool()
    result = await tool.run(path=str(tmp_path))
    assert result.success
    codes = [f["code"] for f in result.output["findings"]]
    assert "SEC005" in codes


@pytest.mark.asyncio
async def test_security_shell_true(tmp_path):
    _make_py_file(
        tmp_path,
        "sub.py",
        textwrap.dedent("""\
        import subprocess
        subprocess.run(["ls"], shell=True)
        """),
    )
    tool = SecurityScanTool()
    result = await tool.run(path=str(tmp_path))
    assert result.success
    codes = [f["code"] for f in result.output["findings"]]
    assert "SEC004" in codes


@pytest.mark.asyncio
async def test_security_clean(tmp_path):
    _make_py_file(
        tmp_path,
        "clean.py",
        textwrap.dedent("""\
        def add(x: int, y: int) -> int:
            return x + y
        """),
    )
    tool = SecurityScanTool()
    result = await tool.run(path=str(tmp_path))
    assert result.success
    assert result.output["findings"] == []


# ---------------------------------------------------------------------------
# PerformanceCheckTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_perf_sync_in_async(tmp_path):
    _make_py_file(
        tmp_path,
        "blocking.py",
        textwrap.dedent("""\
        import time

        async def fetch_data():
            time.sleep(5)
        """),
    )
    tool = PerformanceCheckTool()
    result = await tool.run(path=str(tmp_path))
    assert result.success
    codes = [f["code"] for f in result.output["findings"]]
    assert "PERF001" in codes


@pytest.mark.asyncio
async def test_perf_string_concat_in_loop(tmp_path):
    _make_py_file(
        tmp_path,
        "concat.py",
        textwrap.dedent("""\
        result = ""
        for item in items:
            result += str(item)
        """),
    )
    tool = PerformanceCheckTool()
    result = await tool.run(path=str(tmp_path))
    assert result.success
    codes = [f["code"] for f in result.output["findings"]]
    assert "PERF002" in codes


@pytest.mark.asyncio
async def test_perf_n_plus_one(tmp_path):
    _make_py_file(
        tmp_path,
        "n1.py",
        textwrap.dedent("""\
        for user in users:
            db.get(user.id)
        """),
    )
    tool = PerformanceCheckTool()
    result = await tool.run(path=str(tmp_path))
    assert result.success
    codes = [f["code"] for f in result.output["findings"]]
    assert "PERF004" in codes


@pytest.mark.asyncio
async def test_perf_unnecessary_copy(tmp_path):
    _make_py_file(
        tmp_path,
        "copy.py",
        textwrap.dedent("""\
        items = list([1, 2, 3])
        data = dict({"a": 1})
        """),
    )
    tool = PerformanceCheckTool()
    result = await tool.run(path=str(tmp_path))
    assert result.success
    codes = [f["code"] for f in result.output["findings"]]
    assert "PERF003" in codes


# ---------------------------------------------------------------------------
# TypeCheckTool — AST fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_type_missing_annotations(tmp_path):
    _make_py_file(
        tmp_path,
        "untyped.py",
        textwrap.dedent("""\
        def process(data, count):
            return data * count
        """),
    )
    tool = TypeCheckTool()
    result = await tool.run(path=str(tmp_path))
    assert result.success
    # If mypy/pyright is available the tool delegates to it;
    # otherwise falls back to AST coverage check.
    checker = result.output.get("checker", "")
    if checker == "ast-coverage":
        codes = [f["code"] for f in result.output["findings"]]
        assert "TYPE001" in codes  # missing return
        assert "TYPE002" in codes  # missing param
    else:
        # External checker (mypy/pyright) — just verify we got results
        assert "findings" in result.output


@pytest.mark.asyncio
async def test_type_full_annotations(tmp_path):
    _make_py_file(
        tmp_path,
        "typed.py",
        textwrap.dedent("""\
        def add(x: int, y: int) -> int:
            return x + y
        """),
    )
    tool = TypeCheckTool()
    result = await tool.run(path=str(tmp_path))
    assert result.success
    stats = result.output.get("stats")
    if stats is not None:
        # AST fallback path
        assert stats["param_coverage_pct"] == 100.0
        assert stats["return_coverage_pct"] == 100.0
    else:
        # External checker — verify structure
        assert "findings" in result.output


# ---------------------------------------------------------------------------
# TestCoverageTool — heuristic fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coverage_heuristic(tmp_path, monkeypatch):
    # Force heuristic path by pretending pytest is not available
    monkeypatch.setattr("shutil.which", lambda _name: None)

    # Create a source file and its test
    _make_py_file(
        tmp_path,
        "calculator.py",
        textwrap.dedent("""\
        def add(x, y):
            return x + y
        """),
    )
    _make_py_file(
        tmp_path,
        "test_calculator.py",
        textwrap.dedent("""\
        def test_add():
            assert add(1, 2) == 3
        """),
    )
    tool = TestCoverageTool()
    result = await tool.run(path=str(tmp_path))
    assert result.success
    assert result.output["checker"] == "heuristic"
    assert result.output["source_count"] >= 1
    assert result.output["test_count"] >= 1


# ---------------------------------------------------------------------------
# Migration 011
# ---------------------------------------------------------------------------


def test_migration_011_seeds():
    """Migration 011 DDL should insert 3 review agents without error."""
    import sqlite3

    # Build a minimal agents table matching the post-migration-010 schema.
    # The INSERT OR IGNORE in migration 011 omits id/created_at, so
    # those columns must have defaults.
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE agents (
            id            TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(4)))),
            name          TEXT NOT NULL UNIQUE,
            system_prompt TEXT NOT NULL DEFAULT '',
            tool_allowlist JSON,
            model         TEXT,
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            description   TEXT NOT NULL DEFAULT '',
            enabled       INTEGER NOT NULL DEFAULT 1,
            icon          TEXT NOT NULL DEFAULT '',
            color         TEXT NOT NULL DEFAULT '',
            category      TEXT NOT NULL DEFAULT '',
            tags          TEXT,
            team_id       TEXT,
            skills        TEXT,
            max_iterations INTEGER NOT NULL DEFAULT 8,
            temperature   REAL,
            updated_at    TEXT
        );
    """)

    # Import and run migration 011 via importlib (module name starts with digit)
    import importlib
    mig011 = importlib.import_module(
        "minimax_code.storage.migrations.011_seed_review_agents"
    )
    mig011.run(db)

    rows = db.execute("SELECT name FROM agents WHERE category = 'review'").fetchall()
    names = {r[0] for r in rows}
    assert "security-reviewer" in names
    assert "performance-reviewer" in names
    assert "style-reviewer" in names
    db.close()


# ---------------------------------------------------------------------------
# CodeReview Provider — now registers 6 tools
# ---------------------------------------------------------------------------


def test_code_review_provider_six_tools():
    registry = ToolRegistry()
    provider = CodeReviewProvider()
    added = provider.install(registry)
    expected = {
        "run_linter",
        "find_complex_functions",
        "security_scan",
        "performance_check",
        "type_check",
        "test_coverage",
    }
    assert added == expected
    for name in expected:
        assert registry.has(name)


def test_code_review_provider_uninstall():
    registry = ToolRegistry()
    provider = CodeReviewProvider()
    provider.install(registry)
    provider.uninstall(registry)
    assert not registry.has("run_linter")
    assert not registry.has("security_scan")
