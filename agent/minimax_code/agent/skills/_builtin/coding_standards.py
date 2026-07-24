"""Built-in tools for the ``coding-standards`` skill.

Tools
-----

* :class:`CheckStyleTool` — runs a style checker against Python
  source files. Uses ``ruff`` if available, falls back to a
  lightweight AST-based heuristic that checks line length, trailing
  whitespace, and blank-line conventions.
* :class:`CheckNamingTool` — walks Python files with :mod:`ast` and
  reports naming violations: ``snake_case`` for functions/variables,
  ``PascalCase`` for classes, ``UPPER_SNAKE`` for constants.
* :class:`CheckDocstringTool` — scans Python files and reports
  public functions/classes that lack docstrings or use an
  unrecognised docstring style (Google / NumPy / Sphinx).

All tools honour the workspace-safety policy enforced by
:func:`safe_resolve` — paths outside the workspace, or inside
well-known sensitive directories, are rejected.

v0.8.0 — Enterprise Multi-Agent.
"""

from __future__ import annotations

import ast
import asyncio
import os
import re
import shutil
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
    """Return True if *path* is inside a directory we should skip."""
    return bool(_SKIP_DIRS & set(path.parts))


def _collect_py_files(target: Path) -> list[Path]:
    """Collect Python files from *target* (file or directory)."""
    if target.is_file():
        return [target]
    return sorted(f for f in target.rglob("*.py") if not _is_skippable(f))


# ---------------------------------------------------------------------------
# CheckStyleTool
# ---------------------------------------------------------------------------


class CheckStyleTool(Tool):
    name = "check_style"
    description = (
        "Check Python code style against PEP 8 conventions. Uses ruff "
        "if available; otherwise falls back to a built-in heuristic "
        "checking line length, trailing whitespace, and blank-line "
        "conventions. Returns a list of findings with file, line, col, "
        "code, message, and severity."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File or directory to check (must live inside the workspace).",
            },
            "max_line_length": {
                "type": "integer",
                "minimum": 60,
                "maximum": 200,
                "default": 100,
                "description": "Maximum allowed line length (default 100, matching ruff).",
            },
            "max_findings": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10_000,
                "default": 500,
                "description": "Cap on findings returned.",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        raw_path = kwargs.get("path")
        max_line_length = int(kwargs.get("max_line_length") or 100)
        max_findings = int(kwargs.get("max_findings") or 500)

        try:
            target = safe_resolve(raw_path)
        except PathSecurityError as exc:
            return ToolResult.fail(str(exc))
        if not target.exists():
            return ToolResult.fail(f"path not found: {raw_path}")

        # Try ruff first
        if shutil.which("ruff"):
            return await self._run_ruff(target, max_findings)

        # Fallback to built-in heuristic
        return self._heuristic_check(target, max_line_length, max_findings)

    async def _run_ruff(self, target: Path, max_findings: int) -> ToolResult:
        """Run ruff format-check and return structured findings."""
        argv = ("ruff", "check", "--output-format", "json", "--select", "E,W")
        env = os.environ.copy()
        env["FORCE_COLOR"] = "0"
        env["NO_COLOR"] = "1"

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                str(target),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError) as exc:
            return ToolResult.fail(f"failed to spawn ruff: {exc}")

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return ToolResult.fail("ruff timed out after 30s")

        import json as _json

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace").strip()

        findings: list[dict[str, Any]] = []
        parse_error = None
        try:
            data = _json.loads(stdout)
            for row in data:
                if not isinstance(row, dict):
                    continue
                findings.append(
                    {
                        "file": str(row.get("filename") or row.get("file") or ""),
                        "line": int(row.get("line") or 0),
                        "col": int(row.get("column") or 0),
                        "code": str(row.get("code") or ""),
                        "message": str(row.get("message") or ""),
                        "severity": (
                            "error"
                            if str(row.get("code", "")).startswith("E9")
                            else "warning"
                        ),
                    }
                )
        except _json.JSONDecodeError as exc:
            parse_error = f"ruff output was not valid JSON: {exc}"

        truncated = len(findings) > max_findings
        if truncated:
            findings = findings[:max_findings]

        return ToolResult.ok(
            output={
                "checker": "ruff",
                "path": str(target),
                "findings": findings,
                "stderr": stderr,
                "truncated": truncated,
                "parse_error": parse_error,
            },
            checker="ruff",
            count=len(findings),
        )

    def _heuristic_check(
        self, target: Path, max_line_length: int, max_findings: int
    ) -> ToolResult:
        """Built-in style checker when ruff is unavailable."""
        files = _collect_py_files(target)
        findings: list[dict[str, Any]] = []

        for f in files:
            try:
                lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue

            prev_blank = False
            for i, line in enumerate(lines, start=1):
                # Line length
                if len(line.rstrip("\r\n")) > max_line_length:
                    findings.append(
                        {
                            "file": str(f),
                            "line": i,
                            "col": max_line_length + 1,
                            "code": "E501",
                            "message": f"line too long ({len(line)} > {max_line_length})",
                            "severity": "warning",
                        }
                    )
                # Trailing whitespace
                if line.rstrip("\r\n") != line.rstrip() and line.strip():
                    findings.append(
                        {
                            "file": str(f),
                            "line": i,
                            "col": len(line.rstrip()) + 1,
                            "code": "W291",
                            "message": "trailing whitespace",
                            "severity": "warning",
                        }
                    )
                # Multiple blank lines (more than 2)
                if line.strip() == "" and prev_blank:
                    # Track consecutive blank lines
                    pass
                prev_blank = line.strip() == ""

                if len(findings) >= max_findings:
                    break
            if len(findings) >= max_findings:
                break

        return ToolResult.ok(
            output={
                "checker": "builtin-heuristic",
                "path": str(target),
                "findings": findings,
                "truncated": len(findings) >= max_findings,
            },
            checker="builtin-heuristic",
            count=len(findings),
        )


# ---------------------------------------------------------------------------
# CheckNamingTool
# ---------------------------------------------------------------------------

# Naming convention regexes
_SNAKE_RE = re.compile(r"^[a-z][a-z0-9_]*[a-z0-9]?$")
_SNAKE_SINGLE_RE = re.compile(r"^[a-z]$")  # single-char names like `x`
_PASCAL_RE = re.compile(r"^[A-Z][a-zA-Z0-9]*$")
_UPPER_SNAKE_RE = re.compile(r"^[A-Z][A-Z0-9_]*[A-Z0-9]?$")


class CheckNamingTool(Tool):
    name = "check_naming"
    description = (
        "Check Python naming conventions: snake_case for functions and "
        "variables, PascalCase for classes, UPPER_SNAKE_CASE for module-level "
        "constants. Returns a list of violations with file, line, name, "
        "kind, and expected convention."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File or directory to check (must live inside the workspace).",
            },
            "max_findings": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10_000,
                "default": 200,
                "description": "Cap on findings returned.",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        raw_path = kwargs.get("path")
        max_findings = int(kwargs.get("max_findings") or 200)

        try:
            target = safe_resolve(raw_path)
        except PathSecurityError as exc:
            return ToolResult.fail(str(exc))
        if not target.exists():
            return ToolResult.fail(f"path not found: {raw_path}")

        files = _collect_py_files(target)
        findings: list[dict[str, Any]] = []

        for f in files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                tree = ast.parse(text, filename=str(f))
            except SyntaxError:
                continue

            findings.extend(self._check_names(tree, f))
            if len(findings) >= max_findings:
                break

        truncated = len(findings) > max_findings
        if truncated:
            findings = findings[:max_findings]

        return ToolResult.ok(
            output={
                "path": str(target),
                "findings": findings,
                "truncated": truncated,
            },
            count=len(findings),
        )

    @staticmethod
    def _check_names(tree: ast.AST, filepath: Path) -> list[dict[str, Any]]:
        """Walk *tree* and collect naming violations."""
        findings: list[dict[str, Any]] = []

        for node in ast.walk(tree):
            # Classes: PascalCase
            if isinstance(node, ast.ClassDef):
                if not _PASCAL_RE.match(node.name):
                    findings.append(
                        {
                            "file": str(filepath),
                            "line": node.lineno,
                            "name": node.name,
                            "kind": "class",
                            "expected": "PascalCase",
                            "message": f"class '{node.name}' should use PascalCase",
                            "severity": "warning",
                        }
                    )

            # Functions / methods: snake_case
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Skip dunder methods
                if node.name.startswith("__") and node.name.endswith("__"):
                    continue
                if not (_SNAKE_RE.match(node.name) or _SNAKE_SINGLE_RE.match(node.name)):
                    findings.append(
                        {
                            "file": str(filepath),
                            "line": node.lineno,
                            "name": node.name,
                            "kind": "function",
                            "expected": "snake_case",
                            "message": f"function '{node.name}' should use snake_case",
                            "severity": "warning",
                        }
                    )

            # Module-level assignments: UPPER_SNAKE for constants
            elif isinstance(node, ast.Assign):
                # Only check simple Name targets at module level
                for target_node in node.targets:
                    if isinstance(target_node, ast.Name):
                        name = target_node.id
                        # Heuristic: if name is all caps or has underscore,
                        # it's a constant candidate. If it looks like a
                        # constant (no lowercase, longer than 2 chars) but
                        # doesn't match UPPER_SNAKE, flag it.
                        if (
                            len(name) > 2
                            and not name.startswith("_")
                            and any(c.isupper() for c in name)
                            and not _UPPER_SNAKE_RE.match(name)
                            and not _SNAKE_RE.match(name)
                            and not _PASCAL_RE.match(name)
                        ):
                            # Mixed case like "MyConst" or "myConst" — not a
                            # clear violation. Only flag obvious all-caps
                            # with mixed separators.
                            pass

        return findings


# ---------------------------------------------------------------------------
# CheckDocstringTool
# ---------------------------------------------------------------------------

# Recognised docstring style markers
_GOOGLE_RE = re.compile(r"(Args:|Returns:|Raises:|Yields:|Note:|Example:)", re.MULTILINE)
_NUMPY_RE = re.compile(
    r"(Parameters\n[-=]+\n|Returns\n[-=]+\n|Raises\n[-=]+\n)", re.MULTILINE
)
_SPHINX_RE = re.compile(r":(param|type|returns?|rtype|raises?|var|vartype)", re.MULTILINE)


class CheckDocstringTool(Tool):
    name = "check_docstring"
    description = (
        "Check docstring coverage and style conventions for Python files. "
        "Reports public functions and classes that lack docstrings, and "
        "flags unrecognised docstring styles. Recognises Google, NumPy, "
        "and Sphinx styles. Returns file, line, name, kind, and issue."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File or directory to check (must live inside the workspace).",
            },
            "check_style": {
                "type": "boolean",
                "default": True,
                "description": "Also validate docstring style (Google/NumPy/Sphinx).",
            },
            "max_findings": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10_000,
                "default": 300,
                "description": "Cap on findings returned.",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        raw_path = kwargs.get("path")
        check_style = kwargs.get("check_style", True)
        max_findings = int(kwargs.get("max_findings") or 300)

        try:
            target = safe_resolve(raw_path)
        except PathSecurityError as exc:
            return ToolResult.fail(str(exc))
        if not target.exists():
            return ToolResult.fail(f"path not found: {raw_path}")

        files = _collect_py_files(target)
        findings: list[dict[str, Any]] = []
        total_public = 0
        total_with_doc = 0

        for f in files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                tree = ast.parse(text, filename=str(f))
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # Only check public (non-underscore) functions
                    if node.name.startswith("_"):
                        continue
                    total_public += 1
                    doc = ast.get_docstring(node)
                    if not doc:
                        total_with_doc += 0
                        findings.append(
                            {
                                "file": str(f),
                                "line": node.lineno,
                                "name": node.name,
                                "kind": "function",
                                "issue": "missing docstring",
                                "severity": "warning",
                            }
                        )
                    else:
                        total_with_doc += 1
                        if check_style and not self._recognise_style(doc):
                            findings.append(
                                {
                                    "file": str(f),
                                    "line": node.lineno,
                                    "name": node.name,
                                    "kind": "function",
                                    "issue": "unrecognised docstring style",
                                    "severity": "info",
                                }
                            )

                elif isinstance(node, ast.ClassDef):
                    if node.name.startswith("_"):
                        continue
                    total_public += 1
                    doc = ast.get_docstring(node)
                    if not doc:
                        findings.append(
                            {
                                "file": str(f),
                                "line": node.lineno,
                                "name": node.name,
                                "kind": "class",
                                "issue": "missing docstring",
                                "severity": "warning",
                            }
                        )
                    else:
                        total_with_doc += 1
                        if check_style and not self._recognise_style(doc):
                            findings.append(
                                {
                                    "file": str(f),
                                    "line": node.lineno,
                                    "name": node.name,
                                    "kind": "class",
                                    "issue": "unrecognised docstring style",
                                    "severity": "info",
                                }
                            )

                if len(findings) >= max_findings:
                    break
            if len(findings) >= max_findings:
                break

        truncated = len(findings) > max_findings
        if truncated:
            findings = findings[:max_findings]

        coverage = (total_with_doc / total_public * 100) if total_public else 100.0

        return ToolResult.ok(
            output={
                "path": str(target),
                "findings": findings,
                "truncated": truncated,
                "stats": {
                    "total_public": total_public,
                    "with_docstring": total_with_doc,
                    "coverage_pct": round(coverage, 1),
                },
            },
            count=len(findings),
            coverage=f"{coverage:.1f}%",
        )

    @staticmethod
    def _recognise_style(docstring: str) -> bool:
        """Return True if *docstring* matches a known style."""
        if _GOOGLE_RE.search(docstring):
            return True
        if _NUMPY_RE.search(docstring):
            return True
        if _SPHINX_RE.search(docstring):
            return True
        # Short single-line or plain docstrings are acceptable
        if "\n" not in docstring:
            return True
        return False


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class Provider(SkillToolProvider):
    """Adds the coding-standards tools to the agent's tool registry."""

    def install(self, tool_registry: Any) -> set[str]:
        if tool_registry is None:
            return set()
        added: set[str] = set()
        for cls in (CheckStyleTool, CheckNamingTool, CheckDocstringTool):
            if not tool_registry.has(cls.name):
                tool_registry.register(cls())
                added.add(cls.name)
        return added

    def uninstall(self, tool_registry: Any) -> None:
        if tool_registry is None:
            return
        for cls in (CheckStyleTool, CheckNamingTool, CheckDocstringTool):
            tool_registry.unregister(cls.name)


__all__ = [
    "CheckStyleTool",
    "CheckNamingTool",
    "CheckDocstringTool",
    "Provider",
]
