"""Security scan tool for the ``code-review`` skill.

Scans Python source for common security issues:

* Hard-coded secrets (API keys, passwords, tokens)
* SQL injection patterns (raw string formatting in SQL)
* Dangerous function calls (eval, exec, pickle, subprocess with shell=True)
* Insecure deserialization
* Weak hash algorithms (md5, sha1 used for security)

Uses AST analysis + regex patterns. Does NOT require external tools
like ``bandit`` (but uses bandit if available for enhanced results).

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

_SKIP_DIRS = frozenset(
    {"__pycache__", ".git", ".venv", "venv", "node_modules", ".pytest_cache"}
)


class SecurityScanTool(Tool):
    name = "security_scan"
    description = (
        "Scan Python source files for common security vulnerabilities: "
        "hard-coded secrets, SQL injection, dangerous eval/exec/pickle calls, "
        "insecure deserialization, and weak hash usage. Uses AST analysis; "
        "optionally uses bandit if installed."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File or directory to scan.",
            },
            "severity": {
                "type": "string",
                "enum": ["all", "high", "medium"],
                "default": "all",
                "description": "Minimum severity to report.",
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
        min_severity = kwargs.get("severity", "all")
        max_findings = int(kwargs.get("max_findings") or 500)

        try:
            target = safe_resolve(raw_path)
        except PathSecurityError as exc:
            return ToolResult.fail(str(exc))
        if not target.exists():
            return ToolResult.fail(f"path not found: {raw_path}")

        # Try bandit first for richer results
        if shutil.which("bandit"):
            bandit_result = await self._run_bandit(target, min_severity, max_findings)
            if bandit_result is not None:
                return bandit_result

        # Fallback to built-in AST analysis
        return self._ast_scan(target, min_severity, max_findings)

    async def _run_bandit(
        self, target: Path, min_severity: str, max_findings: int
    ) -> ToolResult | None:
        """Run bandit and return structured findings."""
        argv = ("bandit", "-r", "-f", "json")
        if min_severity == "high":
            argv = ("bandit", "-r", "-f", "json", "-l")
        elif min_severity == "medium":
            argv = ("bandit", "-r", "-f", "json", "-l", "-m")

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
        for row in data.get("results", []):
            findings.append(
                {
                    "file": str(row.get("filename", "")),
                    "line": int(row.get("line_number", 0)),
                    "code": str(row.get("test_id", "")),
                    "message": str(row.get("issue_text", "")),
                    "severity": str(row.get("issue_severity", "LOW")).lower(),
                    "confidence": str(row.get("issue_confidence", "")).lower(),
                }
            )
            if len(findings) >= max_findings:
                break

        return ToolResult.ok(
            output={
                "scanner": "bandit",
                "path": str(target),
                "findings": findings,
                "truncated": len(findings) >= max_findings,
            },
            scanner="bandit",
            count=len(findings),
        )

    def _ast_scan(
        self, target: Path, min_severity: str, max_findings: int
    ) -> ToolResult:
        """Built-in AST-based security scan."""
        files = self._collect_files(target)
        findings: list[dict[str, Any]] = []

        for f in files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            # Regex-based checks
            findings.extend(self._check_secrets(f, text))
            findings.extend(self._check_sql_injection(f, text))

            # AST-based checks
            try:
                tree = ast.parse(text, filename=str(f))
            except SyntaxError:
                continue
            findings.extend(self._check_ast(f, tree))

            if len(findings) >= max_findings:
                break

        # Filter by severity
        if min_severity == "high":
            findings = [f for f in findings if f.get("severity") == "high"]
        elif min_severity == "medium":
            findings = [f for f in findings if f.get("severity") in ("high", "medium")]

        truncated = len(findings) > max_findings
        if truncated:
            findings = findings[:max_findings]

        return ToolResult.ok(
            output={
                "scanner": "builtin-ast",
                "path": str(target),
                "findings": findings,
                "truncated": truncated,
            },
            scanner="builtin-ast",
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
    def _check_secrets(filepath: Path, text: str) -> list[dict[str, Any]]:
        """Detect potential hard-coded secrets."""
        findings: list[dict[str, Any]] = []
        patterns = [
            (r'(?:api[_-]?key|apikey)\s*=\s*["\'][^"\']{8,}["\']', "hard-coded API key"),
            (r'(?:password|passwd|pwd)\s*=\s*["\'][^"\']{4,}["\']', "hard-coded password"),
            (r'(?:secret|token|auth)\s*=\s*["\'][^"\']{8,}["\']', "hard-coded secret/token"),
        ]
        for pattern, msg in patterns:
            for i, line in enumerate(text.splitlines(), start=1):
                if re.search(pattern, line, re.IGNORECASE):
                    # Skip variable names that suggest placeholders
                    if "placeholder" in line.lower() or "example" in line.lower():
                        continue
                    if "xxx" in line.lower() or "your_" in line.lower():
                        continue
                    findings.append(
                        {
                            "file": str(filepath),
                            "line": i,
                            "code": "SEC001",
                            "message": msg,
                            "severity": "high",
                        }
                    )
        return findings

    @staticmethod
    def _check_sql_injection(filepath: Path, text: str) -> list[dict[str, Any]]:
        """Detect potential SQL injection via string formatting."""
        findings: list[dict[str, Any]] = []
        # f-string or .format() in SQL context
        sql_patterns = [
            (r'f["\'].*(?:SELECT|INSERT|UPDATE|DELETE|DROP).*["\']', "f-string in SQL query"),
            (r'\.format\(.*\).*(?:SELECT|INSERT|UPDATE|DELETE)', "string format in SQL query"),
            (r'%(?:s|d).*SELECT|SELECT.*%(?:s|d)', "%-formatting in SQL query"),
        ]
        for i, line in enumerate(text.splitlines(), start=1):
            upper = line.upper()
            if any(kw in upper for kw in ("SELECT", "INSERT", "UPDATE", "DELETE", "DROP")):
                for pattern, msg in sql_patterns:
                    if re.search(pattern, line, re.IGNORECASE):
                        findings.append(
                            {
                                "file": str(filepath),
                                "line": i,
                                "code": "SEC002",
                                "message": msg,
                                "severity": "high",
                            }
                        )
                        break
        return findings

    @staticmethod
    def _check_ast(filepath: Path, tree: ast.AST) -> list[dict[str, Any]]:
        """AST-based security checks."""
        findings: list[dict[str, Any]] = []
        for node in ast.walk(tree):
            # eval() / exec()
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in ("eval", "exec"):
                    findings.append(
                        {
                            "file": str(filepath),
                            "line": node.lineno,
                            "code": "SEC003",
                            "message": f"dangerous call: {func.id}()",
                            "severity": "high",
                        }
                    )
                # subprocess with shell=True
                if isinstance(func, ast.Name) and func.id == "Popen":
                    for kw in node.keywords:
                        if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                            findings.append(
                                {
                                    "file": str(filepath),
                                    "line": node.lineno,
                                    "code": "SEC004",
                                    "message": "subprocess with shell=True",
                                    "severity": "medium",
                                }
                            )
                if isinstance(func, ast.Attribute) and func.attr == "run":
                    if isinstance(func.value, ast.Name) and func.value.id == "subprocess":
                        for kw in node.keywords:
                            if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                                findings.append(
                                    {
                                        "file": str(filepath),
                                        "line": node.lineno,
                                        "code": "SEC004",
                                        "message": "subprocess with shell=True",
                                        "severity": "medium",
                                    }
                                )
                # pickle.loads
                if isinstance(func, ast.Attribute) and func.attr in ("loads", "load"):
                    if isinstance(func.value, ast.Name) and func.value.id == "pickle":
                        findings.append(
                            {
                                "file": str(filepath),
                                "line": node.lineno,
                                "code": "SEC005",
                                "message": "insecure pickle deserialization",
                                "severity": "high",
                            }
                        )
                # weak hash: hashlib.md5 / hashlib.sha1
                if isinstance(func, ast.Attribute) and func.attr in ("md5", "sha1"):
                    if isinstance(func.value, ast.Name) and func.value.id == "hashlib":
                        findings.append(
                            {
                                "file": str(filepath),
                                "line": node.lineno,
                                "code": "SEC006",
                                "message": f"weak hash algorithm: {func.attr}",
                                "severity": "medium",
                            }
                        )
        return findings


__all__ = ["SecurityScanTool"]
