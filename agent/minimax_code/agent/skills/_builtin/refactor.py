"""Built-in tools for the ``refactor-assistant`` skill.

Tools
-----

* :class:`ASTRenameTool` — renames a symbol across Python files
  using AST-aware matching (falls back to regex for non-Python).
  Defaults to ``dry_run=True`` for safety.
* :class:`ExtractFunctionTool` — extracts a range of lines into a
  new function, inferring parameters and return values from context.
  Defaults to ``dry_run=True``.

Both tools honour the workspace-safety policy enforced by
:func:`safe_resolve`.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Any

from ...tools.base import Tool, ToolResult
from ...tools.file_ops import PathSecurityError, safe_resolve
from ..runtime import SkillToolProvider

# Maximum files to scan during rename (safety cap).
_MAX_FILES = 100

# Binary file sniff size.
_BINARY_SNIFF = 8192

# Directories to skip during rename scanning.
_SKIP_DIRS = {
    ".git", ".venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".tox", "dist", "build", ".eggs", ".minimax",
    ".hg", ".svn", "vendor", ".next", ".nuxt",
}


class ASTRenameTool(Tool):
    name = "ast_rename"
    description = (
        "Rename a symbol (variable, function, class, method) across "
        "files. For Python files uses AST-aware matching to distinguish "
        "symbols by context. For other languages falls back to "
        "whole-word regex. Defaults to dry_run=True so the user can "
        "preview changes before applying."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "The current symbol name to rename.",
            },
            "new_name": {
                "type": "string",
                "description": "The replacement name.",
            },
            "paths": {
                "type": "string",
                "description": (
                    "File or directory to scope the rename. "
                    "Defaults to the workspace root."
                ),
            },
            "dry_run": {
                "type": "boolean",
                "default": True,
                "description": "If true, return a preview without modifying files.",
            },
            "max_files": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1000,
                "default": _MAX_FILES,
                "description": "Safety cap on number of files to scan.",
            },
        },
        "required": ["symbol", "new_name"],
        "additionalProperties": False,
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        symbol = str(kwargs["symbol"])
        new_name = str(kwargs["new_name"])
        raw_path = kwargs.get("paths")
        dry_run = kwargs.get("dry_run", True)
        max_files = int(kwargs.get("max_files") or _MAX_FILES)

        if not symbol.isidentifier():
            return ToolResult.fail(f"invalid symbol name: {symbol!r}")
        if not new_name.isidentifier():
            return ToolResult.fail(f"invalid new_name: {new_name!r}")

        # Resolve scope path.
        target: Path
        if raw_path:
            try:
                target = safe_resolve(raw_path)
            except PathSecurityError as exc:
                return ToolResult.fail(str(exc))
        else:
            target = Path(os.environ.get("MINIMAX_CODE_WORKSPACE", os.getcwd()))

        if not target.exists():
            return ToolResult.fail(f"path not found: {raw_path or target}")

        # Collect candidate files.
        files = _collect_files(target, max_files)

        # Apply rename.
        changes: list[dict[str, Any]] = []
        for fpath in files:
            try:
                change = _rename_in_file(fpath, symbol, new_name, dry_run)
                if change:
                    changes.append(change)
            except Exception as exc:
                changes.append({
                    "file": str(fpath),
                    "error": str(exc),
                })

        applied = 0 if dry_run else sum(
            c.get("replacements", 0) for c in changes if "replacements" in c
        )

        return ToolResult.ok(
            output={
                "symbol": symbol,
                "new_name": new_name,
                "dry_run": dry_run,
                "files_scanned": len(files),
                "changes": changes,
                "total_replacements": applied,
            },
            changes=len(changes),
            dry_run=dry_run,
        )


class ExtractFunctionTool(Tool):
    name = "extract_function"
    description = (
        "Extract a range of lines from a file into a new function, "
        "replacing the original lines with a call. Infers parameters "
        "by analysing variable usage. Defaults to dry_run=True."
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The file to refactor.",
            },
            "start_line": {
                "type": "integer",
                "minimum": 1,
                "description": "First line to extract (1-based).",
            },
            "end_line": {
                "type": "integer",
                "minimum": 1,
                "description": "Last line to extract (1-based, inclusive).",
            },
            "function_name": {
                "type": "string",
                "description": "Name for the new function.",
            },
            "dry_run": {
                "type": "boolean",
                "default": True,
                "description": "If true, return a preview without modifying files.",
            },
            "insert_before": {
                "type": "boolean",
                "default": False,
                "description": "Insert the new function before the caller instead of after.",
            },
        },
        "required": ["file_path", "start_line", "end_line", "function_name"],
        "additionalProperties": False,
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        raw_path = str(kwargs["file_path"])
        start_line = int(kwargs["start_line"])
        end_line = int(kwargs["end_line"])
        func_name = str(kwargs["function_name"])
        dry_run = kwargs.get("dry_run", True)
        insert_before = kwargs.get("insert_before", False)

        try:
            target = safe_resolve(raw_path)
        except PathSecurityError as exc:
            return ToolResult.fail(str(exc))

        if not target.exists():
            return ToolResult.fail(f"file not found: {raw_path}")
        if not target.is_file():
            return ToolResult.fail(f"not a file: {raw_path}")

        try:
            text = target.read_text(encoding="utf-8")
        except OSError as exc:
            return ToolResult.fail(f"cannot read file: {exc}")

        lines = text.splitlines()
        total = len(lines)

        if start_line < 1 or end_line > total or start_line > end_line:
            return ToolResult.fail(
                f"invalid line range: {start_line}-{end_line} "
                f"(file has {total} lines)"
            )

        # Extract lines (convert to 0-based).
        extracted = lines[start_line - 1 : end_line]
        before = lines[: start_line - 1]
        after = lines[end_line:]

        # Infer the indentation of the extracted block.
        indent = _detect_indent(extracted)
        base_indent = indent

        # Infer parameters: variables from the outer scope that are
        # read inside the extracted block.
        outer_scope = before  # simplified: all lines before
        params = _infer_params(extracted, outer_scope)

        # Infer return values: variables defined in the extracted
        # block that are used after it.
        returns = _infer_returns(extracted, after)

        # Build the new function.
        param_str = ", ".join(params)
        func_lines = [f"{base_indent}def {func_name}({param_str}):"]
        for line in extracted:
            if line.strip():
                func_lines.append(f"{base_indent}    {line.lstrip()}")
            else:
                func_lines.append("")

        if returns:
            ret_str = ", ".join(returns)
            func_lines.append(f"{base_indent}    return {ret_str}")

        # Build the call site.
        call_args = ", ".join(params)
        call_line = f"{indent}{func_name}({call_args})"
        if returns:
            ret_str = ", ".join(returns)
            call_line = f"{indent}{ret_str} = {func_name}({call_args})"

        # Assemble the result.
        if insert_before:
            result_lines = func_lines + [""] + before + [call_line] + after
        else:
            result_lines = before + [call_line] + [""] + func_lines + after

        new_content = "\n".join(result_lines)

        if not dry_run:
            try:
                target.write_text(new_content, encoding="utf-8")
            except OSError as exc:
                return ToolResult.fail(f"cannot write file: {exc}")

        return ToolResult.ok(
            output={
                "file": str(target),
                "dry_run": dry_run,
                "function_name": func_name,
                "parameters": params,
                "returns": returns,
                "extracted_lines": f"{start_line}-{end_line}",
                "new_content": new_content if dry_run else None,
            },
            dry_run=dry_run,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_files(root: Path, max_files: int) -> list[Path]:
    """Collect source files under *root*, respecting skip dirs."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if len(files) >= max_files:
                return files
            fpath = Path(dirpath) / name
            if _is_source_file(fpath):
                try:
                    data = fpath.read_bytes()
                    if b"\x00" not in data[:_BINARY_SNIFF]:
                        files.append(fpath)
                except OSError:
                    continue
    return files


def _is_source_file(path: Path) -> bool:
    """Check if *path* has a source file extension."""
    return path.suffix.lower() in {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
        ".java", ".go", ".rs", ".c", ".cpp", ".h", ".hpp",
    }


def _rename_in_file(
    fpath: Path, symbol: str, new_name: str, dry_run: bool
) -> dict[str, Any] | None:
    """Apply rename in a single file. Returns change info or None."""
    try:
        text = fpath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    if fpath.suffix.lower() == ".py":
        new_text, count = _rename_python(text, symbol, new_name)
    else:
        new_text, count = _rename_regex(text, symbol, new_name)

    if count == 0:
        return None

    if not dry_run:
        fpath.write_text(new_text, encoding="utf-8")

    return {
        "file": str(fpath),
        "replacements": count,
        "preview": new_text if dry_run else None,
    }


def _rename_python(text: str, symbol: str, new_name: str) -> tuple[str, int]:
    """AST-aware rename for Python files.

    Uses ``ast`` to find all Name nodes matching *symbol*, then
    replaces them in the source text. Falls back to regex if the
    source cannot be parsed.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _rename_regex(text, symbol, new_name)

    # Collect all (line, col) positions of the symbol.
    positions: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == symbol:
            positions.append((node.lineno, node.col_offset))
        elif isinstance(node, ast.Attribute) and node.attr == symbol:
            # attribute rename: only if it's the rightmost part
            positions.append((node.lineno, node.col_offset))
        elif isinstance(node, ast.FunctionDef) and node.name == symbol:
            positions.append((node.lineno, node.col_offset))
        elif isinstance(node, ast.AsyncFunctionDef) and node.name == symbol:
            positions.append((node.lineno, node.col_offset))
        elif isinstance(node, ast.ClassDef) and node.name == symbol:
            positions.append((node.lineno, node.col_offset))
        elif isinstance(node, ast.arg) and node.arg == symbol:
            positions.append((node.lineno, node.col_offset))

    if not positions:
        return text, 0

    # Apply replacements (process in reverse to maintain positions).
    lines = text.splitlines(True)
    count = 0
    for lineno, col in sorted(positions, reverse=True):
        line_idx = lineno - 1
        if line_idx >= len(lines):
            continue
        line = lines[line_idx]
        # Replace the symbol at the exact position.
        end = col + len(symbol)
        if end <= len(line) and line[col:end] == symbol:
            lines[line_idx] = line[:col] + new_name + line[end:]
            count += 1

    return "".join(lines), count


def _rename_regex(text: str, symbol: str, new_name: str) -> tuple[str, int]:
    """Whole-word regex rename for non-Python files."""
    pattern = re.compile(r"\b" + re.escape(symbol) + r"\b")
    count = 0

    def _replace(m: re.Match) -> str:
        nonlocal count
        count += 1
        return new_name

    new_text = pattern.sub(_replace, text)
    return new_text, count


def _detect_indent(lines: list[str]) -> str:
    """Detect the leading whitespace of the first non-empty line."""
    for line in lines:
        stripped = line.lstrip()
        if stripped:
            return line[: len(line) - len(stripped)]
    return "    "


def _infer_params(extracted: list[str], outer: list[str]) -> list[str]:
    """Heuristic: find variables from the outer scope used in extracted lines.

    Scans the extracted block for simple name references, then checks
    which of those were assigned in the outer scope (before the extraction
    point). Returns a deduplicated, sorted list.
    """
    # Collect all name-like tokens in the extracted block.
    names_in_extracted = set()
    for line in extracted:
        for m in re.finditer(r"\b([a-zA-Z_]\w*)\b", line):
            names_in_extracted.add(m.group(1))

    # Collect names assigned in the outer scope before extraction.
    outer_names = set()
    for line in outer:
        # Simple assignment detection: name = ...
        m = re.match(r"\s*([a-zA-Z_]\w*)\s*=", line)
        if m:
            outer_names.add(m.group(1))
        # for-loop variable: for name in ...
        m = re.match(r"\s*for\s+([a-zA-Z_]\w*)\s+in", line)
        if m:
            outer_names.add(m.group(1))
        # with-as: with ... as name
        m = re.match(r"\s*with\s+.*\s+as\s+([a-zA-Z_]\w*)", line)
        if m:
            outer_names.add(m.group(1))

    # Parameters = names used in extracted but defined in outer.
    # Exclude Python keywords and builtins.
    import keyword

    params = names_in_extracted & outer_names
    params -= set(keyword.kwlist)
    params -= {
        "True", "False", "None",
        "print", "len", "range", "str", "int", "float", "list", "dict",
        "set", "tuple", "bool", "type", "isinstance", "issubclass",
        "enumerate", "zip", "map", "filter", "sorted", "reversed",
        "open", "super", "property", "classmethod", "staticmethod",
        "self", "cls",
    }
    return sorted(params)


def _infer_returns(extracted: list[str], after: list[str]) -> list[str]:
    """Heuristic: find variables defined in extracted and used after.

    Returns a list of variable names that should be returned from
    the extracted function.
    """
    # Names assigned in extracted block.
    assigned_in_extracted = set()
    for line in extracted:
        m = re.match(r"\s*([a-zA-Z_]\w*)\s*=", line)
        if m:
            assigned_in_extracted.add(m.group(1))

    # Names used after extraction.
    names_after = set()
    for line in after:
        for m in re.finditer(r"\b([a-zA-Z_]\w*)\b", line):
            names_after.add(m.group(1))

    returns = assigned_in_extracted & names_after
    returns.discard("self")
    return sorted(returns)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class Provider(SkillToolProvider):
    """Adds the refactor-assistant tools to the agent's tool registry."""

    def install(self, tool_registry: Any) -> set[str]:
        if tool_registry is None:
            return set()
        added: set[str] = set()
        for cls in (ASTRenameTool, ExtractFunctionTool):
            if not tool_registry.has(cls.name):
                tool_registry.register(cls())
                added.add(cls.name)
        return added

    def uninstall(self, tool_registry: Any) -> None:
        if tool_registry is None:
            return
        for cls in (ASTRenameTool, ExtractFunctionTool):
            tool_registry.unregister(cls.name)


__all__ = [
    "ASTRenameTool",
    "ExtractFunctionTool",
    "Provider",
]
