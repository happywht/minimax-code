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
import hashlib
import re
from typing import Any

from ...execution import require_write_access
from ...workspace_ctx import current_run_id
from .base import Tool, ToolResult, register_tool
from .file_ops import (
    PathSecurityError,
    claim_write,
    file_sha256,
    in_flight_writer,
    safe_resolve,
)
from .sandbox import overlay_read_target, redirect_write_target


@register_tool
class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Replace an exact substring inside a file. The agent must pass "
        "the old text verbatim; the tool refuses to run if it appears 0 "
        "or >1 times (unless replace_all=True). Returns a textual diff "
        "summary and the line ranges that were modified. Unchanged text "
        "retains its original line endings."
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
            "expected_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
                "description": (
                    "Optional optimistic lock: the sha256 the file had when "
                    "you last read it. On mismatch the edit fails with the "
                    "current hash — re-read the file, then reapply your edit."
                ),
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
        if not old:
            return ToolResult.fail("'old_string' must be non-empty")
        if old == new:
            return ToolResult.fail("'old_string' and 'new_string' are identical — nothing to do")

        try:
            require_write_access()
        except PermissionError as exc:
            return ToolResult.fail(str(exc))

        try:
            target = safe_resolve(path)
        except PathSecurityError as exc:
            return ToolResult.fail(str(exc))

        # v1.5.1 — cross-run write awareness (mirror of write_file's
        # hook): claim the workspace path for this run, surface any rival
        # in-flight run as an advisory warning. Never blocks.
        run_id = current_run_id()
        concurrent_writer = in_flight_writer(target, exclude=run_id)
        if run_id:
            claim_write(target, run_id)

        # v1.5.0 sandbox: edit_file's internal read routes through the
        # overlay too — otherwise the second edit of the same file
        # would resurrect the workspace original and silently lose the
        # first sandboxed edit (the exact clobber class the sandbox
        # exists to prevent). CAS + backup verify the overlay view;
        # the rewrite lands in the sandbox mirror. Without a sandbox
        # both collapse onto ``target``.
        try:
            read_target = overlay_read_target(target)
            write_target = redirect_write_target(target)
        except OSError as exc:
            return ToolResult.fail(str(exc))

        if not read_target.exists():
            return ToolResult.fail(f"file not found: {target}")
        if read_target.is_dir():
            return ToolResult.fail(f"path is a directory: {target}")

        try:
            original_bytes = read_target.read_bytes()
            # Keep the original line endings so unrelated lines survive an edit.
            original = original_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult.fail("file is not valid UTF-8")
        except OSError as exc:
            return ToolResult.fail(f"read failed: {exc}")

        # Compare the caller's fingerprint with the exact bytes decoded above.
        # The atomic writer rechecks that baseline before replacing the file.
        previous_sha = hashlib.sha256(original_bytes).hexdigest()
        expected = kwargs.get("expected_sha256")
        if expected is not None:
            if not isinstance(expected, str) or len(expected) != 64:
                return ToolResult.fail("'expected_sha256' must be a 64-char hex string")
            if previous_sha != expected.strip().lower():
                return ToolResult.fail(
                    "edit_file blocked: file changed since your read (sha256 "
                    "mismatch) — re-read the file and reapply your edit",
                    output={
                        "path": str(target),
                        "expected_sha256": expected,
                        "current_sha256": previous_sha,
                    },
                )

        # Pre-edit backup (best-effort, never blocks).
        backup_meta = None
        try:
            from ..backup import BackupManager
            backup_meta = BackupManager().backup(read_target)
        except Exception:
            pass

        # Match line endings as logical newlines, but retain the original
        # spans when writing. This also works with mixed newline files.
        normalized_old = _normalize_newlines(old)
        pattern = re.compile(r"(?:\r\n|\r|\n)".join(
            re.escape(part) for part in normalized_old.split("\n")
        ))
        matches = list(pattern.finditer(original))
        occurrences = len(matches)
        if occurrences == 0:
            hint = _closest_hint(_normalize_newlines(original), normalized_old)
            return ToolResult.fail(
                f"'old_string' not found in {target.name}" + (f" — {hint}" if hint else "")
            )
        if occurrences > 1 and not replace_all:
            return ToolResult.fail(
                f"'old_string' matches {occurrences} locations; "
                "narrow it or pass replace_all=True"
            )

        chosen = matches if replace_all else matches[:1]
        pieces: list[str] = []
        cursor = 0
        for match in chosen:
            pieces.append(original[cursor:match.start()])
            newline = _nearby_newline(original, match.start(), match.end())
            pieces.append(_normalize_newlines(new).replace("\n", newline))
            cursor = match.end()
        pieces.append(original[cursor:])
        updated = "".join(pieces)

        try:
            from ...file_mutations import atomic_write_text

            atomic_write_text(write_target, updated, baseline_path=read_target, baseline_sha256=previous_sha)
        except OSError as exc:
            return ToolResult.fail(f"write failed: {exc}")

        # Hash the bytes that actually landed.
        new_sha = file_sha256(write_target)

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
                    [str(write_target)],
                    "edit_file",
                    lines_added=added,
                    lines_removed=removed,
                    # v1.5.1 — attribute the edit to its owning run.
                    **({"run_id": run_id} if run_id else {}),
                )
        except Exception:  # noqa: BLE001 — file edit must never break on the bus
            pass

        output: dict[str, Any] = {
            "path": str(target),
            "replacements": occurrences if replace_all else 1,
            "changed_lines": changed_lines,
            "lines_removed": removed,
            "lines_added": added,
            "diff": diff,
            "previous_sha256": previous_sha,
            "sha256": new_sha,
            **({"backup": backup_meta} if backup_meta else {}),
        }
        if concurrent_writer:
            output["concurrent_writer"] = concurrent_writer
            output["warning"] = (
                f"⚠ in-flight run {concurrent_writer} has also claimed this "
                "file — coordinate with it, or guard your edit with "
                "expected_sha256 after a fresh read"
            )
        if write_target != target:
            output["sandboxed"] = True
            output["sandbox_path"] = str(write_target)

        return ToolResult.ok(
            output=output,
            replacements=occurrences if replace_all else 1,
            lines_removed=removed,
            lines_added=added,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _nearby_newline(text: str, start: int, end: int) -> str:
    """Prefer the replaced span, then the closest surrounding line ending."""
    line_endings = re.compile(r"\r\n|\r|\n")
    within = line_endings.search(text, start, end)
    if within:
        return within.group()
    before = None
    for before in line_endings.finditer(text, 0, start):
        pass
    after = line_endings.search(text, end)
    if before and (not after or start - before.end() <= after.start() - end):
        return before.group()
    return after.group() if after else "\n"


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
