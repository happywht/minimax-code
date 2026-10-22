"""Per-run sub-agent write sandbox (v1.5.0 layer 2).

Concurrent field test: parallel sub-agents sharing one workspace root
produced last-write-wins clobbers with no isolation layer. When a spawn
opts in (``spawn_subagent(sandbox=true)``), every write the sub-agent
makes through ``write_file`` / ``edit_file`` is transparently redirected
into ``<root>/.minimax/sandboxes/<run_id>/`` — the shared workspace
stays untouched until the main agent merges via ``collect_subagent``.

Mechanics (three rulings from the design review):

* **Transparent COW redirect over hard refusal.** A sandboxed sub-agent
  keeps using ordinary workspace paths; the redirection happens below
  the tool layer. First touch of an existing workspace file copies the
  original into ``<sandbox>/_base/<rel>`` — that snapshot is the merge
  baseline ``collect_subagent`` diffs against (O(touched files), unlike
  a whole-repo manifest).
* **Overlay reads cover every read-side view.** ``read_file`` and
  ``edit_file``'s internal read both route through
  :func:`overlay_read_target`; since v1.6.0 ``search_files`` /
  ``find_files`` / ``list_directory`` union the sandbox mirrors into
  their workspace views too (:func:`sandbox_mirror_files` /
  :func:`mirror_children`), so a sandboxed agent sees one coherent
  world instead of a split view of its own writes.
* **Managed metadata is not writable through child file tools.**
  ``.minimax/`` and reserved overlay paths are rejected on writes.
  Artifact helpers retain their own scoped paths.

Import graph: this module must not import sibling tool modules (tools
import each other's helpers and ``file_ops`` imports nothing from here
at module level it doesn't already own) — collect-side hashing does a
lazy ``file_sha256`` import instead.

Writes fail closed if the overlay or its baseline cannot be verified.
Read lookups retain the workspace view for paths without an overlay.

"""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat as stat_mod
import sys
import time
from pathlib import Path
from typing import Any

from ...workspace_ctx import current_root, current_sandbox, env_or_cwd_root
from .base import Tool, ToolResult, register_tool

logger = logging.getLogger(__name__)

#: Sandbox tree lives under the workspace root, beside ``backups/`` and
#: ``artifacts/``. ``.minimax`` is already excluded from glob/perception
#: and codebase indexing, so sandbox mirrors never pollute search views.
SANDBOX_RELPATH = ".minimax/sandboxes"

#: COW snapshots of workspace originals — the merge baseline.
BASE_DIR = "_base"

#: Written by ``collect_subagent`` after a successful merge; makes a
#: second collect a no-op instead of re-merging (idempotence marker).
#: v1.5.x kept this inside the sandbox directory (``sb_dir/.merged``);
#: v1.6.0 moved markers to a flat sibling directory so pruning a
#: sandbox never destroys its receipt. The legacy location is still
#: *read* for pre-upgrade sandboxes (see ``_read_collected_report``).
MERGED_MARKER = ".merged"

#: v1.6.0 home for per-run collect receipts:
#: ``<root>/.minimax/sandboxes/.collected/<run_id>.json``. Sibling of
#: the per-run sandbox trees, never scanned by ``sandbox_mirror_files``
#: (which anchors at a run directory), and ``run_<hex12>`` ids cannot
#: collide with the dotted directory name.
COLLECTED_DIR = ".collected"


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------


def sandbox_root_for(run_id: str) -> Path | None:
    """The sandbox directory for *run_id*: ``<root>/.minimax/sandboxes/<id>``.

    Returns ``None`` when no workspace root can be resolved (no session
    root, no env, no CWD — practically unreachable; callers treat it as
    "sandboxing unavailable").
    """
    root = current_root() or env_or_cwd_root()
    if root is None:  # pragma: no cover — env_or_cwd_root always yields
        return None
    from ...managed_paths import run_data_path

    return run_data_path(root, "sandboxes", run_id)


def _workspace_root() -> Path | None:
    """The root mirror paths are computed against (session root or env)."""
    return current_root() or env_or_cwd_root()


def _mirror_path(target: Path, sandbox: Path, root: Path) -> Path | None:
    """``target``'s address inside the sandbox tree, or ``None``.

    ``None`` means "not redirectable": the target escapes the workspace
    root (defensive — ``safe_resolve`` containment should have caught
    that) or lives under ``.minimax/`` (pass-through contract).
    """
    try:
        rel = target.relative_to(root)
    except ValueError:
        return None
    if rel.parts and rel.parts[0].casefold() == ".minimax":
        return None
    return sandbox.joinpath(*rel.parts)


# ---------------------------------------------------------------------------
# Read overlay / write redirect
# ---------------------------------------------------------------------------


def overlay_read_target(target: Path) -> Path:
    """Where a read should actually look: the sandbox mirror if present.

    Pure lookup — no copy-on-write side effects. Falls back to the
    workspace original for every path without a mirror (including all
    ``.minimax/`` targets), so a sandboxed agent reads a coherent view:
    its own writes plus everything it never touched.
    """
    sandbox = current_sandbox()
    if sandbox is None:
        return target
    root = _workspace_root()
    if root is None:
        return target
    mirror = _mirror_path(target, sandbox, root)
    if mirror is None:
        return target
    try:
        if mirror.exists():
            return mirror
    except OSError:  # pragma: no cover — unreadable mirror ≈ absent
        pass
    return target


def redirect_write_target(target: Path) -> Path:
    """Where a write should land: always the sandbox mirror.

    Copy-on-write snapshots must succeed before a write is allowed.
    Managed metadata cannot be modified through ordinary child file tools.
    """
    sandbox = current_sandbox()
    if sandbox is None:
        return target
    root = _workspace_root()
    if root is None:
        raise PermissionError("Cannot verify the overlay workspace")
    mirror = _mirror_path(target, sandbox, root)
    if mirror is None:
        raise PermissionError("Overlay file tools cannot modify managed metadata or external paths")
    rel = target.relative_to(root)
    if rel.parts[0].casefold() in (BASE_DIR, MERGED_MARKER):
        raise PermissionError("Path collides with reserved overlay metadata")
    if mirror.resolve() != mirror or not sandbox.is_relative_to(root):
        raise PermissionError("Overlay path changed to a symbolic link")
    mirror.parent.mkdir(parents=True, exist_ok=True)
    if not mirror.exists() and target.exists() and target.is_file():
        baseline = (sandbox / BASE_DIR).joinpath(*rel.parts)
        if baseline.resolve() != baseline:
            raise PermissionError("Overlay baseline changed to a symbolic link")
        if not baseline.exists():
            from ...file_mutations import atomic_copy_file

            baseline.parent.mkdir(parents=True, exist_ok=True)
            atomic_copy_file(target, baseline, baseline_sha256=None)
    return mirror


def sandbox_mirror_files(sandbox: Path) -> list[str]:
    """Workspace-relative posix paths the sandbox tree holds.

    Same walk :func:`sandbox_files_written` reports, anchored at an
    explicit sandbox directory — the shared helper behind the v1.6.0
    overlay views (``search_files`` / ``find_files`` /
    ``list_directory``), which union these mirrors into the workspace
    view so a sandboxed sub-agent can see its own writes. ``_base/``
    snapshots and the merge marker are excluded; empty when the
    directory does not exist.
    """
    if not sandbox.is_dir():
        return []
    written: list[str] = []
    for path in sorted(sandbox.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(sandbox)
        if rel.parts and rel.parts[0] == BASE_DIR:
            continue
        if path.name == MERGED_MARKER:
            continue
        written.append(rel.as_posix())
    return written


def mirror_children(target: Path) -> Path | None:
    """The sandbox mirror of workspace directory *target*, if populated.

    ``list_directory``'s overlay hook. Returns the mirror directory even
    when *target* itself does not exist in the workspace (sandbox-only
    directories must stay visible), or ``None`` when this run is not
    sandboxed / the target is not redirectable / the mirror is absent.
    Callers must skip ``_base`` and the merge marker when iterating.
    """
    sandbox = current_sandbox()
    if sandbox is None:
        return None
    root = _workspace_root()
    if root is None:
        return None
    mirror = _mirror_path(target, sandbox, root)
    if mirror is None:
        return None
    try:
        if mirror.is_dir():
            return mirror
    except OSError:  # pragma: no cover — unreadable mirror ≈ absent
        pass
    return None


def sandbox_files_written(run_id: str) -> list[str]:
    """Workspace-relative posix paths this run's sandbox holds.

    Walks the sandbox tree excluding ``_base/`` and the merge marker —
    i.e. exactly the files the sub-agent wrote or modified. Empty when
    the sandbox directory does not exist (nothing written / pruned).
    """
    root = sandbox_root_for(run_id)
    if root is None:
        return []
    return sandbox_mirror_files(root)


def collected_marker_path(run_id: str) -> Path | None:
    """The v1.6.0 collect receipt for *run_id* (flat sibling directory).

    Returns ``None`` only when no workspace root can be resolved.
    """
    root = _workspace_root()
    if root is None:  # pragma: no cover — env_or_cwd_root always yields
        return None
    from ...managed_paths import run_data_path

    return run_data_path(root, "sandboxes", run_id, receipt=True)


def _read_collected_report(run_id: str) -> dict[str, Any] | None:
    """The prior collect report when *run_id* was already collected.

    Checks the v1.6.0 flat receipt first, then the legacy in-sandbox
    ``.merged`` marker (pre-upgrade sandboxes). An unreadable marker
    still counts as collected — an empty report is returned so the
    re-collect can never re-merge over resolved state.
    """
    new_marker = collected_marker_path(run_id)
    if new_marker is not None:
        try:
            if new_marker.exists():
                return json.loads(new_marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"errors": [{"error": "collect receipt is unreadable; manual review required"}]}
    legacy = sandbox_root_for(run_id)
    if legacy is not None:
        legacy_marker = legacy / MERGED_MARKER
        try:
            if legacy_marker.exists():
                return json.loads(legacy_marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"errors": [{"error": "collect receipt is unreadable; manual review required"}]}
    return None


def _chmod_retry(func, path, _exc) -> None:  # noqa: ANN001 — shutil callback
    """rmtree callback: clear the read-only bit and retry the step once."""
    os.chmod(path, stat_mod.S_IWRITE)
    func(path)


def _prune_sandbox(run_id: str) -> tuple[bool, str | None]:
    """Delete the run's sandbox tree (best-effort, read-only aware).

    Returns ``(pruned, error)`` — a missing directory counts as pruned.
    Never raises: pruning failures surface as ``prune_error`` on the
    collect report instead of failing an otherwise successful merge.
    """
    sb_dir = sandbox_root_for(run_id)
    if sb_dir is None or not sb_dir.is_dir():
        return True, None
    try:
        # onexc is 3.12+; onerror is deprecated but identical for our
        # single-callback use on 3.11.
        if sys.version_info >= (3, 12):
            shutil.rmtree(sb_dir, onexc=_chmod_retry)
        else:  # pragma: no cover — dev/CI run 3.12+
            shutil.rmtree(sb_dir, onerror=_chmod_retry)
        return True, None
    except OSError as exc:
        logger.debug("sandbox prune failed for %s: %s", run_id, exc)
        return False, str(exc)


__all__ = [
    "BASE_DIR",
    "COLLECTED_DIR",
    "CollectSubagentTool",
    "MERGED_MARKER",
    "SANDBOX_RELPATH",
    "collected_marker_path",
    "mirror_children",
    "overlay_read_target",
    "redirect_write_target",
    "sandbox_files_written",
    "sandbox_mirror_files",
    "sandbox_root_for",
]


# ---------------------------------------------------------------------------
# collect_subagent — main-agent-side merge (v1.5.0 layer 3)
# ---------------------------------------------------------------------------


def _emit_collect(emits: dict[str, list[str]]) -> None:
    """Mirror merged files onto the causal file-change bus (fail-open)."""
    try:
        from ...app import ensure_fs_bus

        bus = ensure_fs_bus()
        if bus is None:
            return
        for kind, paths in emits.items():
            if paths:
                bus.emit(kind, paths, "collect_subagent")
    except Exception:  # noqa: BLE001 — collect must never break on the bus
        logger.debug("collect_subagent fs_bus emit failed", exc_info=True)


@register_tool
class CollectSubagentTool(Tool):
    """Merge a finished sandboxed run's writes back into the workspace.

    Three-way compare per file — the ``_base/`` COW snapshot (what the
    workspace looked like when the run first touched the file), the
    workspace's current bytes, and the sandbox mirror's bytes, all
    hashed at the byte level so binaries merge as first-class citizens:

    * workspace unchanged since the snapshot → apply the sandbox version
    * workspace already equals the sandbox version → no-op
    * workspace changed independently → **conflict**, surfaced with all
      three hashes rather than silently clobbered

    ``on_conflict`` decides what happens to conflicts: ``fail`` refuses
    the whole collect (no marker written — resolve manually and re-run,
    already-merged files degrade to no-ops so the re-run is idempotent);
    ``skip`` keeps the workspace version and leaves collection incomplete.
    Automatic conflict overwrite is disabled. A successful collect writes its receipt under
    ``.minimax/sandboxes/.collected/<run_id>.json`` making subsequent
    collects no-ops — and, since v1.6.0, prunes the sandbox tree itself
    (disable with ``prune=false``). Pruning is conservative: conflicts,
    per-file errors, skipped files or an unwritable receipt all keep the
    sandbox on disk so the merge can be resolved and re-run.
    """

    name = "collect_subagent"
    description = (
        "Merge a finished sandboxed sub-agent run's sandbox writes back "
        "into the shared workspace. Call after wait_subagent/check_subagent "
        "reports completion. Reports per-file merges, no-ops and conflicts "
        "with full sha256 fingerprints; on_conflict picks the policy for "
        "workspace-vs-sandbox divergence (fail=stop and report, "
        "skip=keep workspace and leave collection incomplete). On success prunes "
        "the sandbox tree (prune=false keeps it) after recording the "
        "collect receipt."
    )
    parameters = {
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "description": "The sandboxed run to collect (from spawn_subagent).",
            },
            "on_conflict": {
                "type": "string",
                "enum": ["fail", "skip"],
                "default": "fail",
                "description": (
                    "Policy when the workspace copy changed independently "
                    "since the sandbox snapshot: 'fail' refuses the collect "
                    "and reports all three hashes; 'skip' keeps the workspace "
                    "version without marking collection complete."
                ),
            },
            "prune": {
                "type": "boolean",
                "default": True,
                "description": (
                    "Delete the sandbox tree after a fully successful "
                    "collect. The receipt under "
                    ".minimax/sandboxes/.collected/ is always kept, so "
                    "already-collected detection survives pruning. Kept "
                    "automatically on conflicts, errors or skipped files."
                ),
            },
        },
        "required": ["run_id"],
        "additionalProperties": False,
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        from ...execution import require_write_access

        try:
            require_write_access()
        except PermissionError as exc:
            return ToolResult.fail(str(exc))
        if current_sandbox() is not None:
            return ToolResult.fail("Only the parent workspace may collect child changes")
        run_id = kwargs.get("run_id")
        on_conflict = kwargs.get("on_conflict", "fail") or "fail"
        prune = bool(kwargs.get("prune", True))
        if not isinstance(run_id, str) or not run_id.strip():
            return ToolResult.fail("'run_id' must be a non-empty string")
        run_id = run_id.strip()
        if on_conflict not in ("fail", "skip"):
            return ToolResult.fail(
                "'on_conflict' must be fail or skip; automatic conflict overwrite is disabled"
            )

        # In-flight guard: merging under a live run races its own writes.
        from ...orchestrator.subagent import get_background_run

        if get_background_run(run_id) is not None:
            return ToolResult.fail(
                f"run {run_id!r} is still in flight — wait_subagent first, "
                "then collect"
            )

        # Idempotence first (new receipt home, then the legacy in-sandbox
        # marker): a second collect replays the first run's report. A
        # legacy marker lives inside the tree a prune would delete, so it
        # is migrated to the flat home first — and a failed migration
        # cancels the prune rather than destroying the only receipt.
        prior = _read_collected_report(run_id)
        if prior is not None:
            if (not isinstance(prior, dict) or not isinstance(prior.get("merged"), list)
                    or any(prior.get(key) for key in ("errors", "skipped", "conflicts"))):
                return ToolResult.fail("Previous collect was incomplete; preserve the workspace for manual review", output=prior)
            replay: dict[str, Any] = {**prior, "already_collected": True}
            can_prune = prune
            new_marker = collected_marker_path(run_id)
            if prune and new_marker is not None and not new_marker.exists():
                # Migration exists only to make the prune safe; with
                # prune=False the legacy layout stays exactly as found.
                try:
                    new_marker.parent.mkdir(parents=True, exist_ok=True)
                    new_marker.write_text(
                        json.dumps(prior, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except OSError:
                    logger.debug(
                        "receipt migration failed for %s", run_id, exc_info=True
                    )
                    can_prune = False
            if can_prune:
                pruned, prune_error = _prune_sandbox(run_id)
                replay["pruned"] = pruned
                if prune_error:
                    replay["prune_error"] = prune_error
            return ToolResult.ok(replay)

        sb_dir = sandbox_root_for(run_id)
        if sb_dir is None or not sb_dir.is_dir():
            return ToolResult.fail(
                f"no sandbox found for run {run_id!r} (the run never opted "
                "into sandbox=true, or the sandbox tree was pruned)"
            )

        from .file_ops import file_sha256

        root = _workspace_root()
        assert root is not None  # sandbox_root_for resolved, so root exists

        merged: list[dict[str, Any]] = []
        noop: list[str] = []
        conflicts: list[dict[str, Any]] = []
        skipped: list[str] = []
        errors: list[dict[str, Any]] = []
        emits: dict[str, list[str]] = {"created": [], "modified": []}

        for rel in sandbox_files_written(run_id):
            try:
                sb_file = sb_dir.joinpath(*rel.split("/"))
                base_file = (sb_dir / BASE_DIR).joinpath(*rel.split("/"))
                from ...file_mutations import atomic_copy_file
                from .file_ops import safe_resolve

                ws_file = safe_resolve(rel, workspace=root)
                if sb_file.resolve() != sb_file:
                    raise OSError("Sandbox source changed to a symbolic link")

                sb_sha = file_sha256(sb_file)
                if sb_sha is None:
                    errors.append(
                        {"path": rel, "error": "sandbox copy is unreadable"}
                    )
                    continue
                has_base = base_file.exists()
                base_sha = file_sha256(base_file) if has_base else None
                ws_existed = ws_file.exists()
                cur_sha = file_sha256(ws_file) if ws_existed else None

                # Classify: conflict, no-op, or merge.
                conflict_reason = ""
                if has_base:
                    if cur_sha is None:
                        conflict_reason = "workspace copy was deleted after the sandbox snapshot"
                    elif cur_sha != base_sha and cur_sha != sb_sha:
                        conflict_reason = "workspace copy changed since the sandbox snapshot"
                elif cur_sha is not None and cur_sha != sb_sha:
                    conflict_reason = "file was created independently in the workspace"

                if not conflict_reason and cur_sha == sb_sha:
                    noop.append(rel)
                    continue

                if conflict_reason:
                    entry = {
                        "path": rel,
                        "reason": conflict_reason,
                        "sandbox_sha256": sb_sha,
                    }
                    if base_sha is not None:
                        entry["base_sha256"] = base_sha
                    if cur_sha is not None:
                        entry["current_sha256"] = cur_sha
                    if on_conflict == "fail":
                        conflicts.append(entry)
                    elif on_conflict == "skip":
                        skipped.append(rel)
                    continue

                # Clean merge: workspace still at the baseline (or absent).
                ws_file.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy_file(sb_file, ws_file, baseline_sha256=cur_sha)
                merged.append({"path": rel})
                emits["created" if not ws_existed else "modified"].append(
                    str(ws_file)
                )
            except (OSError, ValueError) as exc:
                errors.append({"path": rel, "error": str(exc)})
                continue  # the walk must survive any single bad file

        _emit_collect(emits)

        report: dict[str, Any] = {
            "run_id": run_id,
            "sandbox_dir": str(sb_dir),
            "strategy": on_conflict,
            "merged": merged,
            "noop": noop,
            "conflicts": conflicts,
            "skipped": skipped,
            "errors": errors,
            "collected_at": time.time(),
        }

        if conflicts:
            # 'fail' policy: refuse without the receipt so a manual
            # resolution + re-run stays idempotent (already-merged
            # files come back as no-ops).
            return ToolResult.fail(
                f"collect_subagent: {len(conflicts)} conflict(s) between the "
                "workspace and the sandbox — resolve manually or re-run with "
                "on_conflict=skip; no receipt was written",
                output=report,
            )

        if errors:
            return ToolResult.fail("Some files could not be collected; no completion receipt was written", output=report)
        if skipped:
            report["complete"] = False
            return ToolResult.ok(report)

        # Receipt first, prune second — deleting the tree without a
        # durable receipt would destroy the idempotence evidence and
        # wedge future collects into "no sandbox found".
        marker_written = False
        new_marker = collected_marker_path(run_id)
        if new_marker is not None:
            try:
                new_marker.parent.mkdir(parents=True, exist_ok=True)
                new_marker.write_text(
                    json.dumps(report, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                marker_written = True
            except OSError:
                logger.debug(
                    "collect receipt write failed for %s", run_id, exc_info=True
                )

        # Conservative prune gate: the receipt must be durable and the
        # merge must have fully succeeded — errors or skipped files mean
        # the sandbox still holds the only copy of something.
        if prune and marker_written and not errors and not skipped:
            pruned, prune_error = _prune_sandbox(run_id)
            report["pruned"] = pruned
            if prune_error:
                report["prune_error"] = prune_error

        return ToolResult.ok(report)
