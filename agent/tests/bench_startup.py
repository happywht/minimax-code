#!/usr/bin/env python
"""Cold-start benchmark: agent process spawn -> ``GET /health`` returns ok.

Measures the wall-clock time from spawning ``python -m minimax_code``
to the first successful ``/health`` response with ``ok: true`` — the
number a user perceives as "the agent is up". Every round uses a
fresh temporary data directory, so each measurement is a true cold
start (imports + config + storage init + full migration chain +
uvicorn bind).

Usage
-----

    cd agent && uv run python tests/bench_startup.py            # 3 rounds, human-readable
    uv run python tests/bench_startup.py --rounds 5 --json      # machine-readable

The JSON payload (last stdout line in --json mode) is what R14 pins
into ``docs/performance-baseline.md``. Never assert on these numbers
in CI — wall clock varies by machine; the accompanying pytest smoke
(``tests/test_bench_startup.py``) only checks the harness emits a
valid measurement.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request

POLL_INTERVAL_S = 0.05
SHUTDOWN_TIMEOUT_S = 10.0


def find_free_port() -> int:
    """Grab an ephemeral port the OS guarantees is free right now."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_healthy(port: int, timeout_s: float) -> tuple[dict | None, str | None]:
    """Poll ``/health`` until ``ok: true`` or the deadline passes.

    Returns ``(body, None)`` on success, ``(None, last_error)`` on
    timeout. Connection errors during boot are expected and retried.
    """
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout_s
    last_err: str | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                if body.get("ok") is True:
                    return body, None
                last_err = f"health returned ok={body.get('ok')!r}"
        except Exception as exc:  # noqa: BLE001 — boot-time conn errors are expected
            last_err = repr(exc)
        time.sleep(POLL_INTERVAL_S)
    return None, last_err


def run_round(timeout_s: float) -> float:
    """One cold-start measurement. Raises RuntimeError on failure."""
    port = find_free_port()
    # Windows: after terminate() the kernel may release the SQLite file
    # handles a beat after wait() returns, which would make the temp-dir
    # cleanup race (WinError 32). Leftover files under %TEMP% are harmless;
    # the OS reaps them.
    with tempfile.TemporaryDirectory(
        prefix="minimax-bench-", ignore_cleanup_errors=True
    ) as data_dir:
        env = {
            **os.environ,
            # Isolate the DB: every round re-runs the full migration
            # chain on an empty directory — never touches the user profile.
            "MINIMAX_CODE_DATA_DIR": data_dir,
            "MINIMAX_CODE_LOG_LEVEL": "WARNING",
        }
        proc = subprocess.Popen(
            [sys.executable, "-m", "minimax_code", "--http-port", str(port)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        t0 = time.perf_counter()
        try:
            body, err = wait_healthy(port, timeout_s)
            elapsed = time.perf_counter() - t0
            if body is None:
                # Best-effort diagnostics: the child is either hung or
                # dead; capture its stderr tail for the error message.
                proc.terminate()
                try:
                    proc.wait(timeout=SHUTDOWN_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                tail = (proc.stderr.read() if proc.stderr else b"")[-2000:]
                raise RuntimeError(
                    f"agent did not become healthy within {timeout_s}s "
                    f"(last error: {err}); stderr tail:\n"
                    f"{tail.decode('utf-8', errors='replace')}"
                ) from None
            return elapsed
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=SHUTDOWN_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench_startup",
        description="Cold-start benchmark: spawn agent -> /health ok",
    )
    parser.add_argument("--rounds", type=int, default=3, help="measurement rounds (default 3)")
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="per-round health timeout in seconds"
    )
    parser.add_argument("--json", action="store_true", help="emit a JSON payload as the last line")
    args = parser.parse_args(argv)

    elapsed_list: list[float] = []
    rounds: list[dict] = []
    for i in range(args.rounds):
        elapsed = run_round(args.timeout)
        elapsed_list.append(elapsed)
        rounds.append({"round": i + 1, "elapsed_s": round(elapsed, 3)})
        print(f"round {i + 1}/{args.rounds}: {elapsed:.3f}s", file=sys.stderr)

    summary = {
        "benchmark": "startup",
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "rounds": rounds,
        "min_s": round(min(elapsed_list), 3),
        "median_s": round(statistics.median(elapsed_list), 3),
        "max_s": round(max(elapsed_list), 3),
    }
    if args.json:
        print(json.dumps(summary))
    else:
        print(
            f"cold start (spawn -> /health ok), {args.rounds} rounds: "
            f"min {summary['min_s']}s / median {summary['median_s']}s / "
            f"max {summary['max_s']}s"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
