"""Structured patch-preview IPC handlers.

``git.diff`` is intentionally raw text because code-review and LLM
flows want the original unified diff.  The UI, however, needs a
file/hunk/line structure for compact previews and approvals.  This
module keeps that view read-only and derives it from the same git diff
source without changing the existing ``git.*`` contract.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .handler_utils import HandlerError
from .handlers_git import _GIT_ERROR, _resolve_cwd, _run_git
from .protocol import INVALID_PARAMS
from .server import Context

logger = logging.getLogger(__name__)

_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_lines>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_lines>\d+))? @@(?P<header>.*)$"
)


def _diff_args(params: dict[str, Any]) -> tuple[list[str], str, str | None]:
    scope = params.get("scope", "working")
    ref = params.get("ref")
    if not isinstance(scope, str):
        raise HandlerError(INVALID_PARAMS, "'scope' must be a string when provided")
    if ref is not None and not isinstance(ref, str):
        raise HandlerError(INVALID_PARAMS, "'ref' must be a string when provided")

    base_args = ["diff", "--no-color", "-M"]
    if ref is not None:
        return [*base_args, ref], ref, ref
    if scope == "staged":
        return [*base_args, "--staged"], scope, None
    if scope == "branch":
        return [*base_args, "HEAD~1..HEAD"], scope, None
    if scope == "working":
        return base_args, scope, None
    raise HandlerError(
        INVALID_PARAMS,
        f"unknown scope {scope!r}; expected 'staged' | 'branch' | 'working' or an explicit 'ref'",
    )


def _strip_prefix(path: str) -> str:
    if path == "/dev/null":
        return path
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def parse_unified_diff(diff_text: str) -> dict[str, Any]:
    """Parse a unified git diff into UI-friendly file/hunk records."""

    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    hunk: dict[str, Any] | None = None
    old_line = 0
    new_line = 0

    def finish_file() -> None:
        nonlocal current, hunk
        if current is None:
            return
        current["status"] = _status_for_file(current)
        files.append(current)
        current = None
        hunk = None

    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            finish_file()
            parts = raw.split(" ")
            old_path = _strip_prefix(parts[2]) if len(parts) > 2 else ""
            new_path = _strip_prefix(parts[3]) if len(parts) > 3 else old_path
            current = {
                "old_path": old_path,
                "new_path": new_path,
                "path": new_path if new_path != "/dev/null" else old_path,
                "status": "modified",
                "additions": 0,
                "deletions": 0,
                "binary": False,
                "hunks": [],
            }
            hunk = None
            continue

        if current is None:
            continue

        if raw.startswith("rename from "):
            current["old_path"] = raw[len("rename from ") :].strip()
            continue
        if raw.startswith("rename to "):
            current["new_path"] = raw[len("rename to ") :].strip()
            current["path"] = current["new_path"]
            continue
        if raw.startswith("new file mode "):
            current["status"] = "added"
            continue
        if raw.startswith("deleted file mode "):
            current["status"] = "deleted"
            continue
        if raw.startswith("Binary files "):
            current["binary"] = True
            continue
        if raw.startswith("--- "):
            current["old_path"] = _strip_prefix(raw[4:].split("\t", 1)[0].strip())
            continue
        if raw.startswith("+++ "):
            current["new_path"] = _strip_prefix(raw[4:].split("\t", 1)[0].strip())
            current["path"] = (
                current["new_path"]
                if current["new_path"] != "/dev/null"
                else current["old_path"]
            )
            continue

        match = _HUNK_RE.match(raw)
        if match:
            old_line = int(match.group("old_start"))
            new_line = int(match.group("new_start"))
            hunk = {
                "old_start": old_line,
                "old_lines": int(match.group("old_lines") or "1"),
                "new_start": new_line,
                "new_lines": int(match.group("new_lines") or "1"),
                "header": match.group("header").strip(),
                "lines": [],
            }
            current["hunks"].append(hunk)
            continue

        if hunk is None:
            continue

        if raw.startswith("+"):
            content = raw[1:]
            hunk["lines"].append(
                {"kind": "add", "old_line": None, "new_line": new_line, "content": content}
            )
            current["additions"] += 1
            new_line += 1
        elif raw.startswith("-"):
            content = raw[1:]
            hunk["lines"].append(
                {"kind": "delete", "old_line": old_line, "new_line": None, "content": content}
            )
            current["deletions"] += 1
            old_line += 1
        elif raw.startswith(" "):
            content = raw[1:]
            hunk["lines"].append(
                {"kind": "context", "old_line": old_line, "new_line": new_line, "content": content}
            )
            old_line += 1
            new_line += 1
        else:
            hunk["lines"].append(
                {"kind": "meta", "old_line": None, "new_line": None, "content": raw}
            )

    finish_file()
    return {
        "files": files,
        "stats": {
            "files": len(files),
            "additions": sum(int(f["additions"]) for f in files),
            "deletions": sum(int(f["deletions"]) for f in files),
        },
    }


def _status_for_file(file: dict[str, Any]) -> str:
    if file.get("status") in {"added", "deleted"}:
        return str(file["status"])
    old_path = file.get("old_path")
    new_path = file.get("new_path")
    if old_path == "/dev/null":
        return "added"
    if new_path == "/dev/null":
        return "deleted"
    if old_path and new_path and old_path != new_path:
        return "renamed"
    return "modified"


def register_patch_handlers(server: Any) -> None:
    """Register ``patch.*`` read-only preview APIs."""

    async def handle_patch_preview(params: Any, ctx: Context) -> None:
        try:
            p = params if isinstance(params, dict) else {}
            cwd = _resolve_cwd(p)
            cmd, scope, ref = _diff_args(p)
            diff_text = _run_git(cmd, cwd=cwd)
            parsed = parse_unified_diff(diff_text)
            await ctx.reply(
                {
                    "scope": scope,
                    "ref": ref,
                    "diff": diff_text,
                    "files": parsed["files"],
                    "stats": parsed["stats"],
                }
            )
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:
            logger.exception("patch.preview failed")
            await ctx.reply_error(_GIT_ERROR, "patch.preview failed")

    server.register("patch.preview", handle_patch_preview)


__all__ = ["parse_unified_diff", "register_patch_handlers"]
