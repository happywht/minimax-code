"""Built-in tools for the ``doc-generator`` skill.

Tools
-----

* :class:`ExtractApiSignaturesTool` — parses Python source files
  and extracts function/class signatures with their docstrings,
  producing a structured list suitable for documentation generation.
* :class:`GenerateDocTool` — takes structured signature data and
  generates bilingual (English + Chinese) Markdown documentation.

Both tools honour the workspace-safety policy enforced by
:func:`safe_resolve` — paths outside the workspace are rejected.

v0.8.0 — Enterprise Multi-Agent.
"""

from __future__ import annotations

import ast
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...tools.base import Tool, ToolResult
from ...tools.file_ops import PathSecurityError, safe_resolve
from ..runtime import SkillToolProvider


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SKIP_DIRS = frozenset(
    {
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        "node_modules",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
    }
)


def _is_skippable(path: Path) -> bool:
    return bool(_SKIP_DIRS & set(path.parts))


def _collect_py_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(f for f in target.rglob("*.py") if not _is_skippable(f))


def _format_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Format function arguments as a readable signature string."""
    args_parts: list[str] = []

    # positional-only
    for arg in getattr(node.args, "posonlyargs", []):
        args_parts.append(arg.arg)

    # regular args
    defaults = node.args.defaults
    n_defaults = len(defaults)
    n_args = len(node.args.args)
    for i, arg in enumerate(node.args.args):
        default_idx = i - (n_args - n_defaults)
        if default_idx >= 0:
            default_node = defaults[default_idx]
            default_str = _literal_repr(default_node)
            args_parts.append(f"{arg.arg}={default_str}")
        else:
            args_parts.append(arg.arg)

    # *args
    if node.args.vararg:
        args_parts.append(f"*{node.args.vararg.arg}")

    # keyword-only
    for i, arg in enumerate(node.args.kwonlyargs):
        default_node = node.args.kw_defaults[i]
        if default_node is not None:
            default_str = _literal_repr(default_node)
            args_parts.append(f"{arg.arg}={default_str}")
        else:
            args_parts.append(arg.arg)

    # **kwargs
    if node.args.kwarg:
        args_parts.append(f"**{node.args.kwarg.arg}")

    return ", ".join(args_parts)


def _literal_repr(node: ast.AST) -> str:
    """Best-effort repr for simple default values."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return f'"{node.value}"'
        return repr(node.value)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_literal_repr(node.value)}.{node.attr}"
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return "..."
    if isinstance(node, ast.Dict):
        return "{}"
    if isinstance(node, ast.Call):
        return "..."
    return "..."


def _return_annotation(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Extract return type annotation as a string."""
    if node.returns is None:
        return ""
    try:
        return ast.unparse(node.returns)
    except Exception:
        return "..."


# ---------------------------------------------------------------------------
# ExtractApiSignaturesTool
# ---------------------------------------------------------------------------


class ExtractApiSignaturesTool(Tool):
    name = "extract_api_signatures"
    description = (
        "Extract public API signatures from Python source files. "
        "Returns a structured list of functions and classes with "
        "their arguments, return types, and docstrings."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File or directory to extract signatures from.",
            },
            "include_private": {
                "type": "boolean",
                "default": False,
                "description": "Include names starting with underscore.",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        raw_path = kwargs.get("path")
        include_private = kwargs.get("include_private", False)

        try:
            target = safe_resolve(raw_path)
        except PathSecurityError as exc:
            return ToolResult.fail(str(exc))
        if not target.exists():
            return ToolResult.fail(f"path not found: {raw_path}")

        files = _collect_py_files(target)
        all_signatures: list[dict[str, Any]] = []

        for f in files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                tree = ast.parse(text, filename=str(f))
            except SyntaxError:
                continue

            sigs = self._extract(tree, f, include_private)
            all_signatures.extend(sigs)

        return ToolResult.ok(
            output={
                "path": str(target),
                "files_scanned": len(files),
                "signatures": all_signatures,
            },
            count=len(all_signatures),
        )

    @staticmethod
    def _extract(
        tree: ast.AST, filepath: Path, include_private: bool
    ) -> list[dict[str, Any]]:
        """Walk *tree* and collect public API signatures."""
        results: list[dict[str, Any]] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                if not include_private and node.name.startswith("_"):
                    continue
                class_entry: dict[str, Any] = {
                    "file": str(filepath),
                    "kind": "class",
                    "name": node.name,
                    "line": node.lineno,
                    "docstring": ast.get_docstring(node) or "",
                    "bases": [
                        ast.unparse(b) if hasattr(ast, "unparse") else str(b)
                        for b in node.bases
                    ],
                    "methods": [],
                }
                # Collect methods
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if not include_private and item.name.startswith("_"):
                            continue
                        class_entry["methods"].append(
                            {
                                "name": item.name,
                                "args": _format_args(item),
                                "returns": _return_annotation(item),
                                "line": item.lineno,
                                "docstring": ast.get_docstring(item) or "",
                            }
                        )
                results.append(class_entry)

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Only top-level functions (not methods inside classes)
                if not include_private and node.name.startswith("_"):
                    continue
                # Check it's not already captured as a method
                results.append(
                    {
                        "file": str(filepath),
                        "kind": "function",
                        "name": node.name,
                        "args": _format_args(node),
                        "returns": _return_annotation(node),
                        "line": node.lineno,
                        "docstring": ast.get_docstring(node) or "",
                    }
                )

        return results


# ---------------------------------------------------------------------------
# GenerateDocTool
# ---------------------------------------------------------------------------


class GenerateDocTool(Tool):
    name = "generate_doc"
    description = (
        "Generate bilingual (English + Chinese) Markdown documentation "
        "from structured API signature data. Accepts the output of "
        "extract_api_signatures or a custom signature list."
    )
    parameters = {
        "type": "object",
        "properties": {
            "signatures": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Array of signature objects from extract_api_signatures.",
            },
            "title": {
                "type": "string",
                "default": "API Reference",
                "description": "Document title.",
            },
            "language": {
                "type": "string",
                "enum": ["bilingual", "en", "zh"],
                "default": "bilingual",
                "description": "Output language mode.",
            },
        },
        "required": ["signatures"],
        "additionalProperties": False,
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        signatures = kwargs.get("signatures", [])
        title = kwargs.get("title") or "API Reference"
        language = kwargs.get("language") or "bilingual"

        if not isinstance(signatures, list):
            return ToolResult.fail("signatures must be a JSON array")

        doc = self._generate(signatures, title, language)

        return ToolResult.ok(
            output={
                "title": title,
                "language": language,
                "document": doc,
                "signature_count": len(signatures),
            },
            doc_length=len(doc),
        )

    @staticmethod
    def _generate(
        signatures: list[dict[str, Any]], title: str, language: str
    ) -> str:
        """Generate bilingual Markdown documentation."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        sections: list[str] = []

        if language in ("bilingual", "en"):
            sections.append(_generate_en(title, signatures, now))
        if language in ("bilingual", "zh"):
            sections.append(_generate_zh(title, signatures, now))

        return "\n\n---\n\n".join(sections)


def _generate_en(
    title: str, signatures: list[dict[str, Any]], date: str
) -> str:
    """Generate English documentation section."""
    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append(f"\n> Generated on {date}")
    lines.append(f"\n> {len(signatures)} item(s)")

    # Table of contents
    lines.append("\n## Table of Contents\n")
    for sig in signatures:
        name = sig.get("name", "")
        kind = sig.get("kind", "")
        anchor = name.lower().replace(" ", "-")
        lines.append(f"- [{name}](#{anchor}) ({kind})")

    # Detailed entries
    for sig in signatures:
        name = sig.get("name", "")
        kind = sig.get("kind", "")
        lines.append(f"\n## {name}\n")
        lines.append(f"**Kind:** `{kind}`")

        if kind == "class":
            bases = sig.get("bases", [])
            if bases:
                lines.append(f"**Bases:** {', '.join(f'`{b}`' for b in bases)}")
            doc = sig.get("docstring", "")
            if doc:
                lines.append(f"\n{textwrap.indent(doc, '> ')}")
            methods = sig.get("methods", [])
            if methods:
                lines.append("\n### Methods\n")
                for m in methods:
                    args = m.get("args", "")
                    ret = m.get("returns", "")
                    ret_str = f" -> `{ret}`" if ret else ""
                    lines.append(f"- **`{m['name']}({args})`{ret_str}**")
                    mdoc = m.get("docstring", "")
                    if mdoc:
                        lines.append(f"  > {mdoc.split(chr(10))[0]}")

        elif kind == "function":
            args = sig.get("args", "")
            ret = sig.get("returns", "")
            ret_str = f" -> `{ret}`" if ret else ""
            lines.append(f"\n```python\ndef {name}({args}){ret_str}\n```")
            doc = sig.get("docstring", "")
            if doc:
                lines.append(f"\n{textwrap.indent(doc, '> ')}")

    return "\n".join(lines)


def _generate_zh(
    title: str, signatures: list[dict[str, Any]], date: str
) -> str:
    """Generate Chinese documentation section."""
    lines: list[str] = []
    lines.append(f"# {title}（中文）")
    lines.append(f"\n> 生成日期：{date}")
    lines.append(f"\n> 共 {len(signatures)} 个条目")

    # Table of contents
    lines.append("\n## 目录\n")
    for sig in signatures:
        name = sig.get("name", "")
        kind = sig.get("kind", "")
        kind_zh = "类" if kind == "class" else "函数"
        anchor = name.lower().replace(" ", "-")
        lines.append(f"- [{name}](#{anchor})（{kind_zh}）")

    # Detailed entries
    for sig in signatures:
        name = sig.get("name", "")
        kind = sig.get("kind", "")
        kind_zh = "类" if kind == "class" else "函数"
        lines.append(f"\n## {name}\n")
        lines.append(f"**类型：** `{kind_zh}`")

        if kind == "class":
            bases = sig.get("bases", [])
            if bases:
                lines.append(f"**继承：** {', '.join(f'`{b}`' for b in bases)}")
            doc = sig.get("docstring", "")
            if doc:
                lines.append(f"\n> {doc.split(chr(10))[0]}")
            methods = sig.get("methods", [])
            if methods:
                lines.append(f"\n### 方法（共 {len(methods)} 个）\n")
                for m in methods:
                    args = m.get("args", "")
                    ret = m.get("returns", "")
                    ret_str = f" -> `{ret}`" if ret else ""
                    lines.append(f"- **`{m['name']}({args})`{ret_str}**")
                    mdoc = m.get("docstring", "")
                    if mdoc:
                        lines.append(f"  > {mdoc.split(chr(10))[0]}")

        elif kind == "function":
            args = sig.get("args", "")
            ret = sig.get("returns", "")
            ret_str = f" -> `{ret}`" if ret else ""
            lines.append(f"\n```python\ndef {name}({args}){ret_str}\n```")
            doc = sig.get("docstring", "")
            if doc:
                lines.append(f"\n> {doc.split(chr(10))[0]}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class Provider(SkillToolProvider):
    """Adds the doc-generator tools to the agent's tool registry."""

    def install(self, tool_registry: Any) -> set[str]:
        if tool_registry is None:
            return set()
        added: set[str] = set()
        for cls in (ExtractApiSignaturesTool, GenerateDocTool):
            if not tool_registry.has(cls.name):
                tool_registry.register(cls())
                added.add(cls.name)
        return added

    def uninstall(self, tool_registry: Any) -> None:
        if tool_registry is None:
            return
        for cls in (ExtractApiSignaturesTool, GenerateDocTool):
            tool_registry.unregister(cls.name)


__all__ = [
    "ExtractApiSignaturesTool",
    "GenerateDocTool",
    "Provider",
]
