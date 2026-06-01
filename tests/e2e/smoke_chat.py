"""E2E smoke for the chat chain: ``agent.send_message`` -> AgentCore -> mock LLM -> events.

Black-box subprocess test:
  1. spawn the agent with ``MINIMAX_API_KEY=""`` to force mock LLM mode
  2. agent.send_message { session_id: "test-session", content: "Hello" }
  3. capture every ``agent.message_chunk`` event from stdout
  4. assert >=1 chunk carries a non-empty ``delta`` and the last chunk
     has ``done: true``
  5. read the messages table directly via sqlite3 and assert both
     the user message ("Hello") and the assistant message
     (the mock-LLM canned text) landed in the DB
  6. shutdown

The mock LLM emits the canned ``_MOCK_TEXT`` in 16-char chunks, then
a final ``finish_reason="stop"`` chunk. The AgentCore converts this
into N ``agent.message_chunk`` events with ``done=False`` plus one
terminal event with ``done=True`` (and an empty delta). We assert on
that contract.

Usage::

    python tests/e2e/smoke_chat.py <python> <agent_dir> <workdir>

Exit 0 = pass, 1 = fail.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

CHUNKS_REQUIRED = 1  # at least 1 chunk must have a non-empty delta
SESSION_ID = "test-session"
USER_CONTENT = "Hello"


# ---------------------------------------------------------------------------
# IPC plumbing (same shape as the other e2e smokes)
# ---------------------------------------------------------------------------


async def _call(
    proc: asyncio.subprocess.Process,
    method: str,
    params: dict | None = None,
    timeout: float = 8.0,
) -> tuple[dict, list[dict]]:
    """Send a request, return ``(response, streamed_events)``.

    Drains stdout line-by-line. Returns the response envelope whose
    ``id`` matches the request, plus any ``event: ...`` lines that
    arrived in between (these are the ``agent.message_chunk`` push
    events the frontend would receive).
    """
    req_id = f"req-{method}-{time.time_ns()}"
    req = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
    await proc.stdin.drain()

    events: list[dict] = []
    deadline = time.time() + timeout
    assert proc.stdout is not None
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(
                proc.stdout.readline(),
                timeout=max(0.05, deadline - time.time()),
            )
        except asyncio.TimeoutError:
            break
        if not raw:
            break
        try:
            obj = json.loads(raw.decode("utf-8", errors="replace").strip())
        except json.JSONDecodeError:
            continue
        if obj.get("id") == req_id:
            return obj, events
        if "event" in obj:
            events.append(obj)
    return ({"error": {"code": -32000, "message": f"timeout waiting for {method}"}}, events)


async def _spawn(env: dict, agent_dir: str, python: str) -> asyncio.subprocess.Process:
    proc = await asyncio.create_subprocess_exec(
        python, "-m", "minimax_code",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=agent_dir,
        env=env,
    )
    await asyncio.sleep(0.6)  # boot
    return proc


async def _shutdown(proc: asyncio.subprocess.Process) -> None:
    """Best-effort shutdown: send IPC ``shutdown`` then wait/kill."""
    try:
        await _call(proc, "shutdown", timeout=2.0)
    except Exception:
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=3.0)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            await proc.wait()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Message-table verification (read after the agent has shut down so the
# WAL is fully flushed)
# ---------------------------------------------------------------------------


def _read_messages(db_path: Path, session_id: str) -> list[dict]:
    """Return every row from the messages table for ``session_id``."""
    if not db_path.exists():
        raise RuntimeError(f"data.db not found at {db_path}")
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "SELECT role, content, created_at FROM messages "
            "WHERE session_id = ? ORDER BY created_at ASC, id ASC",
            (session_id,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {"role": r[0], "content": r[1] or "", "created_at": r[2]}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main(python: str, agent_dir: str, workdir: str) -> int:
    workdir_path = Path(workdir).resolve()
    workdir_path.mkdir(parents=True, exist_ok=True)
    db_path = workdir_path / "data.db"

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(agent_dir).resolve())
    env["MINIMAX_CODE_LOG_LEVEL"] = "WARNING"
    env["MINIMAX_CODE_DATA_DIR"] = str(workdir_path)
    # Critical: force mock mode for the LLM client. The MiniMaxClient
    # treats an empty / unset MINIMAX_API_KEY as mock mode and emits
    # a deterministic canned response in 16-char chunks.
    env["MINIMAX_API_KEY"] = ""

    failures: list[str] = []

    def _fail(msg: str) -> None:
        failures.append(msg)
        print(f"  FAIL: {msg}")

    print(f"=== smoke_chat ===")
    print(f"  python    = {python}")
    print(f"  agent_dir = {agent_dir}")
    print(f"  workdir   = {workdir_path}")
    print(f"  db_path   = {db_path}")
    print()

    proc = await _spawn(env, agent_dir, python)

    # ---- 1. liveness ------------------------------------------------------
    r, _ = await _call(proc, "status")
    if "error" in r:
        _fail(f"status failed: {r['error']}")
        await _shutdown(proc)
        return 1
    print("[1/4] status: ok")

    # ---- 2. send a chat message + capture events --------------------------
    r, events = await _call(
        proc,
        "agent.send_message",
        {"session_id": SESSION_ID, "content": USER_CONTENT},
        timeout=10.0,
    )
    if "error" in r:
        _fail(f"agent.send_message failed: {r['error']}")
        await _shutdown(proc)
        return 1

    result = r.get("result", {})
    reply_text = result.get("text", "")
    print(f"[2/4] agent.send_message reply: text={reply_text[:80]!r}")
    print(f"      events received: {len(events)}")
    for e in events:
        ev = e.get("event", "?")
        d = e.get("data") or {}
        delta = (d.get("delta") or "")[:30]
        done = d.get("done")
        sid = d.get("session_id", "?")[:18]
        print(f"        - {ev} sid={sid} done={done} delta={delta!r}")

    chunk_events = [e for e in events if e.get("event") == "agent.message_chunk"]
    if not chunk_events:
        _fail(f"no agent.message_chunk events streamed (got {len(events)} events total)")
    else:
        # 2a. at least one chunk must carry a non-empty delta
        deltas = [
            (e.get("data") or {}).get("delta", "")
            for e in chunk_events
        ]
        non_empty = [d for d in deltas if d]
        if len(non_empty) < CHUNKS_REQUIRED:
            _fail(
                f"need >= {CHUNKS_REQUIRED} chunk with non-empty delta, "
                f"got {len(non_empty)} (deltas={deltas})"
            )
        # 2b. the *last* chunk must have done=True
        last = chunk_events[-1]
        last_done = (last.get("data") or {}).get("done")
        if last_done is not True:
            _fail(f"last chunk done != True (got {last_done!r})")
    if not failures:
        print(f"[3/4] chunk events: {len(chunk_events)} streamed, last done=True, "
              f"{sum(1 for d in deltas if d)} non-empty deltas")

    # ---- 3. shutdown ------------------------------------------------------
    await _shutdown(proc)
    # Give SQLite a moment to flush WAL -> main file so the
    # stdlib ``sqlite3`` reader below sees the rows.
    await asyncio.sleep(0.4)

    # ---- 4. read messages table -------------------------------------------
    try:
        rows = _read_messages(db_path, SESSION_ID)
    except Exception as exc:
        _fail(f"could not read messages table: {exc}")
        rows = []

    print(f"[4/4] messages table rows for {SESSION_ID!r}: {len(rows)}")
    for row in rows:
        preview = (row["content"] or "")[:60]
        print(f"        - {row['role']:9s} | {row['created_at']} | {preview!r}")

    if not rows:
        _fail(f"messages table has 0 rows for session_id={SESSION_ID!r}")
    else:
        roles = {r["role"] for r in rows}
        if "user" not in roles:
            _fail(f"messages table missing user row (roles seen: {sorted(roles)})")
        if "assistant" not in roles:
            _fail(f"messages table missing assistant row (roles seen: {sorted(roles)})")
        # Belt-and-suspenders: the user content should be the literal
        # string we sent, and the assistant content should be non-empty
        # (the mock LLM's canned response).
        user_row = next((r for r in rows if r["role"] == "user"), None)
        if user_row is not None and user_row["content"] != USER_CONTENT:
            _fail(
                f"user row content mismatch: expected {USER_CONTENT!r}, "
                f"got {user_row['content']!r}"
            )
        assistant_row = next((r for r in rows if r["role"] == "assistant"), None)
        if assistant_row is not None and not assistant_row["content"].strip():
            _fail("assistant row content is empty (mock LLM should have produced text)")

    print()
    if failures:
        print("=== FAIL ===")
        for f in failures:
            print(f"  FAIL: {f}")
        return 1
    print("=== PASS ===")
    return 0


if __name__ == "__main__":
    python = sys.argv[1]
    agent_dir = sys.argv[2]
    workdir = sys.argv[3] if len(sys.argv) > 3 else "."
    Path(workdir).mkdir(parents=True, exist_ok=True)
    sys.exit(asyncio.run(main(python, agent_dir, workdir)))
