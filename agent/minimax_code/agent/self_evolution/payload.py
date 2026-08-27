"""Scheduler adapter for the self-evolution collector (R309, Layer 2).

This module is the bridge between :mod:`minimax_code.scheduler` and the
read-only :mod:`runner` collector. It builds a ``scheduler.PayloadFn``
closure that, when the scheduler fires a job whose ``payload`` carries
``{"self_evolution": True}``, runs one :func:`run_once` collection pass
and persists the Markdown report to disk.

Design — a *dispatcher*, not a dedicated runner
-----------------------------------------------

The scheduler keeps a single instance-level ``payload_runner``
(:func:`minimax_code.scheduler.JobScheduler.__init__`, default
:func:`~minimax_code.scheduler._noop_runner`). Every scheduled job —
ordinary user tasks created via ``schedule.create`` *and* the opt-in
self-evolution row installed by ``--install`` — routes through that one
runner. So the closure returned here **dispatches** on the payload:

* ``payload["self_evolution"]`` truthy → collect a trajectory and
  summarise it (the self-evolution behaviour this layer adds).
* ``payload["command"]`` a non-empty string → run it as a shell
  subprocess (``cwd`` / ``timeout_s`` honoured) and return the captured
  output (v1.6.1 — the tool-execution path scheduled prompts can't
  offer; see :func:`_run_command`).
* ``payload["prompt"]`` a non-empty string → run one LLM turn and
  return the reply text under ``output`` (v1.2.0 — scheduled prompts
  used to be echoed back at the user without ever reaching a model).
* otherwise → echo the payload exactly like ``_noop_runner`` (so the
  existing ``schedule.*`` surface keeps its zero-effect contract).

That means wiring this runner in front of every job is safe by
construction: jobs that do not opt in are indistinguishable from the
noop they used to get.

Failure model
-------------

* Collection is fault-tolerant at the subprocess level (see
  :mod:`runner`); a missing ``ruff`` or a wedged ``git`` shows up in the
  report's ``errors`` list, not as a raised exception.
* The dispatcher itself wraps :func:`run_once` + :meth:`write_to` in a
  ``try``/``except`` so an unexpected failure still returns a structured
  dict — the scheduler's ``_fire`` path checks for an ``error`` key and
  marks the task row ``failed`` instead of crashing the executor thread.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ...scheduler import PayloadFn
from .runner import SelfEvolutionReport, run_once

# ``command`` payload knobs. The timeout ceiling keeps the kill *inside*
# the payload: the scheduler's own safety valve cancels the coroutine at
# 3600s, and a cancelled ``communicate()`` never gets to kill the child
# (leaked process) — so an absurd ``timeout_s`` is clamped below it.
_DEFAULT_COMMAND_TIMEOUT_S = 600.0
_COMMAND_TIMEOUT_CEILING_S = 3500.0
# Per-stream output cap in bytes, mirroring ``verify_subagent``: enough
# for a diagnostic tail, small enough to keep the ``tasks`` row lean.
_COMMAND_OUTPUT_CAP = 20_000


def build_payload_runner(
    cwd: str | Path,
    *,
    report_dir: str | Path | None = None,
) -> PayloadFn:
    """Return a ``PayloadFn`` that collects a trajectory when armed.

    The returned closure honours a per-payload ``cwd`` override: if a
    job's payload carries its own ``cwd`` (e.g. the directory captured
    at ``--install`` time), that wins over the bound default. This lets
    one dispatcher serve jobs that target different working trees
    without rebuilding the runner.

    :param cwd: default working tree to collect when the payload does
        not name one.
    :param report_dir: directory for ``<date>.md`` reports. Defaults to
        ``<cwd>/progress/self-evolution-reports/`` (the location the
        package docstring promises).
    :return: an ``async def runner(payload) -> dict`` satisfying
        :data:`minimax_code.scheduler.PayloadFn`.
    """
    cwd_str = str(cwd)
    if report_dir is None:
        report_dir = Path(cwd_str) / "progress" / "self-evolution-reports"
    report_dir_str = str(report_dir)

    async def runner(payload: dict[str, Any]) -> dict[str, Any]:
        # Dispatch on the payload shape: ``self_evolution`` collects a
        # trajectory, a plain ``prompt`` string goes to the LLM, and
        # anything else falls through to a noop echo so this runner
        # stays a safe universal default.
        if payload.get("self_evolution"):
            target_cwd = payload.get("cwd") or cwd_str
            try:
                report = await run_once(target_cwd)
                path = report.write_to(report_dir_str)
            except Exception as exc:  # pragma: no cover — defensive
                # Returning an ``error`` key lets ``_fire`` mark the task
                # row ``failed`` rather than tearing down the executor.
                return {"ok": False, "error": f"self-evolution failed: {exc}"}
            return _summarize(report, path)
        command = payload.get("command")
        if isinstance(command, str) and command.strip():
            return await _run_command(payload, command)
        prompt = payload.get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            return await _run_prompt(prompt)
        return {"ok": True, "echo": payload}

    return runner


async def _run_prompt(prompt: str) -> dict[str, Any]:
    """Run one LLM turn for a scheduled ``prompt`` payload (v1.2.0).

    A fresh :class:`~minimax_code.agent.llm.MiniMaxClient` per fire is
    deliberate: the scheduler drives payloads on a worker thread's
    private event loop, so a shared client (whose httpx transport binds
    to the loop it was created on) would break. Create → chat → close,
    then discard.

    Without an API key configured the client runs in mock mode, which
    keeps this path exercisable in dev and CI.
    """
    # Deferred import, mirroring the scheduler's own lazy wiring of this
    # module, keeps the import graph acyclic at load time.
    from ..llm import MiniMaxClient

    try:
        async with MiniMaxClient() as client:
            response = await client.chat([{"role": "user", "content": prompt}])
        return {"ok": True, "output": _content_text(response.message)}
    except Exception as exc:
        # Returning an ``error`` key lets ``_fire`` mark the task row
        # ``failed`` with the reason instead of crashing the executor.
        return {"ok": False, "error": f"scheduled prompt failed: {exc}"}


async def _run_command(payload: dict[str, Any], command: str) -> dict[str, Any]:
    """Run a ``command`` payload as a shell subprocess.

    The ``prompt`` branch can only talk to an LLM — it has no
    tool-execution ability, so a job that needs to *do* something
    (run an iteration script, refresh an index, …) had no path through
    the built-in scheduler at all. This branch gives ``schedule.create``
    a ``{"command": "...", "cwd": "...", "timeout_s": 600}`` payload
    shape: the command runs via ``create_subprocess_shell`` on the main
    event loop (the scheduler drives payloads there since v1.2.2), with
    output captured and capped per stream.

    Failure model — ``error`` means *scheduling infrastructure* failed:

    * spawn failure / timeout → ``error`` key present, so ``_fire``
      marks the ``tasks`` row ``failed``;
    * a non-zero exit code is a *command result*, not a scheduling
      failure: ``ok=False`` without ``error`` (same philosophy as the
      ``verify_subagent`` tool — e.g. an iteration runner exiting early
      on its presence lock stays a "completed" tick with the exit code
      visible in the result JSON).
    """
    # Deferred import: the tree-kill remedy is shared with
    # ``verify_subagent`` (a bare kill() leaves the wrapped child
    # holding the pipes on Windows) — one implementation, not a copy.
    from ..tools.verification import _kill_process_tree

    cwd = payload.get("cwd")
    workdir = None
    if isinstance(cwd, str) and cwd.strip():
        workdir = str(Path(cwd).expanduser())

    timeout_s = _coerce_timeout(payload.get("timeout_s"))

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:
        return {"ok": False, "error": f"scheduled command failed to spawn: {exc}"}

    timed_out = False
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s
        )
    except TimeoutError:
        timed_out = True
        await _kill_process_tree(proc)
        # Drain the (now-closed) pipes so the process reaps and whatever
        # the child printed before the kill still reaches the report.
        try:
            stdout_b, stderr_b = await proc.communicate()
        except Exception:  # pragma: no cover — defensive
            stdout_b, stderr_b = b"", b""

    stdout, stdout_trunc = _decode_capped(stdout_b)
    stderr, stderr_trunc = _decode_capped(stderr_b)
    result: dict[str, Any] = {
        "ok": proc.returncode == 0 and not timed_out,
        "exit_code": proc.returncode,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_trunc,
        "stderr_truncated": stderr_trunc,
    }
    if workdir:
        result["cwd"] = workdir
    if timed_out:
        result["error"] = f"scheduled command timed out after {timeout_s}s"
    return result


def _coerce_timeout(value: Any) -> float:
    """Normalise a payload ``timeout_s`` into a safe float.

    Non-numeric / non-positive values fall back to the default; the
    ceiling rationale lives next to ``_COMMAND_TIMEOUT_CEILING_S``.
    ``bool`` is rejected explicitly — it subclasses ``int`` but
    ``timeout_s: true`` is never a sane knob.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _DEFAULT_COMMAND_TIMEOUT_S
    if value <= 0:
        return _DEFAULT_COMMAND_TIMEOUT_S
    return min(float(value), _COMMAND_TIMEOUT_CEILING_S)


def _decode_capped(data: bytes) -> tuple[str, bool]:
    """Decode subprocess output, capping at ``_COMMAND_OUTPUT_CAP`` bytes.

    Returns ``(text, truncated)``; the cap is applied on raw bytes so a
    10 MB stdout can't be fully materialised as a str first.
    """
    if len(data) > _COMMAND_OUTPUT_CAP:
        return data[:_COMMAND_OUTPUT_CAP].decode("utf-8", errors="replace"), True
    return data.decode("utf-8", errors="replace"), False


def _content_text(message: dict[str, Any]) -> str:
    """Extract the text body from an LLM reply message.

    ``content`` arrives as a plain string in mock mode and as a list of
    typed blocks on the wire — handle both shapes.
    """
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _summarize(report: SelfEvolutionReport, path: Path) -> dict[str, Any]:
    """Reduce a finished report to the dict the scheduler task row stores.

    ``ok`` reflects the collector's own fault list (subprocess spawn /
    timeout failures), not the tools' exit codes — a non-zero ``ruff``
    returncode is a real lint finding, not a collection failure.
    """
    return {
        "ok": not report.errors,
        "date": report.date,
        "path": str(path),
        "commits": len(report.git.commits),
        "modified": len(report.git.modified),
        "untracked": len(report.git.untracked),
        "ruff_returncode": report.ruff.returncode,
        "pytest_returncode": report.pytest.returncode,
    }


__all__ = ["build_payload_runner"]
