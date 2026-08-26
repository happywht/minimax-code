"""Text search tool — content + filename search with ripgrep fast path.

When ``rg`` is on ``PATH`` we shell out to it (one-shot, no shell)
for blazing fast recursive search. Otherwise we fall back to a
pure-Python walker that uses :mod:`re` for content search and
:mod:`fnmatch` for filename search.

Since v1.6.0 a run inside a write sandbox always uses the Python
walker: rg cannot see the sandbox mirrors, so the walker unions
mirror files into the workspace candidates (sandbox copy wins on
key collision) and reads every candidate through the overlay.
Narrow ``path`` when possible — the walker is slower than rg.

The tool returns a list of matches capped at ``max_results`` to
keep the LLM prompt small. The default cap (200) is enough for
most tasks; agents that want exhaustive results should paginate
by narrowing the path or pattern.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ...workspace_ctx import current_sandbox
from .base import Tool, ToolResult, register_tool
from .file_ops import PathSecurityError, safe_resolve
from .sandbox import overlay_read_target, sandbox_mirror_files

# Search output limits.
_DEFAULT_MAX_RESULTS = 200
_MAX_FILE_BYTES = 10 * 1024 * 1024  # skip files larger than 10 MiB
_BINARY_SNIFF_BYTES = 8192


@register_tool
class SearchFilesTool(Tool):
    name = "search_files"
    description = (
        "Search for a text pattern (literal or regex) inside files under a "
        "directory. By default uses ripgrep if available, else falls back "
        "to a pure-Python walker. Returns up to 'max_results' matches with "
        "file path, line number, and the matched line (truncated to 400 "
        "chars). Optionally filter by file glob (e.g. '*.py'). Inside a "
        "write sandbox the search transparently covers your sandboxed "
        "writes too (slower Python engine — narrow 'path' when possible)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Text to search for."},
            "path": {"type": "string", "description": "Root directory to search."},
            "regex": {"type": "boolean", "default": False, "description": "If True, 'pattern' is a regex."},
            "file_pattern": {"type": "string", "default": "*", "description": "Only search files matching this glob."},
            "case_sensitive": {"type": "boolean", "default": True},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 5000, "default": _DEFAULT_MAX_RESULTS},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        pattern = kwargs.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return ToolResult.fail("'pattern' must be a non-empty string")

        path_arg = kwargs.get("path") or "."
        try:
            root = safe_resolve(path_arg)
        except PathSecurityError as exc:
            return ToolResult.fail(str(exc))

        if not root.exists():
            return ToolResult.fail(f"path not found: {root}")
        if root.is_file():
            search_root = root.parent
            single_file = root
        else:
            search_root = root
            single_file = None

        is_regex = bool(kwargs.get("regex", False))
        file_pattern = kwargs.get("file_pattern") or "*"
        case_sensitive = bool(kwargs.get("case_sensitive", True))
        max_results = int(kwargs.get("max_results") or _DEFAULT_MAX_RESULTS)

        # Pre-compile a Python regex for the fallback path. When
        # not in regex mode we still pass a pre-compiled regex so
        # the walker has a single search primitive to call.
        rx: re.Pattern[str]
        if is_regex:
            try:
                rx = re.compile(pattern, flags=0 if case_sensitive else re.IGNORECASE)
            except re.error as exc:
                return ToolResult.fail(f"invalid regex: {exc}")
        else:
            escaped = re.escape(pattern)
            rx = re.compile(escaped, flags=0 if case_sensitive else re.IGNORECASE)

        # Sandbox runs always take the Python walker — rg cannot see the
        # sandbox mirrors, so the fast path would silently miss the
        # sub-agent's own writes.
        sandbox = current_sandbox()
        if shutil.which("rg") and sandbox is None:
            try:
                return await _rg_search(
                    pattern=pattern,
                    root=search_root,
                    regex=is_regex,
                    file_pattern=file_pattern,
                    case_sensitive=case_sensitive,
                    max_results=max_results,
                    single_file=single_file,
                )
            except Exception as exc:  # pragma: no cover — defensive fallback
                # rg failed; fall through to Python walker.
                return ToolResult.fail(f"ripgrep failed: {exc}")

        return _python_search(
            root=search_root,
            rx=rx,
            file_pattern=file_pattern,
            max_results=max_results,
            single_file=single_file,
            sandbox=sandbox,
        )


# ---------------------------------------------------------------------------
# ripgrep fast path
# ---------------------------------------------------------------------------


async def _rg_search(
    *,
    pattern: str,
    root: Path,
    regex: bool,
    file_pattern: str,
    case_sensitive: bool,
    max_results: int,
    single_file: Path | None,
) -> ToolResult:
    cmd: list[str] = ["rg", "--json", "--color=never"]
    if not regex:
        cmd.append("--fixed-strings")
    if not case_sensitive:
        cmd.append("-i")
    if file_pattern and file_pattern != "*":
        cmd.extend(["--glob", file_pattern])
    # rg already skips hidden directories; belt-and-braces in case a
    # future flag flips that default (``.minimax`` must stay invisible).
    cmd.extend(["--glob", "!.minimax"])
    # Collect more than max_results then trim — rg has no early-exit
    # we can rely on across versions, so we over-fetch and slice.
    cmd.extend(["-m", str(max_results), "--", pattern, str(root)])
    if single_file is not None:
        cmd[-1] = str(single_file)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return ToolResult.fail("ripgrep disappeared mid-flight")

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=60)
    except TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return ToolResult.fail("ripgrep timed out after 60s")

    if proc.returncode not in (0, 1):  # 1 == "no matches"
        return ToolResult.fail(
            f"ripgrep exited with code {proc.returncode}: {stderr_b.decode('utf-8', 'replace')[:400]}"
        )

    matches = _parse_rg_json(stdout_b.decode("utf-8", "replace"), max_results, root)
    return ToolResult.ok(
        output={"root": str(root), "matches": matches, "engine": "ripgrep"},
        count=len(matches),
        engine="ripgrep",
    )


def _parse_rg_json(text: str, limit: int, root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") != "match":
            continue
        data = payload.get("data") or {}
        path_str = (data.get("path") or {}).get("text")
        lineno = data.get("line_number")
        content = (data.get("lines") or {}).get("text")
        if not isinstance(path_str, str) or not isinstance(lineno, int) or not isinstance(content, str):
            continue
        content = content.rstrip("\r\n")
        try:
            rel = str(Path(path_str).resolve().relative_to(root))
        except ValueError:
            rel = path_str
        if len(content) > 400:
            content = content[:397] + "..."
        out.append({"file": rel, "line": lineno, "text": content})
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Pure-Python fallback
# ---------------------------------------------------------------------------


def _python_search(
    *,
    root: Path,
    rx: re.Pattern[str],
    file_pattern: str,
    max_results: int,
    single_file: Path | None,
    sandbox: Path | None = None,
) -> ToolResult:
    import fnmatch

    # Candidates carry the workspace-relative posix path alongside the
    # path to actually *read* — under a sandbox the read target may be
    # the mirror copy while the reported address stays the workspace
    # one (never leak sandbox locations into results).
    candidates: list[tuple[Path, str]]
    if single_file is not None:
        read_path = overlay_read_target(single_file) if sandbox else single_file
        candidates = [(read_path, _workspace_rel(single_file, root))]
    else:
        # Insertion-ordered union: the workspace walk keeps its legacy
        # order, a sandbox mirror overrides the same-key entry in place
        # (the sub-agent's write is the newer truth), mirror-only files
        # append at the end.
        seen: dict[str, Path] = {}
        for f in _walk_files(root, file_pattern):
            seen[_workspace_rel(f, root)] = f
        if sandbox is not None:
            for rel in sandbox_mirror_files(sandbox):
                if file_pattern not in ("*", "") and not fnmatch.fnmatch(
                    rel.rsplit("/", 1)[-1], file_pattern
                ):
                    continue
                seen[rel] = sandbox.joinpath(*rel.split("/"))
        candidates = [(p, rel) for rel, p in seen.items()]

    matches: list[dict[str, Any]] = []
    for read_path, rel in candidates:
        try:
            if read_path.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        try:
            data = read_path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:_BINARY_SNIFF_BYTES]:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = data.decode("latin-1")
            except UnicodeDecodeError:
                continue

        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                matches.append(_format_match(rel, i, line))
                if len(matches) >= max_results:
                    return ToolResult.ok(
                        output={"root": str(root), "matches": matches, "engine": "python"},
                        count=len(matches),
                        engine="python",
                    )

    return ToolResult.ok(
        output={"root": str(root), "matches": matches, "engine": "python"},
        count=len(matches),
        engine="python",
    )


def _walk_files(root: Path, file_pattern: str) -> Iterable[Path]:
    import fnmatch

    if root.is_file():
        if file_pattern in ("*", "") or fnmatch.fnmatch(root.name, file_pattern):
            yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip dot-directories and the usual noise. ``.minimax`` holds
        # our own machinery (backups / sandboxes / artifacts) — it must
        # never leak into search results.
        dirnames[:] = [
            d
            for d in dirnames
            if d not in (
                ".git", ".venv", "node_modules", "__pycache__",
                ".pytest_cache", ".minimax",
            )
        ]
        for name in filenames:
            if file_pattern not in ("*", "") and not fnmatch.fnmatch(name, file_pattern):
                continue
            yield Path(dirpath) / name


def _workspace_rel(file: Path, root: Path) -> str:
    """Workspace-relative posix path for *file* (absolute on anchor mismatch)."""
    try:
        rel = str(file.resolve().relative_to(root))
    except ValueError:
        return str(file)
    return rel.replace("\\", "/")


def _format_match(rel: str, lineno: int, line: str) -> dict[str, Any]:
    if len(line) > 400:
        line = line[:397] + "..."
    return {"file": rel, "line": lineno, "text": line}


__all__ = ["SearchFilesTool"]
