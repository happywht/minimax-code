"""Tests for the doc-generator skill tools.

Covers:
- ExtractApiSignaturesTool (functions, classes, methods, defaults)
- GenerateDocTool (bilingual, English-only, Chinese-only)
- Provider install/uninstall

v0.8.0 — Enterprise Multi-Agent.
"""

from __future__ import annotations

import textwrap

import pytest

from minimax_code.agent.skills._builtin.doc_generator import (
    ExtractApiSignaturesTool,
    GenerateDocTool,
    Provider,
)
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
    """Set MINIMAX_CODE_WORKSPACE so safe_resolve accepts tmp_path."""
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(tmp_path))


# ---------------------------------------------------------------------------
# ExtractApiSignaturesTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_function(tmp_path):
    _make_py_file(
        tmp_path,
        "funcs.py",
        textwrap.dedent("""\
        def add(x: int, y: int) -> int:
            \"\"\"Add two numbers.\"\"\"
            return x + y
        """),
    )
    tool = ExtractApiSignaturesTool()
    result = await tool.run(path=str(tmp_path))
    assert result.success
    sigs = result.output["signatures"]
    assert len(sigs) >= 1
    fn = next(s for s in sigs if s["name"] == "add")
    assert fn["kind"] == "function"
    assert "x" in fn["args"]
    assert fn["returns"] == "int"
    assert "Add two numbers" in fn["docstring"]


@pytest.mark.asyncio
async def test_extract_class_with_methods(tmp_path):
    _make_py_file(
        tmp_path,
        "cls.py",
        textwrap.dedent("""\
        class Calculator:
            \"\"\"A simple calculator.\"\"\"

            def add(self, x: int, y: int) -> int:
                \"\"\"Add two numbers.\"\"\"
                return x + y

            def _private(self):
                pass
        """),
    )
    tool = ExtractApiSignaturesTool()
    result = await tool.run(path=str(tmp_path))
    assert result.success
    sigs = result.output["signatures"]
    cls = next(s for s in sigs if s["kind"] == "class")
    assert cls["name"] == "Calculator"
    assert len(cls["methods"]) == 1  # _private excluded
    assert cls["methods"][0]["name"] == "add"


@pytest.mark.asyncio
async def test_extract_include_private(tmp_path):
    _make_py_file(
        tmp_path,
        "priv.py",
        textwrap.dedent("""\
        def _helper():
            pass
        """),
    )
    tool = ExtractApiSignaturesTool()
    result = await tool.run(path=str(tmp_path), include_private=True)
    assert result.success
    assert any(s["name"] == "_helper" for s in result.output["signatures"])


@pytest.mark.asyncio
async def test_extract_path_not_found(tmp_path):
    tool = ExtractApiSignaturesTool()
    # Use a path that IS within workspace but doesn't exist
    result = await tool.run(path=str(tmp_path / "nope.py"))
    assert not result.success
    assert "not found" in result.error


# ---------------------------------------------------------------------------
# GenerateDocTool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_bilingual():
    signatures = [
        {
            "kind": "function",
            "name": "hello",
            "args": "name: str",
            "returns": "str",
            "line": 1,
            "docstring": "Say hello.",
        }
    ]
    tool = GenerateDocTool()
    result = await tool.run(signatures=signatures, title="Test API", language="bilingual")
    assert result.success
    doc = result.output["document"]
    assert "# Test API" in doc
    assert "# Test API（中文）" in doc
    assert "hello" in doc


@pytest.mark.asyncio
async def test_generate_english_only():
    signatures = [
        {
            "kind": "class",
            "name": "MyClass",
            "line": 1,
            "docstring": "A class.",
            "bases": [],
            "methods": [],
        }
    ]
    tool = GenerateDocTool()
    result = await tool.run(signatures=signatures, language="en")
    assert result.success
    doc = result.output["document"]
    assert "MyClass" in doc
    # Should NOT have Chinese section
    assert "（中文）" not in doc


@pytest.mark.asyncio
async def test_generate_chinese_only():
    signatures = [
        {
            "kind": "function",
            "name": "process",
            "args": "data: list",
            "returns": "",
            "line": 1,
            "docstring": "Process data.",
        }
    ]
    tool = GenerateDocTool()
    result = await tool.run(signatures=signatures, title="API", language="zh")
    assert result.success
    doc = result.output["document"]
    assert "目录" in doc
    assert "process" in doc


@pytest.mark.asyncio
async def test_generate_invalid_signatures():
    tool = GenerateDocTool()
    result = await tool.run(signatures="not an array")
    assert not result.success
    assert "array" in result.error.lower()


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


def test_provider_install():
    registry = ToolRegistry()
    provider = Provider()
    added = provider.install(registry)
    assert added == {"extract_api_signatures", "generate_doc"}
    assert registry.has("extract_api_signatures")
    assert registry.has("generate_doc")


def test_provider_uninstall():
    registry = ToolRegistry()
    provider = Provider()
    provider.install(registry)
    provider.uninstall(registry)
    assert not registry.has("extract_api_signatures")
    assert not registry.has("generate_doc")


def test_provider_install_none():
    provider = Provider()
    assert provider.install(None) == set()
