"""Structured patch-preview IPC handlers.

``git.diff`` is intentionally raw text because code-review and LLM
flows want the original unified diff.  The UI, however, needs a
file/hunk/line structure for compact previews and approvals.  This
module keeps that view read-only and derives it from the same git diff
source without changing the existing ``git.*`` contract.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from typing import Any

from .handler_utils import HandlerError
from .handlers_git import _GIT_ERROR, _GIT_TIMEOUT_S, _resolve_cwd, _run_git
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


def _operation_diff_args(scope: str) -> list[str]:
    if scope == "working":
        return ["diff", "--no-color", "-M"]
    if scope == "staged":
        return ["diff", "--no-color", "-M", "--staged"]
    raise HandlerError(
        INVALID_PARAMS,
        "hunk operations only support scope 'working' or 'staged'",
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


def _required_str(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise HandlerError(INVALID_PARAMS, f"'{key}' must be a non-empty string")
    return value


def _required_int(params: dict[str, Any], key: str) -> int:
    value = params.get(key)
    if not isinstance(value, int) or value < 0:
        raise HandlerError(INVALID_PARAMS, f"'{key}' must be a non-negative integer")
    return value


def _validate_hunk_anchor(params: dict[str, Any], hunk: dict[str, Any]) -> None:
    for key in ("old_start", "new_start"):
        value = params.get(key)
        if value is None:
            continue
        if not isinstance(value, int):
            raise HandlerError(INVALID_PARAMS, f"'{key}' must be an integer when provided")
        if int(hunk[key]) != value:
            raise HandlerError(
                INVALID_PARAMS,
                "hunk anchor is stale; refresh the diff and try again",
                data={"expected": {key: hunk[key]}, "received": {key: value}},
            )


def _find_hunk(
    parsed: dict[str, Any],
    *,
    file_path: str,
    hunk_index: int,
    params: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    for file in parsed.get("files", []):
        if file.get("path") != file_path and file.get("old_path") != file_path:
            continue
        if file.get("binary"):
            raise HandlerError(INVALID_PARAMS, "binary files do not support hunk operations")
        if file.get("status") == "renamed":
            raise HandlerError(INVALID_PARAMS, "renamed files do not support hunk operations yet")
        hunks = file.get("hunks") or []
        if hunk_index >= len(hunks):
            raise HandlerError(INVALID_PARAMS, "hunk_index is out of range")
        hunk = hunks[hunk_index]
        _validate_hunk_anchor(params, hunk)
        return file, hunk
    raise HandlerError(INVALID_PARAMS, "file_path was not found in the current diff")


def _format_range(start: int, count: int) -> str:
    if count == 1:
        return str(start)
    return f"{start},{count}"


def _line_prefix(kind: str) -> str:
    if kind == "add":
        return "+"
    if kind == "delete":
        return "-"
    return " "


def _single_hunk_patch(file: dict[str, Any], hunk: dict[str, Any]) -> str:
    old_path = str(file.get("old_path") or file.get("path") or "")
    new_path = str(file.get("new_path") or file.get("path") or "")
    if not old_path or not new_path:
        raise HandlerError(INVALID_PARAMS, "patch file paths are incomplete")
    if old_path == "/dev/null" or new_path == "/dev/null":
        raise HandlerError(
            INVALID_PARAMS,
            "added/deleted files do not support partial hunk operations yet",
        )

    old_label = f"a/{old_path}"
    new_label = f"b/{new_path}"
    header = str(hunk.get("header") or "")
    lines = [
        f"diff --git {old_label} {new_label}",
        f"--- {old_label}",
        f"+++ {new_label}",
        (
            f"@@ -{_format_range(int(hunk['old_start']), int(hunk['old_lines']))} "
            f"+{_format_range(int(hunk['new_start']), int(hunk['new_lines']))} @@"
            f"{(' ' + header) if header else ''}"
        ),
    ]
    for line in hunk.get("lines") or []:
        kind = str(line.get("kind"))
        if kind == "meta":
            lines.append(str(line.get("content") or ""))
            continue
        lines.append(f"{_line_prefix(kind)}{line.get('content') or ''}")
    return "\n".join(lines) + "\n"


def _run_git_with_input(
    args: list[str],
    *,
    cwd: str | os.PathLike[str] | None,
    stdin_text: str,
) -> str:
    cmd = ["git", *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            input=stdin_text.encode("utf-8"),
            capture_output=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover
        raise HandlerError(_GIT_ERROR, "git binary not found on PATH", data={"cmd": cmd}) from exc
    except subprocess.TimeoutExpired as exc:
        raise HandlerError(
            _GIT_ERROR,
            f"git {' '.join(args)} timed out after {_GIT_TIMEOUT_S}s",
            data={"cmd": cmd, "timeout_s": _GIT_TIMEOUT_S},
        ) from exc

    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        if not stderr:
            stderr = "git failed with no stderr"
        raise HandlerError(
            _GIT_ERROR,
            stderr,
            data={"cmd": cmd, "returncode": proc.returncode},
        )
    return proc.stdout.decode("utf-8", errors="replace")


def _operation_result(
    *,
    operation: str,
    scope: str,
    file_path: str,
    hunk_index: int,
) -> dict[str, Any]:
    return {
        "ok": True,
        "operation": operation,
        "scope": scope,
        "file_path": file_path,
        "hunk_index": hunk_index,
    }


def register_patch_handlers(server: Any) -> None:
    """Register ``patch.*`` preview and hunk operation APIs."""

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

    async def handle_apply_hunk(params: Any, ctx: Context) -> None:
        try:
            p = params if isinstance(params, dict) else {}
            cwd = _resolve_cwd(p)
            scope = p.get("scope", "working")
            if not isinstance(scope, str):
                raise HandlerError(INVALID_PARAMS, "'scope' must be a string when provided")
            file_path = _required_str(p, "file_path")
            hunk_index = _required_int(p, "hunk_index")
            diff_text = _run_git(_operation_diff_args(scope), cwd=cwd)
            file, hunk = _find_hunk(
                parse_unified_diff(diff_text),
                file_path=file_path,
                hunk_index=hunk_index,
                params=p,
            )
            patch = _single_hunk_patch(file, hunk)
            args = ["apply", "--cached", "--check", "--whitespace=nowarn", "-"]
            _run_git_with_input(args, cwd=cwd, stdin_text=patch)
            _run_git_with_input(
                ["apply", "--cached", "--whitespace=nowarn", "-"],
                cwd=cwd,
                stdin_text=patch,
            )
            await ctx.reply(
                _operation_result(
                    operation="apply_hunk",
                    scope=scope,
                    file_path=file_path,
                    hunk_index=hunk_index,
                )
            )
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:
            logger.exception("patch.apply_hunk failed")
            await ctx.reply_error(_GIT_ERROR, "patch.apply_hunk failed")

    async def handle_revert_hunk(params: Any, ctx: Context) -> None:
        try:
            p = params if isinstance(params, dict) else {}
            cwd = _resolve_cwd(p)
            scope = p.get("scope", "working")
            if not isinstance(scope, str):
                raise HandlerError(INVALID_PARAMS, "'scope' must be a string when provided")
            file_path = _required_str(p, "file_path")
            hunk_index = _required_int(p, "hunk_index")
            diff_text = _run_git(_operation_diff_args(scope), cwd=cwd)
            file, hunk = _find_hunk(
                parse_unified_diff(diff_text),
                file_path=file_path,
                hunk_index=hunk_index,
                params=p,
            )
            patch = _single_hunk_patch(file, hunk)
            args = ["apply", "--reverse", "--check", "--whitespace=nowarn", "-"]
            run_args = ["apply", "--reverse", "--whitespace=nowarn", "-"]
            if scope == "staged":
                args.insert(1, "--cached")
                run_args.insert(1, "--cached")
            _run_git_with_input(args, cwd=cwd, stdin_text=patch)
            _run_git_with_input(run_args, cwd=cwd, stdin_text=patch)
            await ctx.reply(
                _operation_result(
                    operation="revert_hunk",
                    scope=scope,
                    file_path=file_path,
                    hunk_index=hunk_index,
                )
            )
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:
            logger.exception("patch.revert_hunk failed")
            await ctx.reply_error(_GIT_ERROR, "patch.revert_hunk failed")

    server.register("patch.preview", handle_patch_preview)
    server.register("patch.apply_hunk", handle_apply_hunk)
    server.register("patch.revert_hunk", handle_revert_hunk)


__all__ = ["parse_unified_diff", "register_patch_handlers"]
