"""End-to-end smoke test for the permission-store IPC layer.

Spawns the real ``python -m minimax_code`` agent as a subprocess
(in-process ``IPCClient`` is *not* used — it would skip the
``app._default_skills_root`` / DB-open path and miss integration
bugs the way the Phase 1 skills test did). All communication goes
through the stdio JSON-RPC channel.

Usage::

    python tests/e2e/smoke_permissions.py <python> <agent_dir> <workdir>

The script:

1. Spawns the agent, pointing it at a *scratch* data dir via
   ``MINIMAX_CODE_DATA_DIR`` so we never touch the real APPDATA path.
2. Sends ``permission.set`` to allow ``exec_command``.
3. Sends ``permission.check`` and asserts ``allowed=true``.
4. Sends ``permission.set`` to flip the same pattern to ``deny``.
5. Sends ``permission.check`` and asserts ``allowed=false``.
6. Shuts the agent down.
7. Re-spawns a fresh agent (same data dir).
8. Sends ``permission.check`` and asserts ``allowed=false`` (proves
   the rule survived a process restart).
9. Sends ``permission.delete`` and shuts down.

Exits 0 on success, non-zero on the first failed step. A small
summary is written to stdout at the end.

Run this from the repo root::

    python tests/e2e/smoke_permissions.py python agent .smoke_workdir/perms
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Tiny JSON-RPC client over stdio
# ---------------------------------------------------------------------------


class StdioJSONRPC:
    """Minimal line-delimited JSON-RPC client over subprocess stdio."""

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self._proc = proc
        self._counter = 0
        self._lock = asyncio.Lock()

    async def request(
        self, method: str, params: dict | None = None, *, timeout: float = 8.0
    ) -> dict:
        async with self._lock:
            self._counter += 1
            req_id = f"smoke-{method}-{self._counter}"
            req = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params or {},
            }
            line = json.dumps(req, ensure_ascii=False) + "\n"
            self._proc.stdin.write(line.encode("utf-8"))
            await self._proc.stdin.drain()

            deadline = time.time() + timeout
            events: list[dict] = []
            while time.time() < deadline:
                remaining = max(0.05, deadline - time.time())
                raw = await asyncio.wait_for(
                    self._proc.stdout.readline(), timeout=remaining
                )
                if not raw:
                    return {"_error": "stdout-closed", "_events": events}
                try:
                    obj = json.loads(raw.decode("utf-8").strip())
                except json.JSONDecodeError:
                    continue
                if obj.get("id") == req_id:
                    obj["_events"] = events
                    return obj
                if "event" in obj:
                    events.append(obj)
            return {"_error": "timeout", "_events": events}


# ---------------------------------------------------------------------------
# Spawn / shutdown helpers
# ---------------------------------------------------------------------------


async def spawn_agent(
    python: str, agent_dir: Path, workdir: Path
) -> tuple[asyncio.subprocess.Process, StdioJSONRPC]:
    """Boot the agent pointed at a scratch data dir."""
    workdir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(agent_dir)
    env["MINIMAX_CODE_LOG_LEVEL"] = "WARNING"
    env["MINIMAX_CODE_DATA_DIR"] = str(workdir)
    proc = await asyncio.create_subprocess_exec(
        python, "-m", "minimax_code",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(agent_dir),
        env=env,
    )
    # Give the agent ~1s to open the DB and migrate.
    await asyncio.sleep(1.0)
    return proc, StdioJSONRPC(proc)


async def shutdown(proc: asyncio.subprocess.Process) -> None:
    """Send ``shutdown`` and wait for clean exit."""
    if proc.returncode is not None:
        return
    try:
        proc.stdin.write(b'{"jsonrpc":"2.0","id":"bye","method":"shutdown","params":{}}\n')
        await proc.stdin.drain()
    except (OSError, ConnectionResetError):
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=3.0)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()


# ---------------------------------------------------------------------------
# Step runner
# ---------------------------------------------------------------------------


class SmokeRunner:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def ok(self, step: str) -> None:
        self.passed.append(step)
        print(f"  [PASS] {step}")

    def fail(self, step: str, detail: str) -> None:
        self.failed.append((step, detail))
        print(f"  [FAIL] {step}: {detail}")

    def expect(self, cond: bool, step: str, detail: str = "") -> bool:
        if cond:
            self.ok(step)
            return True
        self.fail(step, detail)
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main(python: str, agent_dir: Path, workdir: Path) -> int:
    runner = SmokeRunner()
    print(f"=== smoke_permissions: agent={agent_dir} workdir={workdir} ===")

    # 1. First boot
    print("\n--- Boot 1 ---")
    proc1, cli1 = await spawn_agent(python, agent_dir, workdir)

    # 2. permission.set allow
    r = await cli1.request(
        "permission.set",
        {"tool_pattern": "exec_command", "action": "allow", "scope": "global"},
    )
    if "result" in r and r["result"].get("rule", {}).get("action") == "allow":
        runner.ok("step2 permission.set allow")
    else:
        runner.fail("step2 permission.set allow", f"got: {r}")

    # 3. permission.check -> allowed: true
    r = await cli1.request("permission.check", {"tool_name": "exec_command"})
    if "result" in r and r["result"].get("allowed") is True:
        runner.ok("step3 permission.check true after allow")
    else:
        runner.fail("step3 permission.check true after allow", f"got: {r}")

    # 4. permission.set deny
    r = await cli1.request(
        "permission.set",
        {"tool_pattern": "exec_command", "action": "deny"},
    )
    if "result" in r and r["result"].get("rule", {}).get("action") == "deny":
        runner.ok("step4 permission.set deny")
    else:
        runner.fail("step4 permission.set deny", f"got: {r}")

    # 5. permission.check -> allowed: false
    r = await cli1.request("permission.check", {"tool_name": "exec_command"})
    if "result" in r and r["result"].get("allowed") is False:
        runner.ok("step5 permission.check false after deny")
    else:
        runner.fail("step5 permission.check false after deny", f"got: {r}")

    # 6. shutdown
    await shutdown(proc1)
    runner.ok("step6 shutdown first agent")

    # 7. Restart the agent
    print("\n--- Boot 2 (restart) ---")
    proc2, cli2 = await spawn_agent(python, agent_dir, workdir)

    # 8. permission.check -> still allowed: false (persistence)
    r = await cli2.request("permission.check", {"tool_name": "exec_command"})
    if "result" in r and r["result"].get("allowed") is False:
        runner.ok("step8 permission.check false after restart (persistence)")
    else:
        runner.fail(
            "step8 permission.check false after restart (persistence)",
            f"got: {r}",
        )

    # 9. delete + shutdown
    r = await cli2.request(
        "permission.delete", {"tool_pattern": "exec_command"}
    )
    if "result" in r and r["result"].get("ok") is True:
        runner.ok("step9a permission.delete")
    else:
        runner.fail("step9a permission.delete", f"got: {r}")
    await shutdown(proc2)
    runner.ok("step9b shutdown second agent")

    # ---- Summary ---------------------------------------------------------
    print("\n=== SUMMARY ===")
    print(f"  passed: {len(runner.passed)}")
    print(f"  failed: {len(runner.failed)}")
    for step, detail in runner.failed:
        print(f"    - {step}: {detail}")
    return 0 if not runner.failed else 1


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(
            "usage: python smoke_permissions.py <python> <agent_dir> <workdir>",
            file=sys.stderr,
        )
        sys.exit(2)
    python = sys.argv[1]
    agent_dir = Path(sys.argv[2]).resolve()
    workdir = Path(sys.argv[3]).resolve()
    # Wipe the workdir before running so we always start from a clean
    # DB. The previous run's rules would otherwise leak in.
    import shutil

    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)
    sys.exit(asyncio.run(main(python, agent_dir, workdir)))
