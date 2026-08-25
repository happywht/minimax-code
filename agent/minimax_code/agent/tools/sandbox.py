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
* **Overlay reads cover read_file *and* edit_file's internal read.**
  Both route through :func:`overlay_read_target`; otherwise an edit's
  re-read would silently resurrect the workspace original and lose the
  first sandboxed edit — the exact clobber class this feature exists to
  kill. ``search``/``glob``/``list_directory`` are *not* overlaid
  (known limitation, spelled out in SANDBOX_PROTOCOL_PROMPT).
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

import logging
import shutil
from pathlib import Path

from ...workspace_ctx import current_root, current_sandbox, env_or_cwd_root

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


def sandbox_files_written(run_id: str) -> list[str]:
    """Workspace-relative posix paths this run's sandbox holds.

    Walks the sandbox tree excluding ``_base/`` and the merge marker —
    i.e. exactly the files the sub-agent wrote or modified. Empty when
    the sandbox directory does not exist (nothing written / pruned).
    """
    root = sandbox_root_for(run_id)
    if root is None or not root.is_dir():
        return []
    written: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] == BASE_DIR:
            continue
        if path.name == MERGED_MARKER:
            continue
        written.append(rel.as_posix())
    return written


__all__ = [
    "BASE_DIR",
    "MERGED_MARKER",
    "SANDBOX_RELPATH",
    "overlay_read_target",
    "redirect_write_target",
    "sandbox_files_written",
    "sandbox_root_for",
]
