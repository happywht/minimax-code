"""Debug: send skill.list via real IPC and capture stderr to see what init_runtime did."""
import asyncio
import json
import os
import sys
import time

async def go():
    env = os.environ.copy()
    env["PYTHONPATH"] = "D:/工作/城建院/2606/agent"
    env["MINIMAX_CODE_LOG_LEVEL"] = "DEBUG"

    proc = await asyncio.create_subprocess_exec(
        "D:/工作/城建院/2606/agent/.venv/Scripts/python.exe",
        "-m", "minimax_code",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd="D:/工作/城建院/2606/agent",
        env=env,
    )

    await asyncio.sleep(1.0)

    req = {"jsonrpc": "2.0", "id": "dbg-1", "method": "skill.list", "params": {}}
    proc.stdin.write((json.dumps(req) + "\n").encode("utf-8"))
    await proc.stdin.drain()

    await asyncio.sleep(2.0)
    proc.stdin.write((json.dumps({"jsonrpc":"2.0","id":"dbg-2","method":"shutdown","params":{}}) + "\n").encode("utf-8"))
    await proc.stdin.drain()

    try:
        await asyncio.wait_for(proc.wait(), timeout=3.0)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()

    out, err = await proc.communicate()
    print("=== STDOUT ===")
    print(out.decode("utf-8", errors="replace"))
    print("=== STDERR ===")
    print(err.decode("utf-8", errors="replace"))

asyncio.run(go())
