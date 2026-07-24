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
  summarise it (the new behaviour this layer adds).
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

from pathlib import Path
from typing import Any

from ...scheduler import PayloadFn
from .runner import SelfEvolutionReport, run_once


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
        # Dispatch on the opt-in flag. Payloads without it fall through
        # to a noop echo so this runner is a safe universal default.
        if not payload.get("self_evolution"):
            return {"ok": True, "echo": payload}
        target_cwd = payload.get("cwd") or cwd_str
        try:
            report = await run_once(target_cwd)
            path = report.write_to(report_dir_str)
        except Exception as exc:  # pragma: no cover — defensive
            # Returning an ``error`` key lets ``_fire`` mark the task row
            # ``failed`` rather than tearing down the executor thread.
            return {"ok": False, "error": f"self-evolution failed: {exc}"}
        return _summarize(report, path)

    return runner


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
