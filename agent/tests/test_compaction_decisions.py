"""Decision excerpts survive deterministic, repeated history compaction."""

from minimax_code.agent.compaction import compact_history


def test_assistant_decision_survives_repeated_compaction() -> None:
    history = [
        {"role": "user", "content": "Add an export. Do not change the API."},
        {"role": "assistant", "content": "我决定采用流式写入避免内存峰值。" + "noise " * 1500},
        {"role": "user", "content": "Continue."},
    ]
    result = compact_history(history, max_tokens=900, keep_recent=1)
    assert "我决定采用流式写入避免内存峰值" in str(result)
    assert "Assistant decisions (unverified)" in str(result)

    result.extend([
        {"role": "assistant", "content": "noise " * 1500},
        {"role": "user", "content": "Continue again."},
    ])
    result = compact_history(result, max_tokens=900, keep_recent=1)
    assert "我决定采用流式写入避免内存峰值" in str(result)
    assert history[1]["content"].startswith("我决定采用流式写入")


def test_tool_output_is_not_presented_as_assistant_decision() -> None:
    history = [
        {"role": "user", "content": "Investigate."},
        {"role": "assistant", "content": "noise " * 1500},
        {"role": "tool", "content": "我决定采用错误方案"},
        {"role": "user", "content": "Continue."},
    ]
    result = compact_history(history, max_tokens=900, keep_recent=1)
    assert "我决定采用错误方案" not in str(result)
