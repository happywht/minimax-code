"""Test coverage tool for the ``code-review`` skill.

Runs pytest with coverage reporting and returns structured results:

* Uses ``pytest --cov`` if available
* Falls back to a heuristic that maps test files to source files

v0.8.0 — Enterprise Multi-Agent.
"""

from __future__ import annotations

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


class TestCoverageTool(Tool):
    name = "test_coverage"
    description = (
        "Check test coverage for Python projects. Uses pytest --cov "
        "if available; falls back to a heuristic mapping of test files "
        "to source files. Returns coverage statistics and per-file data."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Project root directory to check.",
            },
            "threshold": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "default": 80,
                "description": "Minimum coverage percentage threshold (default 80).",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        raw_path = kwargs.get("path")
        threshold = int(kwargs.get("threshold") or 80)

        try:
            target = safe_resolve(raw_path)
        except PathSecurityError as exc:
            return ToolResult.fail(str(exc))
        if not target.exists():
            return ToolResult.fail(f"path not found: {raw_path}")
        if not target.is_dir():
            return ToolResult.fail("path must be a directory for coverage analysis")

        # Try pytest --cov
        if shutil.which("pytest"):
            cov_result = await self._run_pytest_cov(target, threshold)
            if cov_result is not None:
                return cov_result

        # Fallback to heuristic
        return self._heuristic_coverage(target, threshold)

    async def _run_pytest_cov(
        self, target: Path, threshold: int
    ) -> ToolResult | None:
        """Run pytest with --cov and parse the terminal report."""
        argv = (
            "pytest",
            "--cov",
            str(target),
            "--cov-report=term-missing",
            "--no-header",
            "-q",
        )
        env = os.environ.copy()
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = ""  # ensure cov plugin loads

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(target),
            )
        except (FileNotFoundError, OSError):
            return None

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=120.0)
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return None

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")

        # Parse the coverage table
        files_data = _parse_cov_output(stdout)
        total_pct = None

        for entry in files_data:
            if entry.get("file") == "TOTAL":
                total_pct = entry.get("pct")

        if total_pct is None and files_data:
            # Calculate from individual files
            covered = sum(e.get("covered", 0) for e in files_data)
            total = sum(e.get("covered", 0) + e.get("missing", 0) for e in files_data)
            total_pct = (covered / total * 100) if total else 0.0

        below_threshold = [
            e for e in files_data
            if e.get("file") != "TOTAL"
            and e.get("pct") is not None
            and e["pct"] < threshold
        ]

        return ToolResult.ok(
            output={
                "checker": "pytest-cov",
                "path": str(target),
                "total_coverage_pct": total_pct,
                "threshold": threshold,
                "meets_threshold": (total_pct or 0) >= threshold,
                "files": files_data,
                "below_threshold": below_threshold,
                "stderr_summary": stderr[:500] if stderr else "",
            },
            checker="pytest-cov",
            total_coverage=f"{total_pct}%" if total_pct is not None else "N/A",
        )

    @staticmethod
    def _heuristic_coverage(target: Path, threshold: int) -> ToolResult:
        """Heuristic: count test files and map to source files."""
        source_files = [
            f for f in target.rglob("*.py")
            if not (_SKIP_DIRS & set(f.parts))
            and not f.name.startswith("test_")
            and not f.name.endswith("_test.py")
            and f.name != "conftest.py"
        ]
        test_files = [
            f for f in target.rglob("*.py")
            if not (_SKIP_DIRS & set(f.parts))
            and (f.name.startswith("test_") or f.name.endswith("_test.py"))
        ]

        # Map test files to source modules
        test_modules: set[str] = set()
        for tf in test_files:
            test_modules.add(tf.stem.replace("test_", "").replace("_test", ""))

        files_data: list[dict[str, Any]] = []
        covered_count = 0
        for sf in source_files:
            stem = sf.stem
            has_test = stem in test_modules
            if has_test:
                covered_count += 1
            files_data.append(
                {
                    "file": str(sf.relative_to(target)),
                    "has_test": has_test,
                    "test_file": None,
                }
            )

        total = len(source_files)
        pct = (covered_count / total * 100) if total else 100.0

        return ToolResult.ok(
            output={
                "checker": "heuristic",
                "path": str(target),
                "total_coverage_pct": round(pct, 1),
                "threshold": threshold,
                "meets_threshold": pct >= threshold,
                "files": files_data,
                "source_count": total,
                "test_count": len(test_files),
                "note": "heuristic estimate — install pytest-cov for accurate data",
            },
            checker="heuristic",
            total_coverage=f"{pct:.1f}%",
        )


def _parse_cov_output(stdout: str) -> list[dict[str, Any]]:
    """Parse pytest-cov terminal output into structured data."""
    results: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # Format: "file.py              42    10    76%"
        # Or:     "TOTAL                 100    25    75%"
        parts = line.split()
        if len(parts) < 4:
            continue
        # Try to parse the last part as percentage
        pct_str = parts[-1].rstrip("%")
        try:
            pct = float(pct_str)
        except ValueError:
            continue
        # The first part is the filename
        file_name = parts[0]
        try:
            stmts = int(parts[-3])
            miss = int(parts[-2])
        except (ValueError, IndexError):
            stmts = 0
            miss = 0

        results.append(
            {
                "file": file_name,
                "statements": stmts,
                "missing": miss,
                "covered": stmts - miss,
                "pct": pct,
            }
        )
    return results


__all__ = ["TestCoverageTool"]
