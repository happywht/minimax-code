"""Self-evolution trajectory collector — the ``run_once`` core (R309, Layer 2).

This module is the read-only, LLM-free half of the self-evolution layer
described in ``SELF.md`` §4. It gathers a **trajectory snapshot** — git
history, working-tree state, lint output, and the test-collection result —
and renders it as a durable Markdown report under
``progress/self-evolution-reports/<date>.md``.

Design principles (per the package docstring + audit #001 §4)
-------------------------------------------------------------

* **Read-only.** Never ``git commit`` / ``git push``. The report is
  evidence; committing it is a human's choice.
* **No LLM in the default flow.** The trajectory is *facts* — raw
  subprocess stdout — not interpretations. Summarisation is a later,
  optional layer.
* **Non-blocking.** Every subprocess runs via
  :func:`asyncio.create_subprocess_exec` so the collector stays compatible
  with the ``scheduler.PayloadFn`` signature (single ``payload`` arg,
  awaited off-loop by ``_run_payload_sync``).
* **Idempotent.** Re-running the same day overwrites that day's report
  in-place; past days are never touched.
* **Fault-tolerant.** Each collection step returns its own result; one
  failing step (e.g. ``ruff`` not installed) is recorded, not raised, so
  a partial trajectory still lands on disk.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Record/field separators for ``git log`` parsing. These ASCII control
# characters never appear in real commit metadata, so they make
# unambiguous delimiters without escaping — simpler than the
# ``__COMMIT__…__END__`` marker scheme the IPC git handlers use, because
# this collector only needs flat per-commit fields, not porcelain-v2
# entry shapes.
_FIELD_SEP = "\x1f"  # unit separator — splits fields within one commit
_RECORD_SEP = "\x1e"  # record separator — splits commits

# Per-subprocess cap. Generous (a cold ``ruff check .`` over this repo is
# ~3 s; ``pytest --co -q`` is ~5 s) but bounded so a wedged tool never
# stalls the scheduler fire. ``git`` calls share the same cap.
_DEFAULT_TIMEOUT_S = 60.0


# ---------------------------------------------------------------------------
# Result value types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CommitEntry:
    """One ``git log`` entry — sha / author / date / subject."""

    sha: str
    author: str
    date: str
    message: str


@dataclass(slots=True)
class GitSnapshot:
    """The git half of the trajectory: branch, recent commits, tree state."""

    branch: str | None
    commits: list[CommitEntry]
    modified: list[str]
    untracked: list[str]
    staged: list[str]
    diff_stat: str
    error: str | None = None


@dataclass(slots=True)
class ToolResult:
    """The lint/test half: whether the tool ran, its exit code, and output.

    ``ran=False`` means the tool was skipped (explicitly disabled via an
    empty override). ``error`` is set when the subprocess could not be
    executed or timed out; in that case ``returncode`` is ``None``.
    """

    name: str
    ran: bool
    returncode: int | None
    stdout: str
    stderr: str
    error: str | None = None


@dataclass(slots=True)
class SelfEvolutionReport:
    """The full trajectory snapshot for one collection pass.

    Rendered to Markdown via :meth:`to_markdown` and persisted under
    ``<report_dir>/<date>.md`` via :meth:`write_to`.
    """

    date: str
    generated_at: str
    cwd: str
    git: GitSnapshot
    ruff: ToolResult
    pytest: ToolResult
    errors: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        """Render the report as a self-contained Markdown document."""
        lines: list[str] = []
        lines.append(f"# Self-Evolution Report — {self.date}")
        lines.append("")
        lines.append(f"- **Generated:** {self.generated_at}")
        lines.append(f"- **CWD:** `{self.cwd}`")
        lines.append("")

        # --- git -----------------------------------------------------------
        lines.append("## Git")
        if self.git.error is not None:
            lines.append("")
            lines.append(f"_git unavailable: {self.git.error}_")
        else:
            branch = self.git.branch or "(detached HEAD)"
            lines.append(f"- **Branch:** `{branch}`")
            lines.append(f"- **Commits (last {len(self.git.commits)}):**")
            for commit in self.git.commits:
                # Flatten newlines in the subject and cap width so the
                # list stays scannable; the sha prefix is preserved for
                # later lookup.
                subject = commit.message.replace("\n", " ").strip()
                if len(subject) > 80:
                    subject = subject[:77] + "..."
                lines.append(
                    f"  - `{commit.sha[:10]}` {commit.author} — "
                    f"{subject} ({commit.date})"
                )
            lines.append("- **Working tree:**")
            lines.append(f"  - Modified: {len(self.git.modified)}")
            lines.append(f"  - Untracked: {len(self.git.untracked)}")
            lines.append(f"  - Staged: {len(self.git.staged)}")
            if self.git.diff_stat.strip():
                lines.append("- **Diff stat:**")
                lines.append("```")
                lines.append(self.git.diff_stat.rstrip())
                lines.append("```")
            else:
                lines.append("- **Diff stat:** _(clean)_")
        lines.append("")

        # --- ruff ----------------------------------------------------------
        lines.append("## Ruff")
        lines.append(f"- **Ran:** {'yes' if self.ruff.ran else 'no'}")
        if self.ruff.ran:
            lines.append(f"- **Returncode:** {self.ruff.returncode}")
        if self.ruff.error:
            lines.append(f"- **Error:** {self.ruff.error}")
        if self.ruff.stdout.strip():
            lines.append("```")
            lines.append(self.ruff.stdout.rstrip())
            lines.append("```")
        lines.append("")

        # --- pytest --------------------------------------------------------
        lines.append("## Pytest (collect-only)")
        lines.append(f"- **Ran:** {'yes' if self.pytest.ran else 'no'}")
        if self.pytest.ran:
            lines.append(f"- **Returncode:** {self.pytest.returncode}")
        if self.pytest.error:
            lines.append(f"- **Error:** {self.pytest.error}")
        if self.pytest.stdout.strip():
            lines.append("```")
            lines.append(self.pytest.stdout.rstrip())
            lines.append("```")
        lines.append("")

        # --- errors --------------------------------------------------------
        if self.errors:
            lines.append("## Collection errors")
            for err in self.errors:
                lines.append(f"- {err}")
            lines.append("")

        return "\n".join(lines)

    def write_to(self, report_dir: str | Path) -> Path:
        """Persist the report as ``<report_dir>/<date>.md``.

        Same-day re-runs overwrite in-place; other days are untouched
        (idempotent guarantee from the package docstring).
        """
        directory = Path(report_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.date}.md"
        path.write_text(self.to_markdown(), encoding="utf-8")
        return path


# ---------------------------------------------------------------------------
# Subprocess primitive
# ---------------------------------------------------------------------------


async def _run_cmd(
    cmd: list[str],
    *,
    cwd: str,
    timeout_s: float,
) -> tuple[int | None, str, str, str | None]:
    """Run ``cmd`` asynchronously; return ``(rc, stdout, stderr, error)``.

    ``error`` is ``None`` on a clean exit (any return code); it is set to
    a short description when the binary is missing or the call timed out,
    in which case ``rc`` is ``None`` and the streams are empty.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return None, "", "", f"command not found: {cmd[0]}"
    except OSError as exc:
        return None, "", "", f"could not spawn {cmd[0]}: {exc}"

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s
        )
    except TimeoutError:
        # Kill the runaway process so it doesn't linger as a zombie.
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return None, "", "", f"timed out after {timeout_s}s"

    rc = proc.returncode
    stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
    stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
    return rc, stdout, stderr, None


# ---------------------------------------------------------------------------
# Tool detection
# ---------------------------------------------------------------------------


def _detect_tool_cmd(cwd: str, name: str) -> list[str]:
    """Locate a dev tool argv prefix: venv-local binary, else ``uv run``.

    Returns the argv that invokes the tool (e.g.
    ``[".../.venv/Scripts/ruff.exe"]`` or ``["uv", "run", "ruff"]``). If
    neither exists, the returned argv still points at ``uv run`` and
    :func:`_run_cmd` records a clean ``FileNotFoundError`` — the caller
    decides whether that counts as "tool unavailable".
    """
    venv_bin_dir = "Scripts" if os.name == "nt" else "bin"
    exe_name = f"{name}.exe" if os.name == "nt" else name
    venv_bin = Path(cwd) / ".venv" / venv_bin_dir / exe_name
    if venv_bin.exists():
        return [str(venv_bin)]
    # Fall back to ``uv run`` — the project's documented entry point.
    return ["uv", "run", name]


async def _run_tool(
    name: str,
    cmd_override: list[str] | None,
    extra_args: list[str],
    *,
    cwd: str,
    timeout_s: float,
    errors: list[str],
) -> ToolResult:
    """Run a dev tool, appending any spawn/timeout failure to ``errors``.

    Override semantics:

    * ``cmd_override == []`` → skip (returns ``ran=False``).
    * ``cmd_override is None`` → auto-detect via :func:`_detect_tool_cmd`.
    * otherwise → use the explicit argv prefix verbatim.
    """
    if cmd_override == []:
        return ToolResult(name=name, ran=False, returncode=None, stdout="", stderr="")
    base = cmd_override or _detect_tool_cmd(cwd, name)
    cmd = [*base, *extra_args]
    rc, out, err_out, err = await _run_cmd(cmd, cwd=cwd, timeout_s=timeout_s)
    if err is not None:
        errors.append(f"{name}: {err}")
    return ToolResult(
        name=name,
        ran=True,
        returncode=rc,
        stdout=out,
        stderr=err_out,
        error=err,
    )


# ---------------------------------------------------------------------------
# git collectors
# ---------------------------------------------------------------------------


def _parse_log(raw: str) -> list[CommitEntry]:
    """Split ``git log`` output delimited by ``%x1e``/``%x1f`` into entries."""
    entries: list[CommitEntry] = []
    for record in raw.split(_RECORD_SEP):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split(_FIELD_SEP)
        if len(parts) < 4:
            continue
        sha, author, date, message = parts[0], parts[1], parts[2], parts[3]
        if sha:
            entries.append(
                CommitEntry(sha=sha, author=author, date=date, message=message)
            )
    return entries


def _parse_status_porcelain(raw: str) -> tuple[list[str], list[str], list[str]]:
    """Bucket porcelain-v1 lines into ``(modified, untracked, staged)``.

    Porcelain v1 line shape is ``XY <path>`` (2-char status + space + path):

    * ``??`` → untracked
    * ``Y == " "`` and ``X != " "`` → staged-only change
    * otherwise (``Y`` is a real change letter) → working-tree modified
    """
    modified: list[str] = []
    untracked: list[str] = []
    staged: list[str] = []
    for line in raw.splitlines():
        if len(line) < 3:
            continue
        xy = line[:2]
        path = line[3:]
        if not path:
            continue
        if xy == "??":
            untracked.append(path)
        elif xy[1] == " ":
            if xy[0] != " ":
                staged.append(path)
        else:
            modified.append(path)
    return modified, untracked, staged


async def _collect_git(
    cwd: str, *, max_commits: int, timeout_s: float
) -> GitSnapshot:
    """Gather branch + recent commits + tree state + diff stat.

    Any git failure (not a repo, binary missing) is captured in
    ``GitSnapshot.error`` and the structured fields stay empty — the rest
    of the report still renders.
    """
    # Branch first — if this fails, the cwd isn't a git repo and there's
    # no point running the rest of the git sub-queries.
    rc, branch_out, _, err = await _run_cmd(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=cwd,
        timeout_s=timeout_s,
    )
    if err is not None or rc != 0:
        return GitSnapshot(
            branch=None,
            commits=[],
            modified=[],
            untracked=[],
            staged=[],
            diff_stat="",
            error=err or "not a git repository",
        )
    branch = branch_out.strip() or None

    fmt = (
        f"%H{_FIELD_SEP}%an{_FIELD_SEP}%ad{_FIELD_SEP}%s{_RECORD_SEP}"
    )
    _, log_out, _, _ = await _run_cmd(
        ["git", "log", f"-n{max_commits}", f"--format={fmt}", "--no-merges"],
        cwd=cwd,
        timeout_s=timeout_s,
    )
    commits = _parse_log(log_out)

    _, status_out, _, _ = await _run_cmd(
        ["git", "status", "--porcelain"],
        cwd=cwd,
        timeout_s=timeout_s,
    )
    modified, untracked, staged = _parse_status_porcelain(status_out)

    _, diff_out, _, _ = await _run_cmd(
        ["git", "diff", "--stat", "--no-color"],
        cwd=cwd,
        timeout_s=timeout_s,
    )

    return GitSnapshot(
        branch=branch,
        commits=commits,
        modified=modified,
        untracked=untracked,
        staged=staged,
        diff_stat=diff_out,
        error=None,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_once(
    cwd: str | Path,
    *,
    max_commits: int = 20,
    ruff_cmd: list[str] | None = None,
    pytest_cmd: list[str] | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    now: datetime | None = None,
) -> SelfEvolutionReport:
    """Collect one trajectory snapshot at ``cwd`` and return it.

    Parameters mirror the package contract:

    * ``ruff_cmd`` / ``pytest_cmd``: explicit argv prefix to override
      auto-detection. Pass an empty list ``[]`` to force-skip a tool.
      ``None`` (default) auto-detects via :func:`_detect_tool_cmd`.
    * ``now``: injectable clock for deterministic test dates.
    """
    cwd_str = str(cwd)
    # Local time is intentional: the date stamps a developer-facing report
    # named ``YYYY-MM-DD.md``, not a UTC event log.
    clock = now if now is not None else datetime.now()
    errors: list[str] = []

    git = await _collect_git(
        cwd_str, max_commits=max_commits, timeout_s=timeout_s
    )
    if git.error is not None:
        errors.append(f"git: {git.error}")

    ruff = await _run_tool(
        "ruff",
        ruff_cmd,
        ["check", ".", "--output-format=concise"],
        cwd=cwd_str,
        timeout_s=timeout_s,
        errors=errors,
    )

    # collect-only: ``--co -q`` enumerates tests without executing them,
    # so the snapshot stays side-effect-free and fast.
    pytest_res = await _run_tool(
        "pytest",
        pytest_cmd,
        ["--co", "-q"],
        cwd=cwd_str,
        timeout_s=timeout_s,
        errors=errors,
    )

    return SelfEvolutionReport(
        date=clock.strftime("%Y-%m-%d"),
        generated_at=clock.isoformat(timespec="seconds"),
        cwd=cwd_str,
        git=git,
        ruff=ruff,
        pytest=pytest_res,
        errors=errors,
    )


__all__ = [
    "CommitEntry",
    "GitSnapshot",
    "SelfEvolutionReport",
    "ToolResult",
    "run_once",
]
