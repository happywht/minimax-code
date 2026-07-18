"""Precise file edit tool — replaces an exact substring.

Designed to dodge the LLM's failure mode of "regenerate the whole
file with subtle drift". The agent is required to pass a verbatim
``old_string`` that is unique inside the file (unless
``replace_all=True``). On a successful edit the tool returns a
diff-like summary so the model can describe what it changed to
the user.
"""

from __future__ import annotations

import difflib
from typing import Any

from .base import Tool, ToolResult, register_tool
from .file_ops import PathSecurityError, safe_resolve


@register_tool
class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Replace an exact substring inside a file. The agent must pass "
        "the old text verbatim; the tool refuses to run if it appears 0 "
        "or >1 times (unless replace_all=True). Returns a textual diff "
        "summary and the line ranges that were modified."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Target file path."},
            "old_string": {"type": "string", "description": "Verbatim text to find. Must be unique unless replace_all=True."},
            "new_string": {"type": "string", "description": "Replacement text."},
            "replace_all": {
                "type": "boolean",
                "default": False,
                "description": "If True, replace every non-overlapping occurrence.",
            },
        },
        "required": ["path", "old_string", "new_string"],
        "additionalProperties": False,
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        path = kwargs.get("path")
        old = kwargs.get("old_string")
        new = kwargs.get("new_string")
        replace_all = bool(kwargs.get("replace_all", False))

        if not isinstance(path, str) or not path:
            return ToolResult.fail("'path' must be a non-empty string")
        if not isinstance(old, str):
            return ToolResult.fail("'old_string' must be a string")
        if not isinstance(new, str):
            return ToolResult.fail("'new_string' must be a string")
        if old == new:
            return ToolResult.fail("'old_string' and 'new_string' are identical — nothing to do")

        try:
            target = safe_resolve(path)
        except PathSecurityError as exc:
            return ToolResult.fail(str(exc))

        if not target.exists():
            return ToolResult.fail(f"file not found: {target}")
        if target.is_dir():
            return ToolResult.fail(f"path is a directory: {target}")

        try:
            original = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult.fail("file is not valid UTF-8")
        except OSError as exc:
            return ToolResult.fail(f"read failed: {exc}")

        # Pre-edit backup (best-effort, never blocks).
        backup_meta = None
        try:
            from ..backup import BackupManager
            backup_meta = BackupManager().backup(target)
        except Exception:
            pass

        occurrences = original.count(old)
        if occurrences == 0:
            # Provide a helpful hint with the closest matching block.
            hint = _closest_hint(original, old)
            return ToolResult.fail(
                f"'old_string' not found in {target.name}" + (f" — {hint}" if hint else "")
            )
        if occurrences > 1 and not replace_all:
            return ToolResult.fail(
                f"'old_string' matches {occurrences} locations; "
                "narrow it or pass replace_all=True"
            )

        if replace_all:
            updated = original.replace(old, new)
        else:
            updated = original.replace(old, new, 1)

        try:
            target.write_text(updated, encoding="utf-8")
        except OSError as exc:
            return ToolResult.fail(f"write failed: {exc}")

        changed_lines, removed, added = _line_delta(original, updated)
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=str(target),
                tofile=str(target),
                n=2,
            )
        )

        # R16 — mirror the successful edit onto the causal file-change bus.
        # Fail-open: a bus failure must never break the tool that just edited.
        try:
            from ...app import ensure_fs_bus

            bus = ensure_fs_bus()
            if bus is not None:
                bus.emit(
                    "modified",
                    [str(target)],
                    "edit_file",
                    lines_added=added,
                    lines_removed=removed,
                )
        except Exception:  # noqa: BLE001 — file edit must never break on the bus
            pass

        return ToolResult.ok(
            output={
                "path": str(target),
                "replacements": occurrences if replace_all else 1,
                "changed_lines": changed_lines,
                "lines_removed": removed,
                "lines_added": added,
                "diff": diff,
                **({"backup": backup_meta} if backup_meta else {}),
            },
            replacements=occurrences if replace_all else 1,
            lines_removed=removed,
            lines_added=added,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _line_delta(old: str, new: str) -> tuple[list[int], int, int]:
    """Return the indices of lines that changed plus add/remove counts.

    Cheap heuristic: compute the set of line hashes on each side
    and return the indices (1-indexed) that differ. We do not try
    to detect *moved* blocks.
    """
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    old_set = set(enumerate(old_lines))
    new_set = set(enumerate(new_lines))
    changed: set[int] = set()
    for i, line in enumerate(old_lines):
        if (i, line) not in new_set:
            changed.add(i + 1)
    for i, line in enumerate(new_lines):
        if (i, line) not in old_set:
            changed.add(i + 1)
    return sorted(changed), len(old_lines) - len(new_lines), len(new_lines) - len(old_lines)


def _closest_hint(text: str, target: str, max_lines: int = 2) -> str:
    """Return a 'did you mean…' hint using difflib's get_close_matches."""
    if not target.strip():
        return ""
    block = target[:120].splitlines()[0] if target else ""
    if not block:
        return ""
    candidates = [line[:120] for line in text.splitlines() if line.strip()]
    matches = difflib.get_close_matches(block, candidates, n=1, cutoff=0.4)
    if not matches:
        return ""
    return f"closest match: {matches[0]!r}"


__all__ = ["EditFileTool"]
