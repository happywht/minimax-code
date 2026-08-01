"""Integration tests for real tree-sitter parsing in xai_codebase_graph.

These tests require the modern tree-sitter grammar packages to be installed.
They build a real :class:`ScopeGraphIndex` from temporary workspace files and
verify that definitions are extracted for Python and, when available, the
other supported languages.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from minimax_code.xai_codebase_graph.languages.registry import default_language_registry
from minimax_code.xai_codebase_graph.manager import IndexBuilder


def _write_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def test_build_index_from_python_file() -> None:
    """A Python file yields indexed definitions via real tree-sitter parsing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_file(
            os.path.join(tmpdir, "sample.py"),
            "class Foo:\n"
            "    def bar(self):\n"
            "        pass\n"
            "\n"
            "def baz():\n"
            "    Foo().bar()\n",
        )

        registry = default_language_registry()
        index = IndexBuilder.with_registry(registry).build(tmpdir)

        assert index.file_count() == 1
        assert index.has_definition("Foo")
        assert index.has_definition("bar")
        assert index.has_definition("baz")

        foo_defs = index.find_definitions("Foo")
        assert len(foo_defs) == 1
        assert foo_defs[0][0] == "sample.py"
        assert foo_defs[0][1] == 1


@pytest.mark.parametrize(
    ("filename", "content", "expected_defs"),
    [
        (
            "sample.ts",
            "function foo(): void {}\n"
            "class Bar {}\n"
            "foo();\n",
            {"foo", "Bar"},
        ),
        (
            "sample.js",
            "function foo() {}\n"
            "class Bar {}\n"
            "foo();\n",
            {"foo", "Bar"},
        ),
        (
            "sample.go",
            "package main\n"
            "func Foo() {}\n"
            "type Bar struct {}\n",
            {"Foo", "Bar"},
        ),
        (
            "sample.rs",
            "fn foo() {}\n"
            "struct Bar;\n",
            {"foo", "Bar"},
        ),
    ],
)
def test_build_index_from_other_languages(
    filename: str,
    content: str,
    expected_defs: set[str],
) -> None:
    """TypeScript, JavaScript, Go and Rust files also yield definitions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_file(os.path.join(tmpdir, filename), content)

        registry = default_language_registry()
        index = IndexBuilder.with_registry(registry).build(tmpdir)

        assert index.file_count() == 1
        for name in expected_defs:
            assert index.has_definition(name), f"expected definition for {name!r}"


def test_build_index_from_mixed_workspace() -> None:
    """A workspace with multiple languages indexes all supported files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_file(os.path.join(tmpdir, "a.py"), "def py_func(): pass\n")
        _write_file(os.path.join(tmpdir, "b.js"), "function jsFunc() {}\n")
        _write_file(os.path.join(tmpdir, "c.txt"), "ignore me\n")

        registry = default_language_registry()
        index = IndexBuilder.with_registry(registry).build(tmpdir)

        assert index.file_count() == 2
        assert index.has_definition("py_func")
        assert index.has_definition("jsFunc")
