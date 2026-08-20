"""Smoke for the startup benchmark harness (``tests/bench_startup.py``).

Pins the harness contract, NOT any wall-clock number — timings vary
by machine and would be flaky in CI. The assertions are shape-only:

- the script exits 0 after one round;
- ``--json`` mode emits a valid payload as the last stdout line;
- the payload carries at least one positive measurement plus summary
  stats (min/median/max agree with the rounds list).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BENCH = Path(__file__).with_name("bench_startup.py")


def test_bench_startup_runs_one_round_and_emits_valid_json() -> None:
    proc = subprocess.run(
        [sys.executable, str(BENCH), "--rounds", "1", "--timeout", "90", "--json"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, f"bench failed:\n{proc.stderr[-2000:]}"

    last_line = proc.stdout.strip().splitlines()[-1]
    payload = json.loads(last_line)
    assert payload["benchmark"] == "startup"
    assert payload["platform"] == sys.platform
    assert len(payload["rounds"]) == 1

    elapsed = payload["rounds"][0]["elapsed_s"]
    assert isinstance(elapsed, float)
    assert elapsed > 0
    assert payload["median_s"] == elapsed
    assert payload["min_s"] <= payload["median_s"] <= payload["max_s"]
