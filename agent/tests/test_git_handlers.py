"""Unit tests for the ``git.*`` IPC handlers.

The handler module is shared between two v0.3.0 tracks (the
code-review flow and the top-bar ``GitStatusBar`` widget). We
test the public wire contract: every call goes through a
synthetic :class:`~minimax_code.ipc.server.Context` and we
assert on the JSON-RPC reply / error envelope, not the internal
helper functions. That keeps the test resilient to internal
refactors in :mod:`handlers_git`.

Tests run against a synthetic git repository the fixture builds
in a temp dir so we never depend on the host machine's working
tree. The fixture is a *real* git repo (we shell out to
``git init`` so the production code path is exercised) seeded
with a couple of commits and an unstaged edit on a tracked
file.

Handler call convention
----------------------
The handlers accept an optional ``cwd`` inside ``params`` (string
or None). When the test wants to target the temp repo, it passes
``{"cwd": "<path>"}``; otherwise the handler shells out against
the agent's process cwd.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest

from minimax_code.config import Config
from minimax_code.ipc.handlers_git import register_git_handlers
from minimax_code.ipc.server import IPCServer

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _git_available() -> bool:
    """True iff the host has a ``git`` binary on PATH."""
    return shutil.which("git") is not None


pytestmark = pytest.mark.skipif(
    not _git_available(), reason="git binary required for these tests"
)


# Application error code the handlers use for "not a git repository"
# and other non-zero git exits. Matches the constant in
# ``handlers_git.py`` (-32000 is the JSON-RPC app-error range).
GIT_ERROR = -32000


# Cap how high git walks up looking for a ``.git`` directory. Set
# in the env for tests that want a truly "not a repository"
# workspace — otherwise the test cwd is itself inside the agent
# project, so any empty subdirectory will still resolve to the
# project repo. The agent handler doesn't strip this env var
# (it always inherits from the agent process), so we set it
# around the *test* subprocess if we ever go via subprocess; for
# the in-process handler path, the handler calls
# ``subprocess.run(cwd=...)`` and that subprocess inherits our
# env, so the cap sticks.
_NOT_A_REPO_CEILING = Path(tempfile.mkdtemp(prefix="git_not_a_repo_"))


class _CapturingContext:
    """Drop-in :class:`Context` that records the reply envelope.

    The real ``Context`` requires a full ``IPCServer`` because it
    funnels replies through ``server._send``. For handler unit
    tests that's overkill — we only care about the result / error
    payload the handler would have emitted, not the line-framing
    on stdout. So we shadow :meth:`reply` and
    :meth:`reply_error` to capture the value and stash it on the
    instance.
    """

    def __init__(self) -> None:
        self.reply_payload: Any = None
        self.reply_error_payload: tuple[int, str, Any] | None = None

    async def reply(self, result: Any) -> None:
        self.reply_payload = result

    async def reply_error(self, code: int, message: str, data: Any = None) -> None:
        self.reply_error_payload = (code, message, data)

    async def emit(self, event: str, data: Any = None) -> None:  # pragma: no cover
        return None


@pytest.fixture(autouse=True)
def _git_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cap git's upward ``.git`` search for the duration of the test.

    This is harmless for the *repo* tests (the fixture sets an
    explicit ``cwd`` pointing at the temp repo, which overrides
    the upward search anyway) and essential for the
    *not-a-repo* tests, which would otherwise find the project
    root's ``.git`` and pass.
    """
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(_NOT_A_REPO_CEILING))


def _build_repo(tmp_path: Path) -> Path:
    """Create a fresh git repo with two commits and an unstaged edit.

    Layout
    ------
    ``<tmp>/repo/``
        ``README.md``   — initial content, committed
        ``src/app.py``  — added in the second commit
        ``src/app.py``  — modified, unstaged (visible to ``git diff``)
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "Test")
    env.setdefault("GIT_AUTHOR_EMAIL", "test@example.com")
    env.setdefault("GIT_COMMITTER_NAME", "Test")
    env.setdefault("GIT_COMMITTER_EMAIL", "test@example.com")
    env.setdefault("GIT_PAGER", "cat")
    # Cap upward search so we don't accidentally pick up an
    # outer .git that the test runner happens to live inside.
    env["GIT_CEILING_DIRECTORIES"] = str(tmp_path)

    def _run(*args: str, cwd: Path | None = None) -> str:
        import subprocess

        out = subprocess.check_output(
            ["git", *args],
            cwd=str(cwd or repo),
            env=env,
            stderr=subprocess.STDOUT,
        )
        return out.decode("utf-8", errors="replace")

    _run("init", "--initial-branch=main")
    _run("config", "user.email", "test@example.com")
    _run("config", "user.name", "Test")

    readme = repo / "README.md"
    readme.write_text("hello\n", encoding="utf-8")
    _run("add", "README.md")
    _run("commit", "-m", "initial")

    src = repo / "src"
    src.mkdir()
    app = src / "app.py"
    app.write_text("def main():\n    return 1\n", encoding="utf-8")
    _run("add", "src/app.py")
    _run("commit", "-m", "add app")

    # Unstaged edit — visible to ``git diff`` (working tree).
    app.write_text(
        "def main():\n    return 1\n\ndef helper():\n    return 2\n",
        encoding="utf-8",
    )
    return repo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A fresh temp git repo with two commits + an unstaged edit."""
    return _build_repo(tmp_path)


def _make_handler(
    cwd: Path, method: str
) -> tuple[Callable[..., Coroutine[Any, Any, None]], _CapturingContext]:
    """Register a single ``git.<method>`` handler and return the bound coroutine.

    Returns ``(handler, ctx)`` — call ``await handler(params, ctx)``
    to drive the request through the production code path.
    """
    server = IPCServer(config=Config.from_env(), stdin=sys.stdin, stdout=sys.stdout)
    register_git_handlers(server)

    # Reach into the server's handler dict to grab the bound
    # coroutine the registration created.
    handler = server._handlers[method]
    return handler, _CapturingContext()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# git.status
# ---------------------------------------------------------------------------


class TestGitStatus:
    @pytest.mark.asyncio
    async def test_returns_branch_and_modified_list(self, repo: Path) -> None:
        handler, ctx = _make_handler(repo, "git.status")
        await handler({"cwd": str(repo)}, ctx)

        assert ctx.reply_error_payload is None
        payload = ctx.reply_payload
        assert payload is not None
        assert payload["branch"] == "main"
        assert payload["clean"] is False
        assert isinstance(payload["modified"], list)
        assert any("src/app.py" in p for p in payload["modified"])
        # The repo has no upstream so ahead/behind are 0.
        assert payload["ahead"] == 0
        assert payload["behind"] == 0

    @pytest.mark.asyncio
    async def test_clean_repo_reports_no_modified(self, tmp_path: Path) -> None:
        repo = _build_repo(tmp_path)
        # Commit the working-tree edit so the tree is clean.
        import subprocess

        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "GIT_PAGER": "cat",
        }
        subprocess.check_call(
            ["git", "add", "src/app.py"], cwd=str(repo), env=env
        )
        subprocess.check_call(
            ["git", "commit", "-m", "second"], cwd=str(repo), env=env
        )
        handler, ctx = _make_handler(repo, "git.status")
        await handler({"cwd": str(repo)}, ctx)
        assert ctx.reply_payload["clean"] is True
        assert ctx.reply_payload["modified"] == []

    @pytest.mark.asyncio
    async def test_status_shape_includes_untracked_and_staged(self, repo: Path) -> None:
        """The v0.3.0 status superset (modified + untracked + staged)."""
        # Add a brand-new untracked file to exercise the untracked bucket.
        (repo / "untracked.txt").write_text("hello\n", encoding="utf-8")
        handler, ctx = _make_handler(repo, "git.status")
        await handler({"cwd": str(repo)}, ctx)
        payload = ctx.reply_payload
        assert payload is not None
        # The superset is part of the contract.
        assert "untracked" in payload
        assert "staged" in payload
        assert any("untracked.txt" in p for p in payload["untracked"])

    @pytest.mark.asyncio
    async def test_not_a_repo_returns_application_error(self, tmp_path: Path) -> None:
        # An empty directory whose parent is *not* a git work tree.
        # The agent fixture lives inside the project root which is
        # itself a repo, so we have to use a *truly* external path
        # to defeat git's upward ``.git`` search. The autouse
        # ``_git_ceiling`` fixture caps that search at the parent
        # of this empty dir.
        empty = Path(_NOT_A_REPO_CEILING) / f"empty_{os.getpid()}_{id(tmp_path)}"
        empty.mkdir(exist_ok=True)
        handler, ctx = _make_handler(empty, "git.status")
        await handler({"cwd": str(empty)}, ctx)
        assert ctx.reply_payload is None
        assert ctx.reply_error_payload is not None
        code, message, _ = ctx.reply_error_payload
        assert code == GIT_ERROR
        # The error message should mention the failure context.
        assert message  # any non-empty message is fine


# ---------------------------------------------------------------------------
# git.diff
# ---------------------------------------------------------------------------


class TestGitDiff:
    @pytest.mark.asyncio
    async def test_working_diff_includes_added_lines(self, repo: Path) -> None:
        handler, ctx = _make_handler(repo, "git.diff")
        await handler({"cwd": str(repo), "scope": "working"}, ctx)
        assert ctx.reply_error_payload is None
        payload = ctx.reply_payload
        assert "diff --git" in payload["diff"]
        # The fixture added ``def helper()`` + ``return 2`` so the
        # diff must contain at least one ``+`` line.
        plus_lines = [
            ln
            for ln in payload["diff"].splitlines()
            if ln.startswith("+") and not ln.startswith("+++")
        ]
        assert len(plus_lines) >= 1

    @pytest.mark.asyncio
    async def test_staged_diff_is_empty_when_nothing_staged(self, repo: Path) -> None:
        handler, ctx = _make_handler(repo, "git.diff")
        await handler({"cwd": str(repo), "scope": "staged"}, ctx)
        assert ctx.reply_error_payload is None
        assert ctx.reply_payload["diff"] == ""

    @pytest.mark.asyncio
    async def test_branch_diff_matches_last_commit(self, repo: Path) -> None:
        handler, ctx = _make_handler(repo, "git.diff")
        await handler({"cwd": str(repo), "scope": "branch"}, ctx)
        assert ctx.reply_error_payload is None
        diff = ctx.reply_payload["diff"]
        # The "branch" scope is ``HEAD~1..HEAD`` — should show the
        # ``src/app.py`` addition.
        assert "src/app.py" in diff
        plus_lines = [
            ln for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")
        ]
        assert any("def main" in ln for ln in plus_lines)

    @pytest.mark.asyncio
    async def test_explicit_ref_round_trip(self, repo: Path) -> None:
        handler, ctx = _make_handler(repo, "git.diff")
        # Use an explicit ref range. ``git diff HEAD~1..HEAD`` is
        # the same shape as the "branch" scope — a single-commit
        # diff that shows what ``add app`` introduced.
        await handler({"cwd": str(repo), "ref": "HEAD~1..HEAD"}, ctx)
        assert ctx.reply_error_payload is None
        diff = ctx.reply_payload["diff"]
        # The "add app" commit added ``src/app.py`` so the diff
        # should mention it.
        assert "src/app.py" in diff
        assert "diff --git" in diff

    @pytest.mark.asyncio
    async def test_default_scope_is_working(self, repo: Path) -> None:
        handler, ctx = _make_handler(repo, "git.diff")
        # No scope at all — should default to "working".
        await handler({"cwd": str(repo)}, ctx)
        assert ctx.reply_error_payload is None
        assert "+" in ctx.reply_payload["diff"]

    @pytest.mark.asyncio
    async def test_not_a_repo_returns_application_error(self, tmp_path: Path) -> None:
        empty = Path(_NOT_A_REPO_CEILING) / f"empty_{os.getpid()}_{id(tmp_path)}"
        empty.mkdir(exist_ok=True)
        handler, ctx = _make_handler(empty, "git.diff")
        await handler({"cwd": str(empty), "scope": "working"}, ctx)
        assert ctx.reply_payload is None
        code, message, _ = ctx.reply_error_payload
        assert code == GIT_ERROR
        assert message


# ---------------------------------------------------------------------------
# git.log
# ---------------------------------------------------------------------------


class TestGitLog:
    @pytest.mark.asyncio
    async def test_returns_entries_field_with_list(self, repo: Path) -> None:
        """The wire contract is ``{entries: [...]}``; the v0.3.0
        git-integration track owns the parser correctness.
        """
        handler, ctx = _make_handler(repo, "git.log")
        await handler({"cwd": str(repo), "n": 5}, ctx)
        assert ctx.reply_error_payload is None
        payload = ctx.reply_payload
        assert "entries" in payload
        assert isinstance(payload["entries"], list)
        # Each entry is a record with the standard keys; we don't
        # assert on the parser specifics because the log handler
        # lives in the git-integration track.
        for entry in payload["entries"]:
            assert "sha" in entry
            assert "author" in entry
            assert "message" in entry
            assert "files_changed" in entry
            assert isinstance(entry["files_changed"], list)

    @pytest.mark.asyncio
    async def test_invalid_n_rejected(self, repo: Path) -> None:
        """``n`` must be a positive integer — handler validates this."""
        handler, ctx = _make_handler(repo, "git.log")
        await handler({"cwd": str(repo), "n": -3}, ctx)
        assert ctx.reply_payload is None
        assert ctx.reply_error_payload is not None

    @pytest.mark.asyncio
    async def test_not_a_repo_returns_application_error(self, tmp_path: Path) -> None:
        empty = Path(_NOT_A_REPO_CEILING) / f"empty_{os.getpid()}_{id(tmp_path)}"
        empty.mkdir(exist_ok=True)
        handler, ctx = _make_handler(empty, "git.log")
        await handler({"cwd": str(empty)}, ctx)
        assert ctx.reply_payload is None
        code, _, _ = ctx.reply_error_payload
        assert code == GIT_ERROR


# ---------------------------------------------------------------------------
# code-review skill integration — review_diff helper
# ---------------------------------------------------------------------------


class TestReviewDiff:
    """The skill handler short-circuits to ``code_review._review_diff``."""

    def test_empty_diff(self) -> None:
        from minimax_code.agent.skills._builtin.code_review import _review_diff

        review = _review_diff("")
        assert review["text"].startswith("I don't see any changes")
        assert review["comments"] == []
        stats = review["stats"]
        assert stats == {"files": 0, "additions": 0, "deletions": 0}

    def test_simple_diff_anchors_a_comment(self) -> None:
        from minimax_code.agent.skills._builtin.code_review import _review_diff

        diff = (
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -0,0 +1,3 @@\n"
            "+def main():\n"
            "+    return 1\n"
            "+\n"
        )
        review = _review_diff(diff)
        stats = review["stats"]
        assert stats["files"] == 1
        assert stats["additions"] == 3
        assert stats["deletions"] == 0
        comments = review["comments"]
        assert len(comments) >= 1
        first = comments[0]
        assert first["file"] == "foo.py"
        assert first["line"] == 1
        assert first["severity"] in {"info", "warning"}
        assert "Review this addition" in first["message"] or "Long line" in first["message"]

    def test_long_line_escalates_to_warning(self) -> None:
        from minimax_code.agent.skills._builtin.code_review import _review_diff

        long_line = "x = '" + "a" * 130 + "'"
        diff = (
            "diff --git a/bar.py b/bar.py\n"
            "--- a/bar.py\n"
            "+++ b/bar.py\n"
            "@@ -0,0 +1,1 @@\n"
            f"+{long_line}\n"
        )
        review = _review_diff(diff)
        warnings = [c for c in review["comments"] if c["severity"] == "warning"]
        assert warnings, "expected at least one warning for the >120-char line"

    def test_per_file_comment_cap(self) -> None:
        from minimax_code.agent.skills._builtin.code_review import _review_diff

        # 5 added lines in one file — the helper caps at 3
        # comments per file so the UI isn't drowned in noise.
        added = "\n".join(f"+line_{i}" for i in range(5))
        diff = (
            "diff --git a/baz.py b/baz.py\n"
            "--- a/baz.py\n"
            "+++ b/baz.py\n"
            "@@ -0,0 +1,5 @@\n"
            f"{added}\n"
        )
        review = _review_diff(diff)
        per_file = [c for c in review["comments"] if c["file"] == "baz.py"]
        assert len(per_file) == 3

    def test_diff_stats_counts_deletions(self) -> None:
        from minimax_code.agent.skills._builtin.code_review import _diff_stats

        diff = (
            "diff --git a/q.py b/q.py\n"
            "--- a/q.py\n"
            "+++ b/q.py\n"
            "@@ -1,3 +1,1 @@\n"
            "-line_a\n"
            "-line_b\n"
            "+line_c\n"
        )
        stats = _diff_stats(diff)
        assert stats["files"] == 1
        assert stats["additions"] == 1
        assert stats["deletions"] == 2
