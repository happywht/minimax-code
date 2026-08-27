"""Tests for the scheduler ``command`` payload branch (v1.6.1).

The dispatcher (:func:`build_payload_runner`) gained a third branch:
``payload["command"]`` runs a shell subprocess — the tool-execution
path the ``prompt`` branch (a bare LLM chat) structurally cannot
offer. Scheduled jobs that need to *do* something (an iteration
script, an index refresh, …) previously had no way through the
built-in scheduler.

These tests drive the real closure over real subprocesses, plus unit
pins on the timeout coercion matrix and the dispatch precedence
(``command`` wins over a co-resident ``prompt`` key).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from minimax_code.agent.self_evolution import build_payload_runner
from minimax_code.agent.self_evolution import payload as payload_mod


def _runner(tmp_path: Path):
    return build_payload_runner(str(tmp_path), report_dir=tmp_path / "reports")


def _py(command: str) -> str:
    # Quote the interpreter — tmp paths can contain spaces.
    return f'"{sys.executable}" {command}'


# ---------------------------------------------------------------------------
# live subprocess runs
# ---------------------------------------------------------------------------


async def test_command_success(tmp_path: Path) -> None:
    result = await _runner(tmp_path)({"command": _py('-c "print(\'hello-sched\')"')})
    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert result["timed_out"] is False
    assert "hello-sched" in result["stdout"]
    assert "error" not in result


async def test_command_nonzero_exit_is_result_not_error(tmp_path: Path) -> None:
    """A non-zero exit is a *command result*: ``ok=False`` but no
    ``error`` key, so ``_fire`` keeps the tasks row ``completed`` with
    the exit code visible in its result JSON (same philosophy as
    ``verify_subagent``)."""
    result = await _runner(tmp_path)({"command": _py("-c \"import sys; sys.exit(3)\"")})
    assert result["ok"] is False
    assert result["exit_code"] == 3
    assert "error" not in result


async def test_command_timeout_kills_and_reports(tmp_path: Path) -> None:
    """Timeout → ``timed_out`` + ``error`` (task row goes ``failed``),
    and the tree-kill must actually reap the child — without it the
    wrapped ``sleep`` holds the pipes and the drain ``communicate()``
    blocks for the child's full runtime."""
    import time

    started = time.monotonic()
    result = await _runner(tmp_path)(
        {
            "command": _py('-c "import time; time.sleep(30)"'),
            "timeout_s": 1,
        }
    )
    elapsed = time.monotonic() - started
    assert result["ok"] is False
    assert result["timed_out"] is True
    assert "timed out" in result["error"]
    # Tree-kill pin: a leaked child would hold us near 30s.
    assert elapsed < 15, f"drain took {elapsed:.1f}s — tree-kill regression?"


async def test_command_stderr_capture(tmp_path: Path) -> None:
    result = await _runner(tmp_path)(
        {"command": _py('-c "import sys; sys.stderr.write(\'boom\\n\')"')}
    )
    assert result["ok"] is True
    assert "boom" in result["stderr"]


async def test_command_stdout_truncated(tmp_path: Path) -> None:
    cap = payload_mod._COMMAND_OUTPUT_CAP
    result = await _runner(tmp_path)(
        {"command": _py(f"-c \"print('x' * {cap * 2})\"")}
    )
    assert result["ok"] is True
    assert result["stdout_truncated"] is True
    assert len(result["stdout"]) <= cap
    assert result["stderr_truncated"] is False


async def test_command_cwd_override(tmp_path: Path) -> None:
    """``cwd`` reruns the subprocess; asserted via a marker file (not by
    string-comparing paths, which 8.3 short names / case make brittle)."""
    result = await _runner(tmp_path)(
        {
            "command": _py("-c \"open('marker.txt', 'w').write('x'); print('done')\""),
            "cwd": str(tmp_path),
        }
    )
    assert result["ok"] is True, result
    assert result["cwd"] == str(tmp_path)
    assert (tmp_path / "marker.txt").read_text(encoding="utf-8") == "x"


async def test_command_invalid_timeout_falls_back_to_default(
    tmp_path: Path,
) -> None:
    """A garbage ``timeout_s`` must not break the run — the default
    (600s) kicks in and a short command completes normally."""
    result = await _runner(tmp_path)(
        {"command": _py('-c "print(\'ok\')"'), "timeout_s": "not-a-number"}
    )
    assert result["ok"] is True
    assert "ok" in result["stdout"]


# ---------------------------------------------------------------------------
# timeout coercion matrix (unit)
# ---------------------------------------------------------------------------


def test_coerce_timeout_matrix() -> None:
    coerce = payload_mod._coerce_timeout
    assert coerce("not-a-number") == payload_mod._DEFAULT_COMMAND_TIMEOUT_S
    assert coerce(None) == payload_mod._DEFAULT_COMMAND_TIMEOUT_S
    assert coerce(-5) == payload_mod._DEFAULT_COMMAND_TIMEOUT_S
    assert coerce(0) == payload_mod._DEFAULT_COMMAND_TIMEOUT_S
    # bool subclasses int — ``timeout_s: true`` is never a sane knob.
    assert coerce(True) == payload_mod._DEFAULT_COMMAND_TIMEOUT_S
    # Ceiling: the scheduler's own valve cancels at 3600s without
    # killing the child, so absurd values clamp just below it.
    assert coerce(99_999) == payload_mod._COMMAND_TIMEOUT_CEILING_S
    assert coerce(30) == 30.0
    assert coerce(2.5) == 2.5


# ---------------------------------------------------------------------------
# dispatch precedence + fall-through (no subprocess involved)
# ---------------------------------------------------------------------------


async def test_command_takes_precedence_over_prompt(
    tmp_path: Path, monkeypatch
) -> None:
    """When both keys ride the same payload, ``command`` wins. The
    prompt branch is stubbed so no LLM call can escape the test."""
    calls: list[str] = []

    async def fake_prompt(prompt: str) -> dict[str, Any]:
        calls.append(prompt)
        return {"ok": True, "output": "should not be reached"}

    monkeypatch.setattr(payload_mod, "_run_prompt", fake_prompt)

    result = await _runner(tmp_path)(
        {"command": _py('-c "print(\'cmd-wins\')"'), "prompt": "hello llm"}
    )
    assert "cmd-wins" in result["stdout"]
    assert calls == []


async def test_command_whitespace_falls_to_echo(tmp_path: Path) -> None:
    payload = {"command": "   "}
    assert await _runner(tmp_path)(payload) == {"ok": True, "echo": payload}


async def test_command_non_string_falls_to_echo(tmp_path: Path) -> None:
    payload = {"command": 123}
    assert await _runner(tmp_path)(payload) == {"ok": True, "echo": payload}
