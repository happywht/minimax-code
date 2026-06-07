"""Type check tool for the ``code-review`` skill.

Analyses Python source for type annotation coverage:

* Tries mypy or pyright if available for full type checking
* Falls back to AST analysis for annotation coverage stats

v0.8.0 — Enterprise Multi-Agent.
"""

from __future__ import annotations

import ast
import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

from ...tools.base import Tool, ToolResult
from ...tools.file_ops import PathSecurityError, safe_resolve

_SKIP_DIRS = frozenset(
    {"__pycache__", ".git", ".venv", "venv", "node_modules", ".pytest_cache"}
)


class TypeCheckTool(Tool):
    name = "type_check"
    description = (
        "Check Python type annotation coverage. Uses mypy or pyright "
        "if available for full checking; falls back to AST analysis "
        "for annotation coverage statistics."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File or directory to check.",
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

        # Try mypy first
        if shutil.which("mypy"):
            mypy_result = await self._run_mypy(target, max_findings)
            if mypy_result is not None:
                return mypy_result

        # Try pyright
        if shutil.which("pyright"):
            pyright_result = await self._run_pyright(target, max_findings)
            if pyright_result is not None:
                return pyright_result

        # Fallback to AST annotation coverage
        return self._ast_coverage(target, max_findings)

    async def _run_mypy(self, target: Path, max_findings: int) -> ToolResult | None:
        argv = ("mypy", "--output", "json", "--no-error-summary")
        env = os.environ.copy()
        env["FORCE_COLOR"] = "0"

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                str(target),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError):
            return None

        try:
            stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=60.0)
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return None

        import json as _json

        findings: list[dict[str, Any]] = []
        for line in stdout_b.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            findings.append(
                {
                    "file": str(row.get("file", "")),
                    "line": int(row.get("line", 0)),
                    "col": int(row.get("column", 0)),
                    "code": str(row.get("code", "")),
                    "message": str(row.get("message", "")),
                    "severity": "error" if row.get("severity") == "error" else "warning",
                }
            )
            if len(findings) >= max_findings:
                break

        return ToolResult.ok(
            output={
                "checker": "mypy",
                "path": str(target),
                "findings": findings,
                "truncated": len(findings) >= max_findings,
            },
            checker="mypy",
            count=len(findings),
        )

    async def _run_pyright(self, target: Path, max_findings: int) -> ToolResult | None:
        argv = ("pyright", "--outputjson")
        env = os.environ.copy()
        env["FORCE_COLOR"] = "0"

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                str(target),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError):
            return None

        try:
            stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=60.0)
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return None

        import json as _json

        try:
            data = _json.loads(stdout_b.decode("utf-8", errors="replace"))
        except _json.JSONDecodeError:
            return None

        findings: list[dict[str, Any]] = []
        for diag in data.get("generalDiagnostics", []):
            findings.append(
                {
                    "file": str(diag.get("file", "")),
                    "line": int(diag.get("range", {}).get("start", {}).get("line", 0)),
                    "col": int(diag.get("range", {}).get("start", {}).get("character", 0)),
                    "code": str(diag.get("rule", "")),
                    "message": str(diag.get("message", "")),
                    "severity": str(diag.get("severity", "warning")).lower(),
                }
            )
            if len(findings) >= max_findings:
                break

        return ToolResult.ok(
            output={
                "checker": "pyright",
                "path": str(target),
                "findings": findings,
                "truncated": len(findings) >= max_findings,
            },
            checker="pyright",
            count=len(findings),
        )

    @staticmethod
    def _ast_coverage(target: Path, max_findings: int) -> ToolResult:
        """Check annotation coverage via AST."""
        files = _collect_files(target)
        findings: list[dict[str, Any]] = []
        total_params = 0
        annotated_params = 0
        total_returns = 0
        annotated_returns = 0

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
                    # Skip private functions
                    if node.name.startswith("_"):
                        continue

                    # Check return annotation
                    total_returns += 1
                    if node.returns is not None:
                        annotated_returns += 1
                    else:
                        findings.append(
                            {
                                "file": str(f),
                                "line": node.lineno,
                                "name": node.name,
                                "code": "TYPE001",
                                "message": f"missing return type annotation for '{node.name}'",
                                "severity": "info",
                            }
                        )

                    # Check parameter annotations (skip self/cls)
                    args = node.args
                    all_args = list(args.args) + list(args.kwonlyargs)
                    skip_first = (
                        len(args.args) > 0
                        and args.args[0].arg in ("self", "cls")
                    )
                    if skip_first:
                        all_args = all_args[1:]

                    for arg in all_args:
                        total_params += 1
                        if arg.annotation is not None:
                            annotated_params += 1
                        else:
                            findings.append(
                                {
                                    "file": str(f),
                                    "line": arg.lineno,
                                    "name": arg.arg,
                                    "code": "TYPE002",
                                    "message": f"missing type annotation for parameter '{arg.arg}'",
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

        param_pct = (annotated_params / total_params * 100) if total_params else 100.0
        return_pct = (annotated_returns / total_returns * 100) if total_returns else 100.0

        return ToolResult.ok(
            output={
                "checker": "ast-coverage",
                "path": str(target),
                "findings": findings,
                "truncated": truncated,
                "stats": {
                    "total_params": total_params,
                    "annotated_params": annotated_params,
                    "param_coverage_pct": round(param_pct, 1),
                    "total_returns": total_returns,
                    "annotated_returns": annotated_returns,
                    "return_coverage_pct": round(return_pct, 1),
                },
            },
            checker="ast-coverage",
            count=len(findings),
        )


def _collect_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(
        f for f in target.rglob("*.py")
        if not (_SKIP_DIRS & set(f.parts))
    )


__all__ = ["TypeCheckTool"]
