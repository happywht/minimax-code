"""JSON-RPC handlers for the ``git.*`` namespace.

These handlers expose a **read-only** git surface to the frontend —
status, diff, and log. They are used by the v0.3.0 code-review
workflow (to feed real diffs into the LLM) and by the top-bar
``GitStatusBar`` widget. The agent does not run ``git add``,
``commit``, ``push``, etc. from this layer; those stay on the
user's shell.

Endpoints
---------
``git.status`` -> ``{branch, clean, ahead, behind, modified, untracked, staged}``
``git.diff``    -> ``{diff: "<unified diff text>", scope: "<echoed scope>"}``
``git.log``     -> ``{entries: [{sha, author, message, files_changed}]}``

Wire contract
-------------
The contract is shared with the v0.3.0 code-review track (see
``docs/v0.3.0-design.md`` §3). The shape is intentionally
narrow:

* ``git.status`` reports a coarse "is the tree clean?" boolean plus
  the list of paths that are modified / untracked / staged. The UI
  uses this to colour the top-bar indicator; downstream consumers
  (e.g. the code-review skill) can fall back to ``git diff`` for the
  full picture.
* ``git.diff`` returns the unified diff text. ``scope="branch"``
  defaults to ``HEAD~1..HEAD`` (latest commit); ``scope="working"``
  is unstaged + untracked; ``scope="staged"`` is staged only. An
  explicit ``ref`` overrides the scope (e.g. ``ref="main..HEAD"``).
* ``git.log`` returns the last N commits (default 10) with the
  per-commit changed-file count — enough for a sidebar widget, not
  the full patchset.

Error contract
--------------
If the agent's CWD is not a git repository, every handler returns
``{"code": -32000, "message": "not a git repository"}`` so the UI
can show a friendly hint instead of a raw subprocess stderr. Other
git failures (e.g. a bad ref) are surfaced as ``-32000`` too with
the stderr message preserved in ``data`` for debugging.

Performance
-----------
Git invocations use the shared asynchronous process boundary with a 5 s timeout. The diff
text is small for working-tree changes; the log is bounded by ``n``
(default 10). None of this should ever block the event loop for
more than a frame or two. If we ever need to run ``git log -p``
over thousands of commits, that's a follow-up — for now we only
ship the metadata.

Wire-up
-------
:func:`register_git_handlers` is called from
:func:`minimax_code.app.register_app_handlers` and lives in
process state. Handlers are stateless — every call shells out
afresh — so the tests can swap CWDs without resetting any
singletons.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from ..execution import communicate_bounded, spawn_exec
from .handler_utils import HandlerError, ensure_cwd_within_root, project_root_from_params
from .protocol import INVALID_PARAMS
from .server import Context

logger = logging.getLogger(__name__)

# Application-level error code for "not a git repository" / "git
# invocation failed". -32000 is the JSON-RPC 2.0 reserved app
# range; the v0.3.0 design doc calls it out explicitly for this
# case.
_GIT_ERROR = -32000

# Generous but bounded — protects the event loop from a runaway
# subprocess. 5 s is enough for a ``git log -10 --format=...`` on a
# large repo; if a user has a multi-million-file monorepo and runs
# ``git diff`` against it, they'll see a timeout error and we can
# revisit. The UI should treat timeouts as "git hung" rather than
# "diff is empty".
_GIT_TIMEOUT_S = 5.0

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _run_git(args: list[str], *, cwd: str | os.PathLike[str] | None = None,
                   input_bytes: bytes | None = None) -> str:
    """Bounded, cancellable Git execution with no ambient service secrets."""
    cmd = ["git", "--no-pager", "-c", "core.fsmonitor=false", *args]
    try:
        proc = await spawn_exec(*cmd, cwd=cwd,
                                env={"GIT_OPTIONAL_LOCKS": "0"},
                                stdin=asyncio.subprocess.PIPE if input_bytes is not None else asyncio.subprocess.DEVNULL,
                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        output = await communicate_bounded(proc, timeout=_GIT_TIMEOUT_S, input=input_bytes)
    except FileNotFoundError as exc:
        raise HandlerError(_GIT_ERROR, "git binary not found on PATH", data={"cmd": cmd}) from exc
    except TimeoutError as exc:
        raise HandlerError(_GIT_ERROR, f"git timed out after {_GIT_TIMEOUT_S}s",
                           data={"cmd": cmd, "timeout_s": _GIT_TIMEOUT_S}) from exc
    if output.stdout_truncated or output.stderr_truncated:
        raise HandlerError(_GIT_ERROR, "Git output exceeded the limit; results are incomplete")
    if proc.returncode != 0:
        stderr = output.stderr.decode("utf-8", errors="replace").strip() or "git failed with no stderr"
        raise HandlerError(_GIT_ERROR, stderr, data={"cmd": cmd, "returncode": proc.returncode})
    return output.stdout.decode("utf-8", errors="replace")


def _validate_ref(ref: str | None) -> None:
    if ref is not None and (not isinstance(ref, str) or not ref.strip() or ref.startswith("-") or "\0" in ref):
        raise HandlerError(INVALID_PARAMS, "'ref' must be a revision, not a command option")


def _resolve_cwd(params: dict[str, Any] | None) -> str | None:
    """Pull an optional ``cwd`` override out of ``params``.

    Handlers accept ``params.cwd`` (string) so tests can target a
    temp repo without monkey-patching ``os.getcwd``. The real
    runtime never sets it — we just shell out against the
    process's CWD.
    """
    if not params:
        return None
    cwd = params.get("cwd")
    if cwd is None or cwd == "":
        return None
    if not isinstance(cwd, str):
        raise HandlerError(
            INVALID_PARAMS,
            "'cwd' must be a string when provided",
        )
    return cwd

async def _scoped_cwd(params: dict[str, Any] | None) -> str | None:
    """Resolve the git working directory with optional project scope.

    Without a rooted ``project_id`` this is exactly :func:`_resolve_cwd`
    — legacy behaviour, zero contract change for existing callers. When
    the params carry a project with a ``root_path`` (v1.3.0 strict
    isolation): an explicit ``cwd`` must resolve inside the project
    root, and a missing ``cwd`` anchors at the project root instead of
    the process CWD. Shared with the ``patch.*`` handlers.
    """
    if isinstance(params, dict) and "session_id" in params:
        from ..app import get_db, init_runtime
        from ..workspace_ctx import resolve_root_for_session
        sid = params["session_id"]
        if not isinstance(sid, str) or not sid.strip():
            raise HandlerError(INVALID_PARAMS, "session_id must be an explicit Native session")
        await init_runtime()
        db = get_db()
        session = await db.fetchone("SELECT engine FROM sessions WHERE id=?", (sid,)) if db else None
        if session is None or session["engine"] != "native":
            raise HandlerError(INVALID_PARAMS, "Scoped review requires an existing Native session")
        root = await resolve_root_for_session(sid)
        if params.get("project_id") is not None or params.get("cwd") is not None:
            raise HandlerError(INVALID_PARAMS, "Use only session_id for a bound review workspace")
    else:
        root = await project_root_from_params(params)
    cwd = _resolve_cwd(params)
    if root is None:
        return cwd
    if cwd is None:
        return str(root)
    return ensure_cwd_within_root(cwd, root)


async def _run_workspace_diff(params: dict[str, Any]) -> dict[str, Any]:
    """Read the current bound workspace, never attribute its changes to the run."""
    from ..app import ensure_db
    from ..execution import ExecutionPolicy, execution_scope
    from ..storage.dao.runs import AgentRunsDAO
    from ..workspace_ctx import require_directory, resolve_root_for_session
    from .host_session_gate import require_host_session

    if set(params) != {"run_id", "session_id"}:
        raise HandlerError(INVALID_PARAMS, "Run workspace review requires only run_id and session_id")
    run_id, session_id = params["run_id"], params["session_id"]
    if not all(isinstance(value, str) and value for value in (run_id, session_id)):
        raise HandlerError(INVALID_PARAMS, "run_id and session_id must be non-empty strings")
    db = await ensure_db()
    dao = AgentRunsDAO(db)

    async def binding():
        run = await dao.get_run(run_id)
        session = await db.fetchone("SELECT id,project_id,engine FROM sessions WHERE id=?", (session_id,))
        metadata = (run or {}).get("metadata") or {}
        recorded = metadata.get("workspace_snapshot") if isinstance(metadata, dict) else None
        model = metadata.get("model_snapshot") if isinstance(metadata, dict) else None
        engine = metadata.get("engine") if isinstance(metadata, dict) else None
        if engine is None and isinstance(model, dict):
            engine = model.get("engine")
        if (not run or run["session_id"] != session_id or not session or not isinstance(metadata, dict)
                or session["engine"] not in {"native", "codex"}
                or engine != session["engine"]
                or "project_id" not in metadata or metadata["project_id"] != session["project_id"]
                or not isinstance(recorded, str) or not Path(recorded).is_absolute()):
            raise HandlerError(INVALID_PARAMS, "Run workspace or project binding is unavailable or changed")
        if session["project_id"] and not await db.fetchone("SELECT id FROM projects WHERE id=?", (session["project_id"],)):
            raise HandlerError(INVALID_PARAMS, "Run project is unavailable")
        await require_host_session(session_id)
        root = require_directory(await resolve_root_for_session(session_id))
        if root != Path(recorded):
            raise HandlerError(INVALID_PARAMS, "Run workspace binding changed; inspect it before reviewing")
        info = root.stat()
        return root, (info.st_dev, info.st_ino), session["project_id"]

    root, identity, project_id = await binding()
    with execution_scope(ExecutionPolicy(workspace=root)):
        repo_root = Path((await _run_git(["rev-parse", "--show-toplevel"], cwd=root)).strip()).resolve()
        if not root.is_relative_to(repo_root):
            raise HandlerError(INVALID_PARAMS, "Git repository does not contain the task workspace")
        common = ["diff", "--no-color", "--no-ext-diff", "--no-textconv", "-M"]
        working = await _run_git([*common, "--", "."], cwd=root)
        staged = await _run_git([*common, "--staged", "--", "."], cwd=root)
        status = await _run_git(["-c", "status.relativePaths=true", "status", "--porcelain=v2", "-z", "--untracked-files=all", "--", "."], cwd=root)
    current_root, current_identity, current_project = await binding()
    if (current_root, current_identity, current_project) != (root, identity, project_id):
        raise HandlerError(INVALID_PARAMS, "Run workspace binding changed during review")
    _, paths, _ = _parse_status_porcelain_v2(status)
    untracked = []
    for name in paths:
        path = Path(os.path.abspath(repo_root / name))
        if not path.is_relative_to(root):
            raise HandlerError(INVALID_PARAMS, "Git returned a path outside the task workspace")
        untracked.append(path.relative_to(root).as_posix())
    return {"run_id": run_id, "session_id": session_id, "project_id": project_id,
            "workspace": str(root), "scope": "current_workspace", "diff": working,
            "staged_diff": staged, "untracked": untracked}

def _parse_status_porcelain_v2(text: str) -> tuple[list[str], list[str], list[str]]:
    """Split ``git status --porcelain=v2 -z`` output into three buckets.

    Porcelain v2 entry shapes (with ``-z``):

    * Tracked: ``1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>\\0``
    * Renamed/copied: ``2 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <X><score> <path>\\0<origPath>\\0``
    * Untracked: ``? <path>\\0``

    ``<XY>`` is the staged/unstaged X/Y code pair; ``X`` is the
    index vs HEAD side, ``Y`` is the working tree vs index side.
    We bucket based on ``Y`` (working tree):

    * ``Y == "."`` and ``X != "."``  → staged-only change
    * ``Y == "."`` and ``X == "."``  → untouched (shouldn't show up)
    * ``Y == "?"``  → untracked
    * else  → working-tree modification (M, D, T, ...)

    Returns
    -------
    (modified, untracked, staged)
    """
    if not text:
        return [], [], []
    modified: list[str] = []
    untracked: list[str] = []
    staged: list[str] = []
    records = iter(text.split("\x00"))
    for raw in records:
        if not raw:
            continue
        # Untracked: "? <path>" — single ``?`` then a space.
        if raw.startswith("? "):
            path = raw[2:]
            if path:
                untracked.append(path)
            continue
        # Tracked normal: "1 <XY> ..." — strip the "1 " prefix
        # then the first 2 chars are the X/Y code.
        if raw.startswith("1 ") and len(raw) >= 4:
            x = raw[2]
            y = raw[3]
            parts = raw.split(" ", 8)
            if len(parts) != 9:
                continue
            path = parts[8]
        elif raw.startswith("2 ") and len(raw) >= 4:
            # Renamed/copied: "2 <XY> ... <X><score> <path>"
            # The path is the field after ``<X><score>``.
            x = raw[2]
            y = raw[3]
            # With -z, the next NUL-delimited record is the source path,
            # even if it resembles a status entry. Do not parse it as one.
            next(records, None)
            parts = raw.split(" ", 9)
            if len(parts) != 10:
                continue
            path = parts[9]
        else:
            continue
        if not path:
            continue
        if y == "?":
            untracked.append(path)
        elif y == "." and x == ".":
            # Unchanged — shouldn't appear in status output.
            continue
        elif y == ".":
            staged.append(path)
        else:
            modified.append(path)
    return modified, untracked, staged

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_git_handlers(server: Any) -> None:
    """Register the ``git.*`` handlers on ``server``."""

    async def handle_git_status(params: Any, ctx: Context) -> None:
        try:
            cwd = await _scoped_cwd(params if isinstance(params, dict) else None)

            # Branch: ``rev-parse --abbrev-ref HEAD`` returns
            # ``HEAD`` when detached; we echo that as-is so the UI
            # can show "HEAD (detached)" if it wants.
            branch = (
                (await _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)).strip()
                or "HEAD"
            )

            # ahead/behind: ``rev-list --left-right --count HEAD...@{u}``
            # returns ``<ahead>\t<behind>``. When there's no
            # upstream it errors out — we treat that as 0/0.
            ahead = 0
            behind = 0
            try:
                rev_out = (await _run_git(
                    ["rev-list", "--left-right", "--count", "HEAD...@{u}"],
                    cwd=cwd,
                )).strip()
                if rev_out:
                    left, right = rev_out.split("\t", 1)
                    ahead = int(left)
                    behind = int(right)
            except HandlerError:
                # No upstream configured — fall back to 0/0.
                ahead = 0
                behind = 0

            # Status: porcelain v2 + NUL gives us a parseable
            # stream of (X, Y, path) tuples.
            status_text = (await _run_git(
                ["status", "--porcelain=v2", "-z", "--untracked-files=normal"],
                cwd=cwd,
            ))
            modified, untracked, staged = _parse_status_porcelain_v2(status_text)
            # "clean" means nothing modified, nothing untracked,
            # nothing staged. We don't penalise ahead/behind —
            # those are pushes, not dirt.
            clean = not (modified or untracked or staged)

            await ctx.reply(
                {
                    "branch": branch,
                    "clean": clean,
                    "ahead": ahead,
                    "behind": behind,
                    "modified": modified,
                    "untracked": untracked,
                    "staged": staged,
                }
            )
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("git.status failed")
            await ctx.reply_error(_GIT_ERROR, "git.status failed")

    async def handle_git_diff(params: Any, ctx: Context) -> None:
        try:
            p = params if isinstance(params, dict) else {}
            if "run_id" in p:
                await ctx.reply(await _run_workspace_diff(p))
                return
            cwd = await _scoped_cwd(p)
            scope = p.get("scope", "working")
            ref = p.get("ref")
            if not isinstance(scope, str):
                raise HandlerError(
                    INVALID_PARAMS, "'scope' must be a string when provided"
                )
            if ref is not None and not isinstance(ref, str):
                raise HandlerError(
                    INVALID_PARAMS, "'ref' must be a string when provided"
                )

            _validate_ref(ref)

            # Build the diff argv. ``--no-color`` strips ANSI for
            # the wire; ``-M`` enables rename detection (catches
            # ``git mv`` as a rename rather than add+delete).
            base_args = ["diff", "--no-color", "--no-ext-diff", "--no-textconv", "-M"]
            if ref is not None:
                # Explicit ref wins. Treat the ref as a revision
                # range the user composed themselves; ``git diff``
                # handles e.g. ``main..HEAD`` and bare SHAs alike.
                diff_target = ref
            elif scope == "staged":
                diff_target = "--staged"
            elif scope == "branch":
                # Latest commit on this branch relative to its
                # parent. ``HEAD~1..HEAD`` matches the design
                # doc's "latest commit" default.
                diff_target = "HEAD~1..HEAD"
            elif scope == "working":
                diff_target = None  # plain ``git diff`` = unstaged + untracked
            else:
                raise HandlerError(
                    INVALID_PARAMS,
                    f"unknown scope {scope!r}; expected 'staged' | 'branch' | 'working' or an explicit 'ref'",
                )

            if diff_target is not None:
                cmd = [*base_args, diff_target]
            else:
                cmd = base_args
            diff_text = (await _run_git([*cmd, "--"], cwd=cwd))

            await ctx.reply(
                {
                    "diff": diff_text,
                    "scope": ref if ref is not None else scope,
                    **({"session_id": p["session_id"], "workspace": cwd} if "session_id" in p else {}),
                }
            )
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("git.diff failed")
            await ctx.reply_error(_GIT_ERROR, "git.diff failed")

    async def handle_git_log(params: Any, ctx: Context) -> None:
        try:
            p = params if isinstance(params, dict) else {}
            cwd = await _scoped_cwd(p)
            n = p.get("n", 10)
            if not isinstance(n, int) or n <= 0:
                raise HandlerError(
                    INVALID_PARAMS, "'n' must be a positive integer"
                )
            # Cap to a sane bound — the UI's log viewer is a
            # sidebar, not a gitk replacement.
            n = min(n, 50)

            # Use a unique end-of-header marker (``__END__``) so we
            # can split on a delimiter that never appears in real
            # git output. The format line itself ends with a
            # newline (default), and ``--name-only`` (without
            # ``-z``) emits each file path on its own line, so the
            # resulting stream is::
            #
            #     __COMMIT__<sha>__<author>__<subject>__END__\n
            #     <file1>\n
            #     <file2>\n
            #     __COMMIT__<sha>__<author>__<subject>__END__\n
            #     ...
            #
            # The ``__END__`` marker is preserved verbatim so a
            # subject that contains ``\n`` (rare but possible) does
            # not bleed into the next commit's file list.
            fmt = "__COMMIT__%H__%an__%s__END__"
            raw = (await _run_git(
                ["log", f"-n{n}", f"--format={fmt}", "--name-only"],
                cwd=cwd,
            ))

            entries: list[dict[str, Any]] = []
            if raw.strip():
                for chunk in raw.split("__COMMIT__"):
                    if not chunk.strip():
                        continue
                    # The chunk is ``<sha>__<author>__<subject>__END__\n<files...>``.
                    end_idx = chunk.find("__END__")
                    if end_idx < 0:
                        # Malformed section — skip rather than crash.
                        logger.warning("git.log: skipping malformed chunk: %r", chunk)
                        continue
                    header = chunk[:end_idx]
                    after = chunk[end_idx + len("__END__") :]
                    header_parts = header.split("__", 2)
                    # ``header_parts`` is [sha, author, subject]
                    # unless subject itself contains ``__`` — we
                    # cap the split to 2 so the subject is the
                    # remainder.
                    sha = header_parts[0] if len(header_parts) > 0 else ""
                    author = header_parts[1] if len(header_parts) > 1 else ""
                    message = header_parts[2] if len(header_parts) > 2 else ""
                    files = [line for line in after.splitlines() if line.strip()]
                    if not sha:
                        continue
                    entries.append(
                        {
                            "sha": sha,
                            "author": author,
                            "message": message,
                            "files_changed": files,
                        }
                    )

            await ctx.reply({"entries": entries})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("git.log failed")
            await ctx.reply_error(_GIT_ERROR, "git.log failed")

    server.register("git.status", handle_git_status)
    server.register("git.diff", handle_git_diff)
    server.register("git.log", handle_git_log)

# ---------------------------------------------------------------------------
# Re-exports for tests + the code-review skill to share
# ---------------------------------------------------------------------------

__all__ = [
    "_parse_status_porcelain_v2",  # exposed for unit tests
    "register_git_handlers",
]
