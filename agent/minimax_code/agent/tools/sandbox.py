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
* **``.minimax/`` passes through untouched.** Artifacts, backups and
  sandbox internals must never be re-redirected (nested-sandbox and
  ``_base`` self-collision hazards). ``report_completion`` already
  bypasses ``write_file`` and is unaffected either way.

Import graph: this module must not import sibling tool modules (tools
import each other's helpers and ``file_ops`` imports nothing from here
at module level it doesn't already own) — collect-side hashing does a
lazy ``file_sha256`` import instead.

Failure philosophy: unlike the fail-*open* artifacts/fs_bus family, the
*lookup* helpers here never raise — a missing root or an unreadable
original degrades to "no overlay / no baseline", and the spawn-side
guard (``subagents``) is what fails closed when the sandbox directory
cannot be created: a silent fallback to the shared workspace would
void the very contract the caller opted into.
"""

from __future__ import annotations

import json
import logging
import shutil
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
MERGED_MARKER = ".merged"


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
    return root / SANDBOX_RELPATH / run_id


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
    if rel.parts and rel.parts[0] == ".minimax":
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

    Copy-on-write: the first time a write touches a path that exists in
    the workspace, the original is snapshotted into ``<sandbox>/_base/``
    as the merge baseline. Best-effort — a failed copy means the merge
    will treat the file as newly created (still safe, just a coarser
    diff). Never raises: a redirect failure here must not turn into a
    tool crash; the spawn-side guard already failed closed on the
    directory we could not even create.
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
        mirror.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.is_file():
            base = sandbox / BASE_DIR
            baseline = base.joinpath(*target.relative_to(root).parts)
            if not baseline.exists():
                baseline.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, baseline)
    except OSError as exc:
        # Degrade to "no baseline" rather than refusing the write.
        logger.debug("sandbox COW baseline failed for %s: %s", target, exc)
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


__all__ = [
    "BASE_DIR",
    "CollectSubagentTool",
    "MERGED_MARKER",
    "SANDBOX_RELPATH",
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
    ``skip`` keeps the workspace version; ``overwrite`` applies the
    sandbox version. A successful collect writes the ``.merged`` marker
    making subsequent collects no-ops.
    """

    name = "collect_subagent"
    description = (
        "Merge a finished sandboxed sub-agent run's sandbox writes back "
        "into the shared workspace. Call after wait_subagent/check_subagent "
        "reports completion. Reports per-file merges, no-ops and conflicts "
        "with full sha256 fingerprints; on_conflict picks the policy for "
        "workspace-vs-sandbox divergence (fail=stop and report, "
        "skip=keep workspace, overwrite=apply sandbox)."
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
                "enum": ["fail", "skip", "overwrite"],
                "default": "fail",
                "description": (
                    "Policy when the workspace copy changed independently "
                    "since the sandbox snapshot: 'fail' refuses the collect "
                    "and reports all three hashes; 'skip' keeps the workspace "
                    "version; 'overwrite' applies the sandbox version."
                ),
            },
        },
        "required": ["run_id"],
        "additionalProperties": False,
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        run_id = kwargs.get("run_id")
        on_conflict = kwargs.get("on_conflict", "fail") or "fail"
        if not isinstance(run_id, str) or not run_id.strip():
            return ToolResult.fail("'run_id' must be a non-empty string")
        run_id = run_id.strip()
        if on_conflict not in ("fail", "skip", "overwrite"):
            return ToolResult.fail(
                "'on_conflict' must be one of: fail, skip, overwrite"
            )

        # In-flight guard: merging under a live run races its own writes.
        from ...orchestrator.subagent import get_background_run

        if get_background_run(run_id) is not None:
            return ToolResult.fail(
                f"run {run_id!r} is still in flight — wait_subagent first, "
                "then collect"
            )

        sb_dir = sandbox_root_for(run_id)
        if sb_dir is None or not sb_dir.is_dir():
            return ToolResult.fail(
                f"no sandbox found for run {run_id!r} (the run never opted "
                "into sandbox=true, or the sandbox tree was pruned)"
            )

        # Idempotence: a second collect returns the first run's report.
        marker = sb_dir / MERGED_MARKER
        if marker.exists():
            prior: dict[str, Any] = {}
            try:
                prior = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            return ToolResult.ok({**prior, "already_collected": True})

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
                ws_file = root.joinpath(*rel.split("/"))

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
                    else:  # overwrite — resolve by applying the sandbox bytes
                        ws_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(sb_file, ws_file)
                        merged.append(
                            {"path": rel, "conflict_resolved": "overwrite"}
                        )
                        emits["modified"].append(str(ws_file))
                    continue

                # Clean merge: workspace still at the baseline (or absent).
                ws_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sb_file, ws_file)
                merged.append({"path": rel})
                emits["created" if not ws_existed else "modified"].append(
                    str(ws_file)
                )
            except OSError as exc:
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
            # 'fail' policy: refuse without the marker so a manual
            # resolution + re-run stays idempotent (already-merged
            # files come back as no-ops).
            return ToolResult.fail(
                f"collect_subagent: {len(conflicts)} conflict(s) between the "
                "workspace and the sandbox — resolve manually or re-run with "
                "on_conflict=skip/overwrite; no marker was written",
                output=report,
            )

        try:
            marker.write_text(
                json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            logger.debug("collect marker write failed for %s", run_id, exc_info=True)

        return ToolResult.ok(report)
