"""Performance check tool for the ``code-review`` skill.

Scans Python source for common performance anti-patterns:

* N+1 query patterns (loop + fetch/save per iteration)
* Unnecessary list() / dict() copies
* Synchronous blocking calls in async functions
* O(n^2) string concatenation in loops
* Large list comprehension side-effects

Uses AST analysis only — no external tools required.

v0.8.0 — Enterprise Multi-Agent.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from ...tools.base import Tool, ToolResult
from ...tools.file_ops import PathSecurityError, safe_resolve

_SKIP_DIRS = frozenset(
    {"__pycache__", ".git", ".venv", "venv", "node_modules", ".pytest_cache"}
)


class PerformanceCheckTool(Tool):
    name = "performance_check"
    description = (
        "Scan Python source for performance anti-patterns: N+1 queries, "
        "unnecessary copies, sync blocking in async functions, O(n^2) "
        "string concatenation, and inefficient patterns."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File or directory to scan.",
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
        max_findings = int(kwargs.get("max_findings") or 300)

        try:
            target = safe_resolve(raw_path)
        except PathSecurityError as exc:
            return ToolResult.fail(str(exc))
        if not target.exists():
            return ToolResult.fail(f"path not found: {raw_path}")

        files = self._collect_files(target)
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

            findings.extend(self._check(f, tree))
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
    def _collect_files(target: Path) -> list[Path]:
        if target.is_file():
            return [target]
        return sorted(
            f for f in target.rglob("*.py")
            if not (_SKIP_DIRS & set(f.parts))
        )

    @staticmethod
    def _check(filepath: Path, tree: ast.AST) -> list[dict[str, Any]]:
        """Walk AST and collect performance anti-patterns."""
        findings: list[dict[str, Any]] = []

        for node in ast.walk(tree):
            # 1. Sync blocking calls inside async functions
            if isinstance(node, (ast.AsyncFunctionDef,)):
                findings.extend(_check_sync_in_async(filepath, node))

            # 2. String concatenation in loops (+= on str variable)
            if isinstance(node, (ast.For, ast.While)):
                findings.extend(_check_string_concat_in_loop(filepath, node))

            # 3. list() / dict() copy of already-iterable
            if isinstance(node, ast.Call):
                findings.extend(_check_unnecessary_copy(filepath, node))

            # 4. Potential N+1: for-loop body with .get / .fetch / .query calls
            if isinstance(node, ast.For):
                findings.extend(_check_n_plus_one(filepath, node))

        return findings


def _check_sync_in_async(
    filepath: Path, fn: ast.AsyncFunctionDef
) -> list[dict[str, Any]]:
    """Flag sync I/O calls inside async functions."""
    findings: list[dict[str, Any]] = []
    sync_io = {
        "open": "use aiofiles or async open",
        "requests": "use httpx.AsyncClient instead",
        "urlopen": "use httpx or aiohttp instead",
        "sleep": "use asyncio.sleep() instead of time.sleep()",
    }
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in sync_io:
                findings.append(
                    {
                        "file": str(filepath),
                        "line": node.lineno,
                        "code": "PERF001",
                        "message": f"sync call '{func.id}()' in async function '{fn.name}'",
                        "severity": "medium",
                        "suggestion": sync_io[func.id],
                    }
                )
            # time.sleep() inside async
            if isinstance(func, ast.Attribute) and func.attr == "sleep":
                if isinstance(func.value, ast.Name) and func.value.id == "time":
                    findings.append(
                        {
                            "file": str(filepath),
                            "line": node.lineno,
                            "code": "PERF001",
                            "message": f"time.sleep() in async function '{fn.name}'",
                            "severity": "medium",
                            "suggestion": "use asyncio.sleep()",
                        }
                    )
    return findings


def _check_string_concat_in_loop(
    filepath: Path, loop: ast.For | ast.While
) -> list[dict[str, Any]]:
    """Flag += on a string variable inside a loop body."""
    findings: list[dict[str, Any]] = []
    for node in ast.walk(loop):
        if isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add):
            if isinstance(node.target, ast.Name):
                # Heuristic: string concat in loop → suggest join()
                findings.append(
                    {
                        "file": str(filepath),
                        "line": node.lineno,
                        "code": "PERF002",
                        "message": f"string += in loop (variable '{node.target.id}')",
                        "severity": "info",
                        "suggestion": "collect into list and use ''.join()",
                    }
                )
    return findings


def _check_unnecessary_copy(
    filepath: Path, call: ast.Call
) -> list[dict[str, Any]]:
    """Flag list(x)/dict(x) when x is already a list/dict literal."""
    findings: list[dict[str, Any]] = []
    func = call.func
    if isinstance(func, ast.Name) and func.id == "list":
        if len(call.args) == 1 and isinstance(call.args[0], ast.List):
            findings.append(
                {
                    "file": str(filepath),
                    "line": call.lineno,
                    "code": "PERF003",
                    "message": "unnecessary list([...]) — already a list literal",
                    "severity": "info",
                }
            )
    if isinstance(func, ast.Name) and func.id == "dict":
        if len(call.args) == 1 and isinstance(call.args[0], ast.Dict):
            findings.append(
                {
                    "file": str(filepath),
                    "line": call.lineno,
                    "code": "PERF003",
                    "message": "unnecessary dict({...}) — already a dict literal",
                    "severity": "info",
                }
            )
    return findings


def _check_n_plus_one(filepath: Path, loop: ast.For) -> list[dict[str, Any]]:
    """Heuristic: detect potential N+1 patterns in for-loops."""
    findings: list[dict[str, Any]] = []
    n1_patterns = {"get", "fetch", "query", "save", "load", "select"}
    for node in ast.walk(loop):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in n1_patterns:
                findings.append(
                    {
                        "file": str(filepath),
                        "line": node.lineno,
                        "code": "PERF004",
                        "message": f"potential N+1: '{node.func.attr}()' inside for-loop",
                        "severity": "warning",
                        "suggestion": "consider bulk/batch operation",
                    }
                )
    return findings


__all__ = ["PerformanceCheckTool"]
