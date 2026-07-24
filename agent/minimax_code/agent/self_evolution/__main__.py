"""CLI for the self-evolution trajectory collector (R309, Layer 2).

Run modes (mutually exclusive):

* ``--once``         — collect one trajectory snapshot now and write the
                       Markdown report under ``progress/self-evolution-reports/``.
* ``--install``      — insert an opt-in ``scheduled_jobs`` row so the
                       agent process collects a trajectory on cron. The
                       dispatcher wired in
                       :func:`minimax_code.scheduler.get_scheduler` is
                       what actually runs the collection on fire; this
                       command only persists the row.
* ``--report <DATE>`` — print a previously-written ``<date>.md`` report
                       to stdout (``YYYY-MM-DD``).

This module is intentionally thin. Every real behaviour lives in
:mod:`runner` and :mod:`payload`; the CLI is a surface, not a second
implementation.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from .runner import run_once

# Daily collection window. Off the :00/:30 mark (every agent that asks
# for "9am" lands on ``0 9``) so a fleet of installs does not stampede
# the same toolchain at once. Locally overridable via ``--cron``.
DEFAULT_CRON = "17 9 * * *"

# Stable name for the installed row. The dispatcher keys on the payload
# (``self_evolution: True``), not on this name, so renaming is safe.
JOB_NAME = "self-evolution-daily"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m minimax_code.agent.self_evolution",
        description="Self-evolution trajectory collector (Layer 2).",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--once",
        action="store_true",
        help="Collect one trajectory snapshot now and write the report.",
    )
    mode.add_argument(
        "--install",
        action="store_true",
        help="Insert a daily scheduled_jobs row that triggers collection on cron.",
    )
    mode.add_argument(
        "--report",
        metavar="DATE",
        help="Print a previously-written report (YYYY-MM-DD) to stdout.",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="Working tree to collect (default: current directory).",
    )
    parser.add_argument(
        "--cron",
        default=DEFAULT_CRON,
        help=(
            "Cron expression for --install "
            f"(default: {DEFAULT_CRON!r}, ~9am local)."
        ),
    )
    parser.add_argument(
        "--report-dir",
        default=None,
        help=(
            "Directory for reports "
            "(default: <cwd>/progress/self-evolution-reports)."
        ),
    )
    return parser.parse_args(argv)


def _resolve_cwd(arg: str | None) -> str:
    """Normalise the ``--cwd`` argument to an absolute path."""
    return str(Path(arg).resolve()) if arg else os.getcwd()


def _resolve_report_dir(cwd: str, arg: str | None) -> Path:
    return Path(arg) if arg else Path(cwd) / "progress" / "self-evolution-reports"


async def _run_once(cwd: str, report_dir: Path) -> Path:
    report = await run_once(cwd)
    return report.write_to(report_dir)


async def _install(cwd: str, cron_expr: str) -> dict:
    """Persist the opt-in scheduled_jobs row without starting the scheduler.

    We construct :class:`~minimax_code.scheduler.JobScheduler` directly
    (no ``start()``): ``add_job`` still writes the DB row + next-run, but
    skips the APScheduler registration because ``self._started`` is
    False. The running agent process picks the row up on its next
    ``_reload_from_db`` (boot or scheduler rebuild) — that is where the
    dispatcher wired in :func:`~minimax_code.scheduler.get_scheduler`
    fires the actual collection.
    """
    from ...app import ensure_db
    from ...scheduler import JobScheduler

    db = await ensure_db()
    if db is None:
        raise SystemExit(
            "storage unavailable (MINIMAX_CODE_NO_DB=1); cannot install job"
        )
    sched = JobScheduler(db)
    try:
        return await sched.add_job(
            name=JOB_NAME,
            cron_expr=cron_expr,
            payload={"self_evolution": True, "cwd": cwd},
            enabled=True,
        )
    finally:
        # stop() is a no-op when the scheduler never started, but call
        # it for symmetry so future lifecycle changes stay safe.
        await sched.stop()


def _print_report(report_dir: Path, date: str) -> int:
    path = report_dir / f"{date}.md"
    if not path.exists():
        print(f"report not found: {path}", file=sys.stderr)
        return 1
    sys.stdout.write(path.read_text(encoding="utf-8"))
    return 0


def amain(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cwd = _resolve_cwd(args.cwd)
    report_dir = _resolve_report_dir(cwd, args.report_dir)

    if args.report is not None:
        return _print_report(report_dir, args.report)
    if args.once:
        path = asyncio.run(_run_once(cwd, report_dir))
        print(f"wrote {path}")
        return 0
    if args.install:
        row = asyncio.run(_install(cwd, args.cron))
        print(
            f"installed self-evolution job id={row.get('id')} "
            f"name={JOB_NAME!r} cron={args.cron!r}"
        )
        print(f"  cwd={cwd}")
        print(
            "  the agent process runs the collection on the next cron fire "
            "(scheduler.get_scheduler wires the dispatcher at boot)."
        )
        return 0
    return 0  # unreachable — mutually exclusive required group


def main() -> None:
    sys.exit(amain())


if __name__ == "__main__":
    main()
