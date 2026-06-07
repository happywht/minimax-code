"""Symbol extraction parsers for multiple languages.

Uses Python's ``ast`` module for .py files and regex-based extraction
for TypeScript/JavaScript, Java, and Go. No heavy dependencies
(tree-sitter, etc.) — just stdlib + regex.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass, field

__all__ = ["SymbolNode", "PARSERS"]


@dataclass
class SymbolNode:
    """A named code entity (module, class, function, etc.)."""

    name: str
    kind: str  # module, class, function, method, variable, interface, enum
    line: int
    children: list[SymbolNode] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Python parser (AST-based, reliable)
# ---------------------------------------------------------------------------


def extract_python_symbols(source: str, filename: str) -> list[SymbolNode]:
    """Extract class/function/method hierarchy from Python source."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []

    nodes: list[SymbolNode] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            methods = _py_class_members(node)
            nodes.append(SymbolNode(
                name=node.name,
                kind="class",
                line=node.lineno,
                children=methods,
            ))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nodes.append(SymbolNode(
                name=node.name,
                kind="function",
                line=node.lineno,
            ))
        elif isinstance(node, ast.Assign):
            # Top-level variable assignments: `NAME = ...`
            for target in node.targets:
                if isinstance(target, ast.Name):
                    nodes.append(SymbolNode(
                        name=target.id,
                        kind="variable",
                        line=node.lineno,
                    ))
    return nodes


def _py_class_members(cls_node: ast.ClassDef) -> list[SymbolNode]:
    """Extract methods and class-level attributes from a class."""
    members: list[SymbolNode] = []
    for node in ast.iter_child_nodes(cls_node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Detect @property, @staticmethod, @classmethod.
            decorators = {d.id if isinstance(d, ast.Name) else "" for d in node.decorator_list}
            kind = "method"
            if "property" in decorators:
                kind = "property"
            elif "staticmethod" in decorators:
                kind = "staticmethod"
            elif "classmethod" in decorators:
                kind = "classmethod"
            members.append(SymbolNode(
                name=node.name,
                kind=kind,
                line=node.lineno,
            ))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    members.append(SymbolNode(
                        name=target.id,
                        kind="variable",
                        line=node.lineno,
                    ))
    return members


# ---------------------------------------------------------------------------
# TypeScript / JavaScript parser (regex-based)
# ---------------------------------------------------------------------------

_TS_RE = re.compile(
    r"(?:^|\n)"
    r"(?:"
    r"(?:export\s+)?(?:default\s+)?(?:abstract\s+)?(?:async\s+)?class\s+(\w+)"
    r"|(?:export\s+)?(?:async\s+)?function\s+(\w+)"
    r"|(?:export\s+)?(?:const|let|var)\s+(\w+)\s*[:=]"
    r"|(?:export\s+)?interface\s+(\w+)"
    r"|(?:export\s+)?type\s+(\w+)\s*="
    r"|(?:export\s+)?enum\s+(\w+)"
    r")",
    re.MULTILINE,
)

_TS_METHOD_RE = re.compile(
    r"(?:public|private|protected)?\s*(?:async\s+)?(?:static\s+)?"
    r"(\w+)\s*\(",
    re.MULTILINE,
)


def extract_js_ts_symbols(source: str, filename: str) -> list[SymbolNode]:
    """Extract symbols from TypeScript/JavaScript source using regex."""
    nodes: list[SymbolNode] = []
    seen: set[str] = set()

    for i, line in enumerate(source.splitlines(), 1):
        m = _TS_RE.match(line) if not line.startswith(" ") and not line.startswith("\t") else None
        if m is None:
            # Try matching from the beginning of the line only.
            m = _TS_RE.match(line.lstrip())

        if m:
            name = m.group(1) or m.group(2) or m.group(3) or m.group(4) or m.group(5) or m.group(6)
            if name and name not in seen and not name.startswith("_"):
                # Determine kind from which group matched.
                if m.group(1):
                    kind = "class"
                elif m.group(2):
                    kind = "function"
                elif m.group(3):
                    kind = "variable"
                elif m.group(4):
                    kind = "interface"
                elif m.group(5):
                    kind = "type"
                elif m.group(6):
                    kind = "enum"
                else:
                    kind = "variable"
                nodes.append(SymbolNode(name=name, kind=kind, line=i))
                seen.add(name)

    return nodes


# ---------------------------------------------------------------------------
# Java parser (regex-based)
# ---------------------------------------------------------------------------

_JAVA_CLASS_RE = re.compile(
    r"(?:public|protected|private)?\s*(?:abstract\s+)?(?:final\s+)?"
    r"(?:class|interface|enum)\s+(\w+)",
    re.MULTILINE,
)

_JAVA_METHOD_RE = re.compile(
    r"(?:public|protected|private)?\s*(?:static\s+)?(?:final\s+)?"
    r"(?:abstract\s+)?(?:synchronized\s+)?(?:<[^>]+>\s+)?"
    r"(\w+(?:\[\])*)\s+(\w+)\s*\(",
    re.MULTILINE,
)


def extract_java_symbols(source: str, filename: str) -> list[SymbolNode]:
    """Extract class/interface/enum and method symbols from Java source."""
    nodes: list[SymbolNode] = []
    seen: set[str] = set()

    for i, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        m = _JAVA_CLASS_RE.match(stripped)
        if m:
            name = m.group(1)
            if name not in seen:
                nodes.append(SymbolNode(name=name, kind="class", line=i))
                seen.add(name)
            continue

        # Skip lines inside method bodies (indented > 2 levels).
        if stripped.startswith("    ") and not stripped.startswith("    public") and not stripped.startswith("    protected"):
            continue

        mm = _JAVA_METHOD_RE.match(stripped)
        if mm:
            method_name = mm.group(2)
            if method_name not in seen and method_name not in ("if", "for", "while", "switch", "catch"):
                nodes.append(SymbolNode(name=method_name, kind="method", line=i))
                seen.add(method_name)

    return nodes


# ---------------------------------------------------------------------------
# Go parser (regex-based)
# ---------------------------------------------------------------------------

_GO_FUNC_RE = re.compile(
    r"func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(",
    re.MULTILINE,
)

_GO_TYPE_RE = re.compile(
    r"type\s+(\w+)\s+(?:struct|interface)",
    re.MULTILINE,
)


def extract_go_symbols(source: str, filename: str) -> list[SymbolNode]:
    """Extract func and type symbols from Go source."""
    nodes: list[SymbolNode] = []
    seen: set[str] = set()

    for i, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()

        # Types first (struct/interface).
        m = _GO_TYPE_RE.match(stripped)
        if m:
            name = m.group(1)
            if name not in seen:
                nodes.append(SymbolNode(name=name, kind="type", line=i))
                seen.add(name)
            continue

        # Functions/methods.
        m = _GO_FUNC_RE.match(stripped)
        if m:
            name = m.group(1)
            if name not in seen:
                # Determine if it's a method (has receiver) or function.
                kind = "method" if "func (" in stripped[:10] else "function"
                nodes.append(SymbolNode(name=name, kind=kind, line=i))
                seen.add(name)

    return nodes


# ---------------------------------------------------------------------------
# Parser registry
# ---------------------------------------------------------------------------

PARSERS: dict[str, Callable[[str, str], list[SymbolNode]]] = {
    ".py": extract_python_symbols,
    ".js": extract_js_ts_symbols,
    ".jsx": extract_js_ts_symbols,
    ".ts": extract_js_ts_symbols,
    ".tsx": extract_js_ts_symbols,
    ".mjs": extract_js_ts_symbols,
    ".cjs": extract_js_ts_symbols,
    ".java": extract_java_symbols,
    ".go": extract_go_symbols,
}
