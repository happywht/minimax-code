from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from minimax_code.execution import ExecutionPolicy, capture_output, execution_scope, spawn_exec
from minimax_code.orchestrator.subagent import SubAgentConfig, SubAgentHandle, SubAgentRuntime
from minimax_code.workspace.child import _git, collect_child, load_child, prepare_child
from minimax_code.workspace_ctx import current_root, reset_current_root, set_current_root


@pytest.fixture
def parent(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    token = set_current_root(root)
    try:
        yield root
    finally:
        reset_current_root(token)


async def test_independent_shell_and_explicit_collection(parent):
    (parent / "hello.txt").write_text("before")
    child = await prepare_child(parent, "run_shell")
    with execution_scope(ExecutionPolicy(workspace=parent)), child.scope():
        proc = await spawn_exec(sys.executable, "-c",
                                "from pathlib import Path; Path('hello.txt').write_text('after')",
                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await capture_output(proc, timeout=10)
        assert proc.returncode == 0
    assert current_root() == parent
    assert (parent / "hello.txt").read_text() == "before"
    child.finish("completed")
    report = collect_child(child)
    assert report["complete"]
    assert (parent / "hello.txt").read_text() == "after"
    assert (child.base / "backups/hello.txt").read_text() == "before"
    assert collect_child(load_child(parent, "run_shell"))["already_collected"]
    assert child.root.exists()  # No destructive implicit pruning.


async def test_child_scope_cannot_switch_from_another_parent(parent, tmp_path):
    child = await prepare_child(parent, "foreign")
    other = tmp_path / "other"
    other.mkdir()
    with execution_scope(ExecutionPolicy(workspace=other)):
        with pytest.raises(PermissionError, match="outside the parent"):
            with child.scope():
                pass
        assert current_root() == parent


async def test_parallel_children_detect_conflict_and_preflight_all_files(parent):
    (parent / "shared.txt").write_text("base")
    first, second = await asyncio.gather(prepare_child(parent, "one"), prepare_child(parent, "two"))
    (first.root / "shared.txt").write_text("first")
    (second.root / "shared.txt").write_text("second")
    (second.root / "a_new.txt").write_text("new")
    first.finish("completed")
    second.finish("completed")
    assert collect_child(first)["complete"]
    result = collect_child(second)
    assert not result["complete"]
    assert result["conflicts"][0]["path"] == "shared.txt"
    assert not (parent / "a_new.txt").exists()
    assert (parent / "shared.txt").read_text() == "first"
    assert (second.root / "shared.txt").read_text() == "second"


async def test_user_changes_and_deletions_are_preserved(parent):
    (parent / "edit.txt").write_text("base")
    (parent / "delete.txt").write_text("keep")
    child = await prepare_child(parent, "change")
    (child.root / "edit.txt").write_text("agent")
    (child.root / "delete.txt").unlink()
    child.finish("completed")
    (parent / "edit.txt").write_text("user")
    result = collect_child(child, on_conflict="skip")
    assert not result["complete"]
    assert len(result["conflicts"]) == 2
    assert (parent / "edit.txt").read_text() == "user"
    assert (parent / "delete.txt").read_text() == "keep"
    assert load_child(parent, "change").record["state"] == "completed"


async def test_running_and_post_run_mutation_cannot_be_collected(parent):
    child = await prepare_child(parent, "active")
    with pytest.raises(ValueError, match="active"):
        collect_child(child)
    child.finish("completed")
    (child.root / "late.txt").write_text("late")
    with pytest.raises(ValueError, match="changed after"):
        collect_child(child)


async def test_review_and_copy_reject_changed_source(parent, monkeypatch):
    from minimax_code import file_mutations
    from minimax_code.workspace.child_review import preview

    (parent / "a.txt").write_text("original", encoding="utf-8")
    child = await prepare_child(parent, "review")
    (child.root / "a.txt").write_text("child result", encoding="utf-8")
    child.finish("completed")
    first = preview(child)
    assert first["files"][0]["path"] == "a.txt"
    assert "child result" in first["files"][0]["diff"]
    (parent / "a.txt").write_text("user edit", encoding="utf-8")
    assert preview(child)["files"][0]["conflict"] == "parent_changed"
    assert preview(child)["revision"] != first["revision"]
    (parent / "a.txt").write_text("original", encoding="utf-8")
    original = file_mutations._atomic_replace

    def mutate_source(target, write, **kwargs):
        if target == parent / "a.txt":
            (child.root / "a.txt").write_text("late mutation", encoding="utf-8")
        return original(target, write, **kwargs)

    monkeypatch.setattr(file_mutations, "_atomic_replace", mutate_source)
    with pytest.raises(OSError, match="Source changed"):
        collect_child(child)
    assert (parent / "a.txt").read_text(encoding="utf-8") == "original"


async def test_git_worktree_includes_dirty_inputs_without_touching_parent_index(parent):
    await _git(parent, "init")
    (parent / "source.txt").write_text("committed")
    await _git(parent, "add", "source.txt")
    await _git(parent, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
               "commit", "-m", "base")
    (parent / "source.txt").write_text("staged")
    await _git(parent, "add", "source.txt")
    (parent / "source.txt").write_text("unstaged")
    (parent / "资料.txt").write_text("untracked")
    before = await _git(parent, "status", "--porcelain")
    index = await _git(parent, "diff", "--cached")
    with execution_scope(ExecutionPolicy(workspace=parent)):
        child = await prepare_child(parent, "git_child")
    assert child.record["kind"] == "git_worktree"
    assert (child.root / "source.txt").read_text() == "unstaged"
    assert (child.root / "资料.txt").read_text() == "untracked"
    assert await _git(child.root, "rev-parse", "--show-toplevel") == str(child.root).replace("\\", "/") + "\n"
    assert await _git(parent, "diff", "--cached") == index
    # The only new parent entry is owned metadata, never the staged index.
    after = await _git(parent, "status", "--porcelain")
    assert after.replace("?? .minimax/\n", "") == before


async def test_alias_and_unsafe_run_ids_fail_closed(parent, tmp_path):
    for run_id in ("../escape", "CON", "a/b"):
        with pytest.raises(ValueError):
            await prepare_child(parent, run_id)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    try:
        (parent / "alias.txt").symlink_to(outside)
    except OSError:
        pytest.skip("Symbolic links unavailable")
    with pytest.raises(ValueError, match="Unsafe"):
        await prepare_child(parent, "alias")
    assert load_child(parent, "alias").record["state"] == "needs_review"


async def test_limits_and_planning_fail_without_shared_fallback(parent, monkeypatch):
    (parent / "big.txt").write_text("12345")
    monkeypatch.setattr("minimax_code.workspace.child.MAX_FILE_BYTES", 4)
    with pytest.raises(ValueError, match="exceeds"):
        await prepare_child(parent, "large")
    with execution_scope(ExecutionPolicy(workspace=parent, read_only=True)):
        with pytest.raises(PermissionError):
            await prepare_child(parent, "plan")
    assert (parent / "big.txt").read_text() == "12345"


async def test_runtime_boundary_and_artifact_handoff(parent):
    from minimax_code.agent.tools.artifacts import artifact_dir
    from minimax_code.agent.tools.sandbox import CollectSubagentTool
    from minimax_code.workspace_ctx import reset_current_run_id, set_current_run_id

    class Core:
        async def run(self, **kwargs):
            assert current_root() != parent
            (current_root() / "result.txt").write_text("result")
            report = artifact_dir("run_real")
            assert report == parent / ".minimax/artifacts/run_real"
            report.mkdir(parents=True)
            (report / "COMPLETION.md").write_text("done")
            return SimpleNamespace(final_text="done", iterations=1, tool_calls=[], usage={},
                                   cancelled=False, truncated=False)

    runtime = SubAgentRuntime(llm=object())
    handle = SubAgentHandle(agent_id="writer", config=SubAgentConfig(name="writer"), core=Core())
    token = set_current_run_id("run_real")
    try:
        envelope = await runtime.invoke(handle, session_id="s", request="write")
    finally:
        reset_current_run_id(token)
    assert envelope["workspace"]["run_id"] == "run_real"
    assert envelope["files_written"] == ["result.txt"]
    assert not (parent / "result.txt").exists()
    collected = await CollectSubagentTool().run(run_id="run_real")
    assert collected.success
    assert (parent / "result.txt").read_text() == "result"


async def test_runtime_cancellation_finishes_workspace_before_return(parent):
    started = asyncio.Event()

    class Core:
        async def run(self, **kwargs):
            (current_root() / "partial.txt").write_text("partial")
            started.set()
            await asyncio.Event().wait()

    runtime = SubAgentRuntime(llm=object())
    handle = SubAgentHandle(agent_id="writer", config=SubAgentConfig(name="writer"), core=Core())
    task = asyncio.create_task(runtime.invoke(handle, session_id="s", request="write"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    manifests = list((parent / ".minimax/child-workspaces").glob("*/manifest.json"))
    record = json.loads(manifests[0].read_text())
    assert record["state"] == "cancelled"
    assert record["changes"] == ["partial.txt"]
    assert not (parent / "partial.txt").exists()


async def test_sensitive_inputs_and_dependency_trees_are_not_copied(parent):
    (parent / ".env.local").write_text("private")
    (parent / "token.key").write_text("private")
    (parent / "node_modules").mkdir()
    (parent / "node_modules/dep.js").write_text("dependency")
    (parent / "package-lock.json").write_text("lock")
    child = await prepare_child(parent, "inputs")
    assert not (child.root / ".env.local").exists()
    assert not (child.root / "token.key").exists()
    assert not (child.root / "node_modules").exists()
    assert (child.root / "package-lock.json").exists()
