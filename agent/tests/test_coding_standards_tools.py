"""Tests for the coding-standards skill tools.

Covers:
- CheckStyleTool (heuristic mode, ruff mode when available)
- CheckNamingTool (PascalCase, snake_case, dunder skip)
- CheckDocstringTool (coverage, missing docstrings, style recognition)
- Provider install/uninstall

v0.8.0 — Enterprise Multi-Agent.
"""

from __future__ import annotations

import textwrap

import pytest

from minimax_code.agent.skills._builtin.coding_standards import (
    _GOOGLE_RE,
    _SPHINX_RE,
    CheckDocstringTool,
    CheckNamingTool,
    CheckStyleTool,
    Provider,
)
from minimax_code.agent.tools.base import ToolRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_py_file(tmp_path, name: str, content: str) -> str:
    """Write a Python file and return its path string."""
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return str(p)


@pytest.fixture(autouse=True)
def _set_workspace(tmp_path, monkeypatch):
    """Set MINIMAX_CODE_WORKSPACE so safe_resolve accepts tmp_path."""
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(tmp_path))


# ---------------------------------------------------------------------------
# CheckStyleTool — heuristic mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_style_heuristic_line_length(tmp_path):
    _make_py_file(
        tmp_path,
        "long_line.py",
        "x = " + "a" * 120 + "\n",
    )
    tool = CheckStyleTool()
    result = await tool.run(path=str(tmp_path))
    assert result.success
    findings = result.output["findings"]
    assert any(f["code"] == "E501" for f in findings)


@pytest.mark.asyncio
async def test_check_style_heuristic_trailing_ws(tmp_path):
    _make_py_file(
        tmp_path,
        "trail.py",
        "x = 1   \ny = 2\n",
    )
    tool = CheckStyleTool()
    result = await tool.run(path=str(tmp_path))
    assert result.success
    findings = result.output["findings"]
    assert any(f["code"] == "W291" for f in findings)


@pytest.mark.asyncio
async def test_check_style_path_not_found(tmp_path):
    tool = CheckStyleTool()
    result = await tool.run(path=str(tmp_path / "nonexistent.py"))
    assert not result.success
    assert "not found" in result.error


# ---------------------------------------------------------------------------
# CheckNamingTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_naming_bad_class(tmp_path):
    _make_py_file(
        tmp_path,
        "bad_class.py",
        textwrap.dedent("""\
        class my_class:
            pass
        """),
    )
    tool = CheckNamingTool()
    result = await tool.run(path=str(tmp_path))
    assert result.success
    findings = result.output["findings"]
    assert any(f["name"] == "my_class" and f["expected"] == "PascalCase" for f in findings)


@pytest.mark.asyncio
async def test_check_naming_bad_function(tmp_path):
    _make_py_file(
        tmp_path,
        "bad_func.py",
        textwrap.dedent("""\
        def BadFunction():
            pass
        """),
    )
    tool = CheckNamingTool()
    result = await tool.run(path=str(tmp_path))
    assert result.success
    findings = result.output["findings"]
    assert any(f["name"] == "BadFunction" and f["expected"] == "snake_case" for f in findings)


@pytest.mark.asyncio
async def test_check_naming_dunder_skip(tmp_path):
    _make_py_file(
        tmp_path,
        "dunder.py",
        textwrap.dedent("""\
        class MyClass:
            def __init__(self):
                pass
        """),
    )
    tool = CheckNamingTool()
    result = await tool.run(path=str(tmp_path))
    assert result.success
    findings = result.output["findings"]
    # __init__ should NOT be flagged
    assert not any(f["name"] == "__init__" for f in findings)


@pytest.mark.asyncio
async def test_check_naming_clean(tmp_path):
    _make_py_file(
        tmp_path,
        "clean.py",
        textwrap.dedent("""\
        class MyClass:
            def my_method(self):
                pass

        def my_function():
            pass
        """),
    )
    tool = CheckNamingTool()
    result = await tool.run(path=str(tmp_path))
    assert result.success
    assert result.output["findings"] == []


# ---------------------------------------------------------------------------
# CheckDocstringTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_docstring_missing(tmp_path):
    _make_py_file(
        tmp_path,
        "no_doc.py",
        textwrap.dedent("""\
        def public_func(x):
            return x + 1

        class MyClass:
            def method(self):
                pass

            def _private(self):
                pass
        """),
    )
    tool = CheckDocstringTool()
    result = await tool.run(path=str(tmp_path))
    assert result.success
    findings = result.output["findings"]
    names = [f["name"] for f in findings]
    assert "public_func" in names
    assert "MyClass" in names
    assert "method" in names  # public method, no docstring — flagged
    # Private method should NOT be flagged
    assert "_private" not in names
    assert result.output["stats"]["coverage_pct"] == 0.0


@pytest.mark.asyncio
async def test_check_docstring_full_coverage(tmp_path):
    _make_py_file(
        tmp_path,
        "full_doc.py",
        textwrap.dedent("""\
        def public_func(x):
            \"\"\"Do something.\"\"\"
            return x + 1

        class MyClass:
            \"\"\"A class.\"\"\"
            pass
        """),
    )
    tool = CheckDocstringTool()
    result = await tool.run(path=str(tmp_path))
    assert result.success
    assert result.output["stats"]["coverage_pct"] == 100.0


def test_check_docstring_style_google():
    """Google-style docstrings should be recognised."""
    docstring = textwrap.dedent("""\
    Do something.

    Args:
        x: input value

    Returns:
        incremented value
    """)
    assert _GOOGLE_RE.search(docstring) is not None


def test_check_docstring_style_sphinx():
    """:param style docstrings should be recognised."""
    docstring = textwrap.dedent("""\
    Do something.

    :param x: input value
    :returns: incremented value
    """)
    assert _SPHINX_RE.search(docstring) is not None


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


def test_provider_install():
    registry = ToolRegistry()
    provider = Provider()
    added = provider.install(registry)
    assert added == {"check_style", "check_naming", "check_docstring"}
    assert registry.has("check_style")
    assert registry.has("check_naming")
    assert registry.has("check_docstring")


def test_provider_uninstall():
    registry = ToolRegistry()
    provider = Provider()
    provider.install(registry)
    provider.uninstall(registry)
    assert not registry.has("check_style")
    assert not registry.has("check_naming")
    assert not registry.has("check_docstring")


def test_provider_install_none():
    provider = Provider()
    assert provider.install(None) == set()
