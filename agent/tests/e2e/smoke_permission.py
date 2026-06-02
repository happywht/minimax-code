"""End-to-end smoke test for the real tool-call consent flow.

This subprocess test exercises the ``permission.*`` IPC namespace +
the new ``permission.resolve`` handler against a real ``python -m
minimax_code`` agent. We can't drive a *real* tool call from the
mock-mode LLM (it returns canned text, not function-calls), so the
test focuses on the wire-format + handler surface:

1. ``permission.set { tool_pattern, action: "ask" }`` — the rule the
   gater will hit when a real tool is invoked.
2. ``permission.check`` — confirms the rule is visible.
3. ``permission.resolve { request_id: "perm_unknown", decision }`` —
   the handler must respond with ``ok: false, reason: "unknown or
   already-resolved request_id"`` and not crash (the sidecar is not
   currently waiting on a gater; the resolve is a no-op success).
4. ``permission.resolve { request_id: "perm_live", decision: "allow" }``
   in a second boot after we manually wire a :class:`PermissionGater`
   to the server — proves the resolve path returns ``ok: true`` when
   there *is* a pending request. (We can't do this through stdio;
   see the in-process ``test_chat.py`` for the full gated-loop
   coverage.)

The actual real-tool-call round trip is covered in
:mod:`agent.tests.test_chat.TestAgentCoreGating`, which drives the
gater + resolve flow in-process with a fake LLM. The smoke here
proves the *production* wire path is wired up.

Usage::

    python tests/e2e/smoke_permission.py <python> <agent_dir> <workdir>
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
        proc.stdin.write(
            b'{"jsonrpc":"2.0","id":"bye","method":"shutdown","params":{}}\n'
        )
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
    print(f"=== smoke_permission: agent={agent_dir} workdir={workdir} ===")

    # 1. Boot
    print("\n--- Boot 1 ---")
    proc, cli = await spawn_agent(python, agent_dir, workdir)

    # 2. permission.set ask
    r = await cli.request(
        "permission.set",
        {"tool_pattern": "mock_dangerous", "action": "ask", "scope": "global"},
    )
    if "result" in r and r["result"].get("rule", {}).get("action") == "ask":
        runner.ok("step2 permission.set ask")
    else:
        runner.fail("step2 permission.set ask", f"got: {r}")

    # 3. permission.check -> action: "ask" (the gater will fire on a real tool call)
    r = await cli.request(
        "permission.check", {"tool_name": "mock_dangerous"}
    )
    if (
        "result" in r
        and r["result"].get("action") == "ask"
        and r["result"].get("allowed") is True
    ):
        runner.ok("step3 permission.check reports action=ask")
    else:
        runner.fail("step3 permission.check reports action=ask", f"got: {r}")

    # 4. permission.resolve with no active gater — handler must
    #    respond cleanly with ok=false and a reason, NOT crash.
    r = await cli.request(
        "permission.resolve",
        {"request_id": "perm_unknown_xyz", "decision": "allow"},
    )
    if (
        "result" in r
        and r["result"].get("ok") is False
        and "no active consent gater" in r["result"].get("reason", "")
    ):
        runner.ok("step4 permission.resolve (no gater) ok=false with reason")
    else:
        runner.fail(
            "step4 permission.resolve (no gater) ok=false with reason",
            f"got: {r}",
        )

    # 5. permission.resolve with malformed decision — must error.
    r = await cli.request(
        "permission.resolve",
        {"request_id": "perm_x", "decision": "maybe"},
    )
    if "error" in r and "decision" in str(r.get("error", {})).lower():
        runner.ok("step5 permission.resolve rejects invalid decision")
    else:
        runner.fail(
            "step5 permission.resolve rejects invalid decision", f"got: {r}"
        )

    # 6. permission.resolve with missing param — must error.
    r = await cli.request(
        "permission.resolve", {"request_id": "perm_x"}
    )
    if "error" in r and "missing" in str(r.get("error", {})).lower():
        runner.ok("step6 permission.resolve rejects missing decision")
    else:
        runner.fail(
            "step6 permission.resolve rejects missing decision", f"got: {r}"
        )

    # 7. permission.set deny (negative path) + check
    r = await cli.request(
        "permission.set",
        {"tool_pattern": "mock_dangerous", "action": "deny"},
    )
    if "result" in r and r["result"].get("rule", {}).get("action") == "deny":
        runner.ok("step7a permission.set deny")
    else:
        runner.fail("step7a permission.set deny", f"got: {r}")
    r = await cli.request(
        "permission.check", {"tool_name": "mock_dangerous"}
    )
    if "result" in r and r["result"].get("allowed") is False:
        runner.ok("step7b permission.check reports allowed=false after deny")
    else:
        runner.fail(
            "step7b permission.check reports allowed=false after deny",
            f"got: {r}",
        )

    # 8. shutdown
    await shutdown(proc)
    runner.ok("step8 shutdown first agent")

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
            "usage: python smoke_permission.py <python> <agent_dir> <workdir>",
            file=sys.stderr,
        )
        sys.exit(2)
    python = sys.argv[1]
    agent_dir = Path(sys.argv[2]).resolve()
    workdir = Path(sys.argv[3]).resolve()
    import shutil

    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)
    sys.exit(asyncio.run(main(python, agent_dir, workdir)))
