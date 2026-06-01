"""Built-in tools for the ``code-review`` skill.

Tools
-----

* :class:`RunLinterTool` — invokes the workspace linter
  (``ruff`` if available, ``flake8`` / ``pyflakes`` as
  fallbacks) and returns structured findings
  (``{file, line, col, code, message, severity}``). The tool
  shells out to the linter so we don't have to vendor the
  parsing rules; if no linter is installed it returns a
  helpful error rather than a silent success.
* :class:`FindComplexFunctionsTool` — walks a directory,
  parses each Python file with :mod:`ast`, and reports
  functions whose cyclomatic complexity (McCabe-style
  counting: ``if`` / ``for`` / ``while`` / ``except`` /
  ``and`` / ``or`` / ``with`` / ``assert`` / ``comprehension``
  branches) exceeds ``threshold`` (default ``10``).

Both tools honour the workspace-safety policy enforced by
:func:`safe_resolve` — paths outside the workspace, or inside
well-known sensitive directories, are rejected.
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
from ..runtime import SkillToolProvider

# Default complexity threshold; matches the "code is too complex"
# guidance popular in the Python community.
_DEFAULT_THRESHOLD = 10

# A short list of lint commands we know how to interpret.
_LINTERS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    # (display name, argv, output-format)
    ("ruff", ("ruff", "check", "--output-format", "json"), "json"),
    ("flake8", ("flake8", "--format=json"), "json"),  # needs plugin-flake8-json
    ("pyflakes", ("pyflakes",), "text"),
)


class RunLinterTool(Tool):
    name = "run_linter"
    description = (
        "Run a Python linter (ruff → flake8 → pyflakes) against a file or "
        "directory inside the workspace. Returns a list of findings — each "
        "with `file`, `line`, `col`, `code`, `message`, and `severity` — "
        "plus the linter used. Use this as the static-analysis leg of a "
        "code review; pair it with `find_complex_functions` for complexity."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File or directory to lint (must live inside the workspace).",
            },
            "linter": {
                "type": "string",
                "enum": ["auto", "ruff", "flake8", "pyflakes"],
                "default": "auto",
                "description": "Force a specific linter; default 'auto' picks the first available.",
            },
            "max_findings": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10_000,
                "default": 500,
                "description": "Cap on findings returned to keep the LLM context sane.",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        raw_path = kwargs.get("path")
        preferred = kwargs.get("linter", "auto") or "auto"
        max_findings = int(kwargs.get("max_findings") or 500)

        try:
            target = safe_resolve(raw_path)
        except PathSecurityError as exc:
            return ToolResult.fail(str(exc))
        if not target.exists():
            return ToolResult.fail(f"path not found: {raw_path}")

        linter_name, argv = self._pick_linter(preferred)
        if linter_name is None:
            return ToolResult.fail(
                "no supported linter found (install ruff, flake8, or pyflakes)"
            )

        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
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
        except FileNotFoundError as exc:
            return ToolResult.fail(f"linter binary not found: {exc}")
        except OSError as exc:
            return ToolResult.fail(f"failed to spawn linter: {exc}")

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return ToolResult.fail("linter timed out after 30s")

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace").strip()
        # Many linters exit non-zero when they find issues — treat that
        # as "success with findings" rather than a failure.
        findings, parse_error = _parse_linter_output(linter_name, stdout)
        truncated = False
        if len(findings) > max_findings:
            findings = findings[:max_findings]
            truncated = True

        return ToolResult.ok(
            output={
                "linter": linter_name,
                "path": str(target),
                "findings": findings,
                "stderr": stderr,
                "exit_code": proc.returncode,
                "truncated": truncated,
                "parse_error": parse_error,
            },
            linter=linter_name,
            count=len(findings),
            truncated=truncated,
        )

    @staticmethod
    def _pick_linter(preferred: str) -> tuple[str | None, tuple[str, ...]]:
        if preferred != "auto":
            for name, argv, _fmt in _LINTERS:
                if name == preferred and shutil.which(name):
                    return name, argv
            return None, ()
        for name, argv, _fmt in _LINTERS:
            if shutil.which(name):
                return name, argv
        return None, ()


class FindComplexFunctionsTool(Tool):
    name = "find_complex_functions"
    description = (
        "Walk a directory and report Python functions whose cyclomatic "
        "complexity exceeds `threshold` (default 10). Returns a list of "
        "findings — each with `file`, `name`, `line`, `complexity`, and "
        "the `threshold`. Counts `if`, `for`, `while`, `except`, `and`, "
        "`or`, `with`, `assert`, and comprehension branches."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory or file to scan (must live inside the workspace).",
            },
            "threshold": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": _DEFAULT_THRESHOLD,
                "description": "Cyclomatic complexity threshold (default 10).",
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
        threshold = int(kwargs.get("threshold") or _DEFAULT_THRESHOLD)
        max_findings = int(kwargs.get("max_findings") or 200)

        try:
            target = safe_resolve(raw_path)
        except PathSecurityError as exc:
            return ToolResult.fail(str(exc))
        if not target.exists():
            return ToolResult.fail(f"path not found: {raw_path}")

        files: list[Path]
        if target.is_file():
            files = [target]
        else:
            files = sorted(target.rglob("*.py"))
        files = [f for f in files if not _is_skippable(f)]

        findings: list[dict[str, Any]] = []
        for f in files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                tree = ast.parse(text, filename=str(f))
            except SyntaxError as exc:
                findings.append(
                    {
                        "file": str(f),
                        "name": "<module>",
                        "line": exc.lineno or 0,
                        "complexity": 0,
                        "threshold": threshold,
                        "error": f"SyntaxError: {exc.msg}",
                    }
                )
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    cc = _cyclomatic_complexity(node)
                    if cc > threshold:
                        findings.append(
                            {
                                "file": str(f),
                                "name": node.name,
                                "line": int(node.lineno),
                                "complexity": cc,
                                "threshold": threshold,
                            }
                        )
            if len(findings) >= max_findings:
                break

        truncated = len(findings) > max_findings
        if truncated:
            findings = findings[:max_findings]
        return ToolResult.ok(
            output={"path": str(target), "findings": findings, "truncated": truncated},
            count=len(findings),
            truncated=truncated,
            threshold=threshold,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_skippable(path: Path) -> bool:
    """Return True if ``path`` is in a directory we should never lint.

    Excludes caches, virtualenvs, and VCS metadata. The checks
    are deliberately cheap — no stat() calls, no symlink
    chasing.
    """
    parts = set(path.parts)
    for bad in (
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        "node_modules",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
    ):
        if bad in parts:
            return True
    return False


def _cyclomatic_complexity(fn: ast.AST) -> int:
    """Cyclomatic complexity of a function/method (McCabe-style).

    We start from 1 (the function entry edge) and add 1 for every
    decision point: ``if``, ``elif`` is folded into ``if`` (we
    only count the ``if``-line, since ``elif`` does not add a new
    entry edge in our model — but for simplicity we count each
    ``if`` / ``elif`` as one branch), ``for``, ``while``,
    ``except``, ``and``, ``or``, ``with``, ``assert``, and
    comprehensions / generator expressions. Boolean operators
    that short-circuit also count.
    """
    complexity = 1
    for node in ast.walk(fn):
        if isinstance(node, (ast.If, ast.IfExp)):
            complexity += 1
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            complexity += 1
        elif isinstance(node, ast.ExceptHandler):
            complexity += 1
        elif isinstance(node, ast.With):
            complexity += 1
        elif isinstance(node, ast.Assert):
            complexity += 1
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            complexity += 1
        elif isinstance(node, ast.BoolOp):
            # ``a and b and c`` adds 2 decision points.
            complexity += max(0, len(getattr(node, "values", [])) - 1)
    return complexity


def _parse_linter_output(
    linter_name: str, stdout: str
) -> tuple[list[dict[str, Any]], str | None]:
    """Normalise linter output into a list of finding dicts.

    Returns ``(findings, parse_error)`` — ``parse_error`` is
    non-None when the output looked malformed (e.g. non-JSON
    where we expected JSON) so the LLM can react.
    """
    if not stdout.strip():
        return [], None
    if linter_name == "ruff" or linter_name == "flake8":
        import json as _json

        try:
            data = _json.loads(stdout)
        except _json.JSONDecodeError as exc:
            return [], f"{linter_name} output was not valid JSON: {exc}"
        out: list[dict[str, Any]] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            out.append(
                {
                    "file": str(row.get("filename") or row.get("file") or ""),
                    "line": int(row.get("line") or 0),
                    "col": int(row.get("column") or row.get("col") or 0),
                    "code": str(row.get("code") or row.get("code") or ""),
                    "message": str(row.get("message") or ""),
                    "severity": "error" if (row.get("code") or "").startswith("E") else "warning",
                }
            )
        return out, None

    # pyflakes emits one ``file:line:col: message`` per line.
    out2: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # "path/to/file.py:12:3: undefined name 'foo'"
        parts = line.split(":", 3)
        if len(parts) < 4:
            continue
        file_, lineno, col, message = parts
        try:
            ln = int(lineno)
            co = int(col)
        except ValueError:
            continue
        out2.append(
            {
                "file": file_.strip(),
                "line": ln,
                "col": co,
                "code": "pyflakes",
                "message": message.strip(),
                "severity": "warning",
            }
        )
    return out2, None


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class Provider(SkillToolProvider):
    """Adds the code-review tools to the agent's tool registry."""

    def install(self, tool_registry: Any) -> set[str]:
        if tool_registry is None:
            return set()
        added: set[str] = set()
        for cls in (RunLinterTool, FindComplexFunctionsTool):
            if not tool_registry.has(cls.name):
                tool_registry.register(cls())
                added.add(cls.name)
        return added

    def uninstall(self, tool_registry: Any) -> None:
        if tool_registry is None:
            return
        for cls in (RunLinterTool, FindComplexFunctionsTool):
            tool_registry.unregister(cls.name)


__all__ = ["RunLinterTool", "FindComplexFunctionsTool", "Provider"]
