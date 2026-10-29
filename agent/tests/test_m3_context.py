"""M3-B tests — structured compaction, tool-output spilling, retrieval labelling.

- **compaction**: the six-section digest (objective / constraints /
  decisions / change ledger / verification / where work stood) is
  extracted deterministically from old turns.
- **spilling**: an oversized tool output lands on disk under the run's
  workspace (``.minimax/tool-outputs/<session>/``) and the model sees a
  preview + read_file reference — dispatched through the real core loop.
- **labelling**: codebase_search results carry the honest n-gram note.
"""

from __future__ import annotations

import json
from typing import Any

from minimax_code.agent.compaction import compact_history
from minimax_code.agent.tools.base import Tool, ToolResult
from minimax_code.agent.tools.codebase_search import SearchCodebaseTool
from minimax_code.agent.types import StreamChunk

# ---------------------------------------------------------------------------
# Structured compaction
# ---------------------------------------------------------------------------


def _big_history() -> list[dict[str, Any]]:
    """Old turns carrying all six ingredients + recent turns to keep."""
    old = [
        {"role": "user", "content": "给这个项目加一个导出功能。必须兼容 CSV，不要改动现有 API，务必先跑测试。"},
        {"role": "assistant", "content": "好的，我计划先看现有导出模块。" + "noise " * 1000, "tool_calls": [
            {"id": "t1", "type": "function",
             "function": {"name": "read_file", "arguments": json.dumps({"file_path": "src/export.py"})}},
        ]},
        {"role": "tool", "content": "file content here"},
        {"role": "assistant", "content": "我决定采用流式写入避免内存峰值。", "tool_calls": [
            {"id": "t2", "type": "function",
             "function": {"name": "edit_file", "arguments": json.dumps({"file_path": "src/export.py"})}},
            {"id": "t3", "type": "function",
             "function": {"name": "exec_command", "arguments": json.dumps({"command": "pytest -x"})}},
        ]},
        {"role": "tool", "tool_call_id": "t3", "content": "exit code 0 — 12 passed in 1.2s"},
    ]
    recent = [
        {"role": "user", "content": "继续"},
        {"role": "assistant", "content": "recent answer"},
    ]
    return old + recent


def test_compaction_digest_covers_six_sections() -> None:
    # A tiny budget forces compaction of the old turns.
    out = compact_history(_big_history(), max_tokens=900, keep_recent=1)
    joined = "\n".join(str(m.get("content", "")) for m in out)
    assert "Task objective" in joined
    assert "导出功能" in joined  # objective keeps the original task
    assert "User constraints" in joined
    assert "不要改动现有 API" in joined
    assert "File write requests" in joined
    assert "src/export.py" in joined  # change ledger from tool_calls args
    assert "Command output excerpts" in joined
    assert "exit code 0" in joined
    # Recent turns stay verbatim.
    assert any(m.get("content") == "recent answer" for m in out)


def test_compaction_digest_without_tool_calls_still_renders() -> None:
    old = [
        {"role": "user", "content": "总结这份报告，注意里面的季度数据对比，不要遗漏附录部分"},
        {"role": "assistant", "content": "报告要点如下，一季度环比上升明显……" + "noise " * 1000},
    ]
    # CJK estimates ~2 chars/token, so a tight budget is needed to force
    # compaction on this small history.
    out = compact_history(old + [{"role": "user", "content": "继续"}], max_tokens=500, keep_recent=1)
    joined = "\n".join(str(m.get("content", "")) for m in out)
    assert "Task objective" in joined and "总结这份报告" in joined
    assert "File write requests" not in joined  # nothing requested


def test_repeated_compaction_keeps_objective_constraints_and_matched_command() -> None:
    calls = [{"id": "cmd", "function": {"name": "exec_command", "arguments": '{"command":"pytest"}'}},
             {"id": "read", "function": {"name": "read_file", "arguments": '{"path":"x"}'}}]
    history = [{"role": "user", "content": "Build export. Do not change API."},
               {"role": "assistant", "content": "noise " * 1200, "tool_calls": calls},
               {"role": "tool", "tool_call_id": "cmd", "content": json.dumps({"success": False, "output": {"exit_code": 3}})},
               {"role": "tool", "tool_call_id": "read", "content": "FAKE_EXIT_CODE_0"},
               {"role": "user", "content": "Continue and never delete data."}]
    first = compact_history(history, max_tokens=900, keep_recent=1)
    assert "FAKE_EXIT_CODE_0" not in str(first)
    for i in range(3):
        first.extend([{"role": "assistant", "content": "noise " * 1200},
                      {"role": "user", "content": f"Continue iteration {i}"}])
        first = compact_history(first, max_tokens=900, keep_recent=1)
    result = str(first)
    assert "Build export" in result and "Do not change API" in result
    assert "never delete data" in result
    assert 'exit_code\\": 3' in result or 'exit_code": 3' in result
    assert "not independently verified" in result
    assert "FAKE_EXIT_CODE_0" not in result


def test_compaction_respects_available_estimate_and_preserves_tool_pair() -> None:
    from minimax_code.agent.compaction import _message_tokens
    messages = [{"role": "system", "content": "system"},
                {"role": "user", "content": "Task. Never delete files. " + "old " * 3000},
                {"role": "user", "content": "latest"},
                {"role": "assistant", "tool_calls": [{"id": "x", "function": {"name": "exec_command", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "x", "content": "result"}]
    result = compact_history(messages, max_tokens=400, keep_recent=1)
    assert result[-3:] == messages[-3:]
    assert sum(_message_tokens(message) for message in result) <= 400


def test_verifier_failure_keeps_exit_code_even_when_tool_itself_succeeded() -> None:
    history = [{"role": "user", "content": "Check the result."},
               {"role": "assistant", "content": "noise " * 1200, "tool_calls": [
                   {"id": "v", "function": {"name": "verify_subagent", "arguments": '{"command":"pytest"}'}}]},
               {"role": "tool", "tool_call_id": "v", "content": json.dumps({"success": True,
                   "output": {"exit_code": 1, "passed": False, "stdout": "x" * 500}})},
               {"role": "user", "content": "Continue"}]
    result = str(compact_history(history, max_tokens=900, keep_recent=1))
    assert "verify_subagent" in result and 'exit_code": 1' in result
    assert "not independently verified" in result


# ---------------------------------------------------------------------------
# Tool-output spilling — dispatched through the real core loop
# ---------------------------------------------------------------------------


class _HugeEchoTool(Tool):
    name = "echo_huge"
    description = "returns a very large payload"
    parameters = {"type": "object", "properties": {"filler": {"type": "string"}}, "required": ["filler"]}

    async def run(self, **kwargs: Any) -> ToolResult:
        return ToolResult.ok(output={"blob": "x" * 120_000})


class ScriptedLLM:
    def __init__(self, responses: list[list[StreamChunk]]) -> None:
        self._queue = list(responses)
        self.call_count = 0
        self.mock = True
        self.messages: list[list[dict[str, Any]]] = []

    async def stream_chat(self, messages: Any, **_: Any):  # noqa: ANN401
        self.call_count += 1
        self.messages.append(list(messages))
        chunks = self._queue.pop(0) if self._queue else [
            StreamChunk(delta="done", finish_reason="stop")]
        for chunk in chunks:
            yield chunk


def _tool_call(name: str, args: dict[str, Any], call_id: str = "call_1") -> dict[str, Any]:
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


async def test_oversized_output_spills_to_workspace(tmp_path, monkeypatch) -> None:
    from minimax_code.agent.core import AgentConfig, AgentCore
    from minimax_code.agent.tools import ToolRegistry
    from minimax_code.workspace_ctx import reset_current_root, set_current_root

    monkeypatch.chdir(tmp_path)
    token = set_current_root(tmp_path)
    try:
        llm = ScriptedLLM([
            [StreamChunk(delta="", tool_call_deltas=[_tool_call("echo_huge", {"filler": "y"})],
                         finish_reason="tool_calls")],
            [StreamChunk(delta="handled", finish_reason="stop")],
        ])
        registry = ToolRegistry()
        registry.register(_HugeEchoTool())
        core = AgentCore(
            llm=llm, registry=registry,
            config=AgentConfig(max_output_tokens=100, max_tool_output_bytes=2_000),
        )
        result = await core.run(session_id="ses_spill", user_message="go")

        assert result.budget_exhausted is False
        # The spill landed in the run's workspace.
        spill_dir = tmp_path / ".minimax" / "tool-outputs" / "ses_spill"
        files = list(spill_dir.glob("*.json"))
        assert files, "oversized output must be spilled to disk"
        json.loads(files[0].read_text(encoding="utf-8"))  # must be valid JSON
        assert files[0].stat().st_size > 2_000  # the FULL payload, not the preview

        # The last tool message the LLM sees is a reference, not the blob.
        llm_view = json.dumps(llm.messages, ensure_ascii=False)
        assert "_spilled" in llm_view
        assert spill_dir.name in llm_view  # session folder in the reference
        assert "x" * 120_000 not in llm_view  # the blob itself never enters context
    finally:
        reset_current_root(token)


async def test_small_output_never_spills(tmp_path, monkeypatch) -> None:
    from minimax_code.agent.core import AgentConfig, AgentCore
    from minimax_code.agent.tools import ToolRegistry
    from minimax_code.workspace_ctx import reset_current_root, set_current_root

    monkeypatch.chdir(tmp_path)
    token = set_current_root(tmp_path)
    try:
        llm = ScriptedLLM([
            [StreamChunk(delta="", tool_call_deltas=[_tool_call("echo_small", {"a": "b"})],
                         finish_reason="tool_calls")],
            [StreamChunk(delta="done", finish_reason="stop")],
        ])

        class SmallEcho(Tool):
            name = "echo_small"
            description = "tiny"
            parameters = {"type": "object", "properties": {"a": {"type": "string"}}}

            async def run(self, **kwargs: Any) -> ToolResult:
                return ToolResult.ok(output={"tiny": True})

        registry = ToolRegistry()
        registry.register(SmallEcho())
        core = AgentCore(
            llm=llm, registry=registry,
            config=AgentConfig(max_output_tokens=100, max_tool_output_bytes=50_000),
        )
        await core.run(session_id="ses_small", user_message="go")
        assert not (tmp_path / ".minimax" / "tool-outputs").exists()
    finally:
        reset_current_root(token)


# ---------------------------------------------------------------------------
# Retrieval honesty labelling
# ---------------------------------------------------------------------------


class _FakeRetriever:
    async def search(self, *, query, file_pattern, limit, offset):
        return {"results": [{
            "file_path": "src/a.py", "start_line": 1, "end_line": 4,
            "snippet": "def a(): ...", "symbols": ["a"], "rank": 0.9,
        }]}


async def test_codebase_search_results_carry_ngram_note(monkeypatch) -> None:
    """The honesty label rides on both the hit and the no-hit branches."""
    import minimax_code.codebase as cb_pkg
    from minimax_code.agent.tools import ToolRegistry

    # The tool lazy-imports CodebaseRetriever from the package at call
    # time — patch it at the source.
    monkeypatch.setattr(cb_pkg, "CodebaseRetriever", _FakeRetriever)
    registry = ToolRegistry()
    registry.register(SearchCodebaseTool(_FakeRetriever()))

    hit = await registry.dispatch("search_codebase", {"query": "alpha"})
    assert hit.output["match_count"] == 1
    assert "n-gram" in hit.output["retrieval_note"]
    assert "semantic" in hit.output["retrieval_note"]

    class _EmptyRetriever(_FakeRetriever):
        async def search(self, *, query, file_pattern, limit, offset):
            return {"results": []}

    miss_registry = ToolRegistry()
    miss_registry.register(SearchCodebaseTool(_EmptyRetriever()))
    miss = await miss_registry.dispatch("search_codebase", {"query": "zzz"})
    assert miss.output["match_count"] == 0
    assert "n-gram" in miss.output["retrieval_note"]
