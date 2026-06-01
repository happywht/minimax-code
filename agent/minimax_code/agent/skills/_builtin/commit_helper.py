"""Built-in tools for the ``commit-helper`` skill.

Tools
-----

* :class:`GetGitDiffTool` — runs ``git diff`` (or ``git diff --cached``)
  and returns the textual diff plus a small structured summary
  (file list, +/-, total bytes). Default invocation returns the
  unstaged diff in the current directory; pass ``staged=True`` to
  read the staged diff, and ``path`` to scope the diff to a
  subdirectory or file.

The tool is deliberately conservative: it never modifies the
repository, it never runs ``git add`` / ``git commit`` itself
(those are out of scope for the skill — the user must run them).
The exit code is surfaced so the agent can react to errors.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from ...tools.base import Tool, ToolResult
from ...tools.file_ops import PathSecurityError, safe_resolve
from ..runtime import SkillToolProvider

# Hard cap on diff size — protects the LLM context from huge commits.
_MAX_DIFF_BYTES = 200 * 1024  # 200 KiB


class GetGitDiffTool(Tool):
    name = "get_git_diff"
    description = (
        "Run `git diff` in the workspace and return the textual diff plus a "
        "small structured summary (file list, line counts, byte size). Pass "
        "`staged=true` to inspect staged-but-not-committed changes; pass "
        "`path` to scope the diff to a subdirectory or single file. Output "
        "is capped at 200 KiB; larger diffs are truncated with a notice."
    )
    parameters = {
        "type": "object",
        "properties": {
            "staged": {
                "type": "boolean",
                "default": False,
                "description": "When true, returns the staged diff (`git diff --cached`).",
            },
            "path": {
                "type": "string",
                "description": "Subdirectory or file to scope the diff to.",
            },
            "max_bytes": {
                "type": "integer",
                "minimum": 1024,
                "maximum": 5 * 1024 * 1024,
                "default": _MAX_DIFF_BYTES,
                "description": "Override the per-call output cap (default 200 KiB).",
            },
        },
        "additionalProperties": False,
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        staged = bool(kwargs.get("staged", False))
        raw_path = kwargs.get("path")
        max_bytes = int(kwargs.get("max_bytes") or _MAX_DIFF_BYTES)

        # Resolve path against the workspace safety policy.
        cwd: str | None = None
        if raw_path:
            try:
                resolved = safe_resolve(raw_path)
            except PathSecurityError as exc:
                return ToolResult.fail(str(exc))
            cwd = str(resolved)

        cmd: list[str] = ["git", "diff"]
        if staged:
            cmd.append("--cached")
        if raw_path:
            cmd.extend(["--", raw_path])

        # Inherit environment, force unbuffered output.
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        # Disable git's colour codes — they're noise in tool output.
        env.setdefault("GIT_PAGER", "cat")

        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return ToolResult.fail("git is not installed or not on PATH")
        except OSError as exc:
            return ToolResult.fail(f"failed to spawn git: {exc}")

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return ToolResult.fail("git diff timed out after 15s")

        diff_text = stdout_b.decode("utf-8", errors="replace")
        stderr_text = stderr_b.decode("utf-8", errors="replace").strip()
        exit_code = proc.returncode

        if exit_code != 0 and not diff_text:
            return ToolResult.fail(
                f"git diff exited with code {exit_code}: {stderr_text or 'no output'}"
            )

        truncated = False
        if len(diff_text.encode("utf-8")) > max_bytes:
            diff_text = diff_text.encode("utf-8")[:max_bytes].decode("utf-8", errors="replace")
            diff_text += f"\n…(truncated, output exceeded {max_bytes} bytes)"
            truncated = True

        # Build a tiny summary.
        files: list[str] = []
        additions = 0
        deletions = 0
        for line in diff_text.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("diff --git "):
                # "diff --git a/foo b/foo" → foo
                try:
                    rest = line[len("diff --git ") :]
                    _, b = rest.split(" b/", 1)
                    files.append(b)
                except ValueError:
                    continue
            elif line.startswith("+") and not line.startswith("+++"):
                additions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1

        return ToolResult.ok(
            output={
                "diff": diff_text,
                "summary": {
                    "files": files,
                    "additions": additions,
                    "deletions": deletions,
                    "staged": staged,
                    "path": raw_path,
                    "bytes": len(diff_text.encode("utf-8")),
                },
                "truncated": truncated,
                "stderr": stderr_text,
                "exit_code": exit_code,
            },
            files=len(files),
            additions=additions,
            deletions=deletions,
            truncated=truncated,
        )


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class Provider(SkillToolProvider):
    """Adds :class:`GetGitDiffTool` to the agent's tool registry."""

    def install(self, tool_registry: Any) -> set[str]:
        if tool_registry is None:
            return set()
        # Don't double-install.
        if tool_registry.has(GetGitDiffTool.name):
            return set()
        tool_registry.register(GetGitDiffTool())
        return {GetGitDiffTool.name}

    def uninstall(self, tool_registry: Any) -> None:
        if tool_registry is None:
            return
        tool_registry.unregister(GetGitDiffTool.name)


__all__ = ["GetGitDiffTool", "Provider"]
