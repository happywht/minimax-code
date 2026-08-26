"""File discovery tool — recursive glob pattern matching.

Walks the workspace directory tree and returns file paths matching a
glob pattern. Supports depth limiting, directory exclusion, and result
capping. Uses :func:`fnmatch` for pattern matching and :func:`os.walk`
for traversal, consistent with the pure-Python fallback in ``search.py``.

Since v1.6.0, a run inside a write sandbox unions its sandbox mirrors
into the results (workspace-relative, deduplicated) so ``find_files``
agrees with ``read_file``'s overlay view. ``.minimax`` is excluded by
the walker *unconditionally* — a caller-supplied ``exclude_dirs`` list
replaces the default table and must not be able to un-exclude our own
machinery.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Any

from ...workspace_ctx import current_root, current_sandbox, env_or_cwd_root
from .base import Tool, ToolResult, register_tool
from .file_ops import PathSecurityError, safe_resolve
from .sandbox import sandbox_mirror_files

_DEFAULT_MAX_RESULTS = 200
_DEFAULT_MAX_DEPTH = 20
_DEFAULT_EXCLUDE_DIRS = [
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    "dist",
    "build",
    ".eggs",
    ".minimax",
]

#: Excluded regardless of the caller's ``exclude_dirs`` — holds the
#: sandbox / backup / artifact machinery and must never leak.
_HARD_EXCLUDED_DIRS = frozenset({".minimax"})


@register_tool
class GlobFindTool(Tool):
    name = "find_files"
    description = (
        "Recursively find files matching a glob pattern (e.g. '**/*.py', "
        "'src/**/*.ts'). Returns matching paths relative to the root. "
        "Automatically excludes common noise directories (.git, "
        "node_modules, __pycache__, .venv). Use 'exclude_dirs' to "
        "customise which directories to skip. Inside a write sandbox "
        "your sandboxed writes are included in the results."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern (e.g. '**/*.py', 'src/**/*.ts', '*.json').",
            },
            "path": {
                "type": "string",
                "description": "Root directory to search. Defaults to workspace root.",
            },
            "max_depth": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": _DEFAULT_MAX_DEPTH,
                "description": "Maximum directory depth to traverse.",
            },
            "exclude_dirs": {
                "type": "array",
                "items": {"type": "string"},
                "default": _DEFAULT_EXCLUDE_DIRS,
                "description": "Directory names to skip during traversal.",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5000,
                "default": _DEFAULT_MAX_RESULTS,
                "description": "Maximum number of file paths to return.",
            },
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
            return ToolResult.ok(
                output={"root": str(root), "files": [str(root)], "count": 1},
                count=1,
            )

        max_depth = int(kwargs.get("max_depth") or _DEFAULT_MAX_DEPTH)
        exclude_dirs = kwargs.get("exclude_dirs") or _DEFAULT_EXCLUDE_DIRS
        if isinstance(exclude_dirs, str):
            exclude_dirs = [exclude_dirs]
        exclude_set = set(exclude_dirs)
        max_results = int(kwargs.get("max_results") or _DEFAULT_MAX_RESULTS)

        matches = _glob_walk(root, pattern, max_depth, exclude_set, max_results)
        sandbox = current_sandbox()
        if sandbox is not None and len(matches) < max_results:
            matches = _union_mirror_matches(
                matches, root, sandbox, pattern, max_depth, max_results
            )

        return ToolResult.ok(
            output={"root": str(root), "files": matches, "count": len(matches)},
            count=len(matches),
        )


def _glob_walk(
    root: Path,
    pattern: str,
    max_depth: int,
    exclude_set: set[str],
    max_results: int,
) -> list[str]:
    """Walk *root* and return relative paths matching *pattern*."""
    results: list[str] = []
    # Normalise pattern — ``**`` means "any depth", everything else
    # is matched against the filename with fnmatch.
    has_doublestar = "**" in pattern
    # Strip leading ``**/`` for matching, we handle depth manually.
    strip_prefix = "**/" if has_doublestar else ""
    match_pattern = pattern[len(strip_prefix):] if strip_prefix else pattern

    for dirpath, dirnames, filenames in os.walk(root):
        # Calculate current depth relative to root.
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            depth = 0
        else:
            depth = rel_dir.count(os.sep) + 1

        # Prune excluded directories (caller table + hard exclusions)
        # and enforce depth limit.
        dirnames[:] = [
            d
            for d in dirnames
            if d not in exclude_set and d not in _HARD_EXCLUDED_DIRS
        ]
        if depth >= max_depth:
            dirnames.clear()

        for name in filenames:
            if _match(name, match_pattern, has_doublestar):
                full = os.path.join(dirpath, name)
                try:
                    rel = str(Path(full).resolve().relative_to(root))
                except ValueError:
                    rel = str(full)
                # Replace backslashes for cross-platform consistency.
                rel = rel.replace("\\", "/")
                results.append(rel)
                if len(results) >= max_results:
                    return results

    return results


def _union_mirror_matches(
    matches: list[str],
    root: Path,
    sandbox: Path,
    pattern: str,
    max_depth: int,
    max_results: int,
) -> list[str]:
    """Append sandbox-only matches to *matches* (the workspace walk results).

    Mirror paths are workspace-relative; only those under *root*
    survive, re-projected relative to *root*, depth-checked against
    *max_depth* and matched with the same filename rule as the walk.
    Same-name entries are already covered by the walk — the union only
    adds what the workspace itself does not have.
    """
    ws_root = current_root() or env_or_cwd_root()
    if ws_root is None:  # pragma: no cover — env_or_cwd_root always yields
        return matches
    try:
        root_prefix = root.relative_to(ws_root).as_posix()
    except ValueError:
        return matches  # root outside the workspace — nothing to union
    if root_prefix == ".":
        root_prefix = ""

    has_doublestar = "**" in pattern
    strip_prefix = "**/" if has_doublestar else ""
    match_pattern = pattern[len(strip_prefix):] if strip_prefix else pattern

    existing = set(matches)
    for rel in sandbox_mirror_files(sandbox):
        if root_prefix:
            if not rel.startswith(root_prefix + "/"):
                continue
            sub = rel[len(root_prefix) + 1:]
        else:
            sub = rel
        if not sub or sub in existing:
            continue
        # Depth gate mirrors the walker: a file is visible when its
        # parent directory's depth stays below max_depth.
        parent = sub.rsplit("/", 1)[0] if "/" in sub else ""
        if (parent.count("/") + 1 if parent else 0) >= max_depth:
            continue
        if not _match(sub.rsplit("/", 1)[-1], match_pattern, has_doublestar):
            continue
        existing.add(sub)
        matches.append(sub)
        if len(matches) >= max_results:
            break
    return matches


def _match(name: str, pattern: str, has_doublestar: bool) -> bool:
    """Check if *name* matches *pattern*.

    When the original pattern contained ``**``, we match against the
    full pattern (including any directory prefix). Otherwise we just
    match the filename.
    """
    if has_doublestar:
        # For **/foo/*.py style patterns, we only match the filename
        # part. The **/ prefix was stripped so pattern is like "*.py"
        # or "foo/*.py". We only check the filename portion.
        basename_pattern = pattern.rsplit("/", 1)[-1] if "/" in pattern else pattern
        return fnmatch.fnmatch(name, basename_pattern)
    return fnmatch.fnmatch(name, pattern)


__all__ = ["GlobFindTool"]
