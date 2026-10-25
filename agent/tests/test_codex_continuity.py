"""Scripted app-server + real SQLite; no model credentials or inference."""
from __future__ import annotations

import asyncio
import json
import sys

import pytest
from test_run_service import Context, native  # noqa: F401

from minimax_code.engines.base import EngineInfo
from minimax_code.ipc import builtins
from minimax_code.ipc.handlers_runs import register_run_handlers
from minimax_code.runs import get_run_service
from minimax_code.storage.dao.messages import MessagesDAO
from minimax_code.storage.dao.sessions import SessionsDAO

FAKE = r'''
import json, sys, pathlib
root = pathlib.Path.cwd()
statepath = root / 'external.json'
state = json.loads(statepath.read_text()) if statepath.exists() else {'n':0,'total':0}
def send(x):
    print(json.dumps(x),flush=True)
def event(method, params):
    send({'method':method,'params':params})
for raw in sys.stdin:
    req=json.loads(raw); method=req.get('method'); rid=req.get('id'); p=req.get('params',{})
    with (root/'requests.jsonl').open('a') as f: f.write(json.dumps(req)+'\n')
    if rid is None: continue
    result={}
    if method=='initialize': result={'userAgent':'codex-cli/0.163.0'}
    elif method in ('thread/start','thread/resume'):
        result={'thread':{'id':'thread-1','cwd':str(root),'turns':[]},'cwd':str(root),
                'model':'configured-codex','modelProvider':'codex-account','reasoningEffort':'high'}
        if (root/'active.flag').exists(): result['thread']['status']={'type':'active','activeFlags':[]}
    elif method=='turn/start':
        text=p['input'][0]['text']
        if text=='before-turn-failure':
            send({'id':rid,'error':{'code':-1,'message':'turn rejected'}}); continue
        state['n']+=1; tid='turn-'+str(state['n'])
        base={'threadId':'thread-1','turnId':tid}
        event('item/agentMessage/delta',{**base,'itemId':'answer','delta':'answer '+str(state['n'])})
        # Deliberately emit text/usage BEFORE the turn/start response.
        totals=[100] if state['n']==1 else [state['total']+20,state['total']+20,state['total']+50]
        if text=='reset': totals=[10]
        if text=='unknown': totals=[]
        for total in totals:
            b={'inputTokens':total-5,'outputTokens':5,'totalTokens':total}
            event('thread/tokenUsage/updated',{**base,'tokenUsage':{'total':b,'last':b}})
        if text!='reset' and totals: state['total']=totals[-1]
        if text=='unknown': state['total']+=100
        statepath.write_text(json.dumps(state))
        send({'id':rid,'result':{'turn':{'id':tid}}})
        if text=='approval':
            send({'id':1,'method':'item/commandExecution/requestApproval','params':{**base,'command':'test'}})
            decision=json.loads(sys.stdin.readline())
            (root/'decision.json').write_text(json.dumps(decision))
        if text=='eof': sys.exit(0)
        if text=='protocol-error':
            print('INVALID JSON',flush=True); continue
        if text=='hang': continue
        # Late old turn and wrong-thread replays must not alter the current answer.
        event('item/agentMessage/delta',{'threadId':'other','turnId':tid,'delta':'WRONG'})
        if state['n']>1: event('thread/tokenUsage/updated',{'threadId':'thread-1','turnId':'turn-1',
                                      'tokenUsage':{'total':{'inputTokens':9995,'outputTokens':5,'totalTokens':10000}}})
        event('turn/completed',{'threadId':'thread-1','turn':{'id':tid,'status':'failed' if text=='failed' else 'completed'}})
        continue
    elif method=='turn/interrupt':
        if (root/'late.flag').exists():
            event('item/agentMessage/delta',{'threadId':'thread-1','turnId':p['turnId'],'itemId':'late','delta':' final tail'})
            b={'inputTokens':145,'outputTokens':5,'totalTokens':150}
            event('thread/tokenUsage/updated',{'threadId':'thread-1','turnId':p['turnId'],'tokenUsage':{'total':b,'last':b}})
        event('turn/completed',{'threadId':'thread-1','turn':{'id':p['turnId'],'status':'interrupted'}})
    send({'id':rid,'result':result})
'''


@pytest.fixture
async def codex(native, monkeypatch):  # noqa: F811
    from minimax_code.engines import codex as adapter
    await native.db.execute("UPDATE sessions SET engine='codex' WHERE id='one'")
    fake = native.root / "app_server.py"
    fake.write_text(FAKE, encoding="utf-8")
    monkeypatch.setenv("MINIMAX_CODE_CODEX_CMD", json.dumps([sys.executable, str(fake)]))
    monkeypatch.setattr(adapter.CodexEngine, "info", lambda self: _available())
    monkeypatch.delenv("MINIMAX_CODE_DAILY_BUDGET_TOKENS", raising=False)
    monkeypatch.delenv("MINIMAX_CODE_RUN_BUDGET_TOKENS", raising=False)
    register_run_handlers(native.server)
    yield native


async def _available():
    return EngineInfo(id="codex", name="fake", available=True, capabilities={"resume": True})


async def rpc(state, method, params):
    ctx = Context(state.server)
    await state.server._handlers[method](params, ctx)
    return ctx


async def start(state, content="hello", key="one", **kwargs):
    return await rpc(state, "run.start", {"session_id": "one", "content": content,
                                         "idempotency_key": key, **kwargs})


async def settle(state, ctx):
    ctx.reply_error.assert_not_awaited()
    run_id = ctx.reply.call_args.args[0]["run"]["id"]
    job = get_run_service(state.server).get_job(run_id)
    if job:
        await asyncio.gather(get_run_service(state.server).wait(job), return_exceptions=True)
    return await state.dao.get_run(run_id)


def requests(state, method):
    path = state.root / "requests.jsonl"
    return [r for line in path.read_text().splitlines() if (r := json.loads(line)).get("method") == method] if path.exists() else []


async def reconcile_interrupted_attempt(state, run):
    """Workspace review is separate from explicit uncertain-cost authorization."""
    from minimax_code.storage.dao.budgets import BudgetsDAO
    dao = BudgetsDAO(state.db)
    attempts = await dao.attempts(run["metadata"]["budget_family_id"])
    for attempt in attempts:
        if attempt["state"] == "unknown":
            await dao.reconcile(target="attempt", target_id=attempt["id"],
                liability_tokens=max(attempt["estimated_tokens"], attempt["known_tokens"] or 0),
                reason="Offline fixture explicitly authorizes interrupted interval liability")


async def test_two_turns_reopen_and_restore_history(codex):
    first = await settle(codex, await start(codex))
    assert first["status"] == "completed"
    await get_run_service(codex.server).shutdown()
    del codex.server._run_service
    await codex.db.close()
    await codex.db.connect()
    second = await settle(codex, await start(codex, "second", "two"))
    assert second["status"] == "completed"
    assert len(requests(codex, "thread/start")) == len(requests(codex, "thread/resume")) == 1
    assert requests(codex, "thread/resume")[0]["params"]["threadId"] == "thread-1"
    assert requests(codex, "thread/resume")[0]["params"]["model"] == "configured-codex"
    messages = await MessagesDAO(codex.db).list_for_session("one")
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert [m["content"] for m in messages] == ["hello", "answer 1", "second", "answer 2"]
    assert messages[-1]["tokens_in"] == 50 and messages[-1]["tokens_out"] == 0
    assert messages[-1]["metadata"]["usage_certainty"] == "observed"
    session = await SessionsDAO(codex.db).get("one")
    assert session["engine_thread_id"] == "thread-1"
    assert session["engine_state"]["usage_watermark"]["totalTokens"] == 150


async def test_queued_turns_reread_binding_and_retry_one_user(codex, monkeypatch):
    first, duplicate, second = await asyncio.gather(start(codex), start(codex), start(codex, "second", "two"))
    await settle(codex, first)
    await settle(codex, second)
    assert first.reply.call_args.args[0]["run"]["id"] == duplicate.reply.call_args.args[0]["run"]["id"]
    assert len(requests(codex, "turn/start")) == 2
    assert len(requests(codex, "thread/start")) == len(requests(codex, "thread/resume")) == 1
    assert len(await MessagesDAO(codex.db).list_for_session("one")) == 4
    # Reconnect retry needs no CLI availability or current directory.
    from minimax_code.engines.codex import CodexEngine
    monkeypatch.setattr(CodexEngine, "info", lambda self: (_ for _ in ()).throw(RuntimeError("offline CLI")))
    replay = await start(codex)
    replay.reply_error.assert_not_awaited()
    assert replay.reply.call_args.args[0]["created"] is False
    conflict = await start(codex, "changed")
    conflict.reply_error.assert_awaited_once()


@pytest.mark.parametrize("ending", ["eof", "hang", "shutdown", "protocol-error"])
async def test_partial_survives_eof_and_cancel(codex, ending):
    ctx = await start(codex, "hang" if ending == "shutdown" else ending)
    run_id = ctx.reply.call_args.args[0]["run"]["id"]
    for _ in range(200):
        messages = await MessagesDAO(codex.db).list_for_session("one")
        if any(m["role"] == "assistant" and m["tokens_in"] == 95 for m in messages):
            break
        await asyncio.sleep(.01)
    if ending == "hang":
        get_run_service(codex.server).cancel_run(run_id)
    if ending == "shutdown":
        await get_run_service(codex.server).shutdown()
        del codex.server._run_service
    run = await settle(codex, ctx)
    assert run["status"] in {"failed", "cancelled"}
    assert run["metadata"]["codex_thread_id"] == "thread-1"
    result = await codex.dao.result_payload(run_id)
    assert result["text"] == "answer 1" and result["tokens_in"] == 95
    assert result["external_turn_id"] == "turn-1"
    messages = await MessagesDAO(codex.db).list_for_session("one")
    assert messages[-1]["content"] == "answer 1" and messages[-1]["metadata"]["partial"]
    denied = await start(codex, "new", "new")
    denied.reply_error.assert_awaited_once()
    unreviewed = await rpc(codex, "run.resume", {"run_id": run_id, "idempotency_key": "recover"})
    unreviewed.reply_error.assert_awaited_once()
    held = await settle(codex, await rpc(codex, "run.resume", {
        "run_id": run_id, "idempotency_key": "held", "reviewed": True}))
    assert held["status"] == "budget_paused"
    assert len(requests(codex, "turn/start")) == 1
    await reconcile_interrupted_attempt(codex, run)
    recovered = await rpc(codex, "run.resume", {"run_id": held["id"], "idempotency_key": "recover", "reviewed": True, "budget_tokens": 10000})
    recovery_run = await settle(codex, recovered)
    assert recovery_run["resumed_from_id"] == held["id"] and recovery_run["status"] == "completed"
    result = await codex.dao.result_payload(recovery_run["id"])
    assert result["tokens_total"] == 50 and result["usage_certainty"] == "recovery_interval"


async def test_counter_reset_is_uncertain_and_bounded_recovery_requires_review(codex):
    await settle(codex, await start(codex))
    reset = await settle(codex, await start(codex, "reset", "reset", budget_tokens=1000))
    assert reset["status"] == "budget_paused"
    result = await codex.dao.result_payload(reset["id"])
    assert result["usage_certainty"] == "uncertain" and result["tokens_in"] is None
    session = await SessionsDAO(codex.db).get("one")
    assert session["engine_state"]["usage_watermark"]["totalTokens"] == 100
    denied = await rpc(codex, "run.resume", {"run_id": reset["id"], "idempotency_key": "r", "budget_tokens": 2000})
    denied.reply_error.assert_awaited_once()


async def test_native_bypass_and_engine_mismatch_rejected(codex):
    ctx = Context(codex.server)
    await builtins.handle_agent_send_message({"session_id": "one", "content": "wrong"}, ctx)
    ctx.reply_error.assert_awaited_once()
    wrong = await start(codex, engine="native")
    wrong.reply_error.assert_awaited_once()
    assert await codex.dao.list_runs() == []


async def test_binding_failure_never_starts_external_turn(codex):
    await codex.db.execute("CREATE TRIGGER reject_binding BEFORE UPDATE OF engine_thread_id ON sessions "
                           "WHEN NEW.engine_thread_id IS NOT NULL BEGIN SELECT RAISE(ABORT,'database bind failure'); END")
    run = await settle(codex, await start(codex))
    assert run["status"] == "failed"
    assert len(requests(codex, "thread/start")) == 1 and requests(codex, "turn/start") == []
    assert (await SessionsDAO(codex.db).get("one"))["engine_thread_id"] is None


async def test_thread_binding_survives_failure_before_turn_and_recovery(codex):
    run = await settle(codex, await start(codex, "before-turn-failure"))
    assert run["status"] == "failed"
    session = await SessionsDAO(codex.db).get("one")
    assert session["engine_thread_id"] == run["metadata"]["codex_thread_id"] == "thread-1"
    await reconcile_interrupted_attempt(codex, run)
    resumed = await rpc(codex, "run.resume", {"run_id": run["id"], "reviewed": True, "idempotency_key": "r"})
    assert (await settle(codex, resumed))["status"] == "completed"
    assert len(requests(codex, "thread/start")) == 1
    assert requests(codex, "thread/resume")[0]["params"]["threadId"] == "thread-1"


@pytest.mark.parametrize("drift", ["workspace", "thread"])
async def test_workspace_and_missing_thread_cannot_fallback(codex, drift):
    await settle(codex, await start(codex))
    if drift == "workspace":
        new = codex.root / "different"
        new.mkdir()
        await SessionsDAO(codex.db).update("one", workspace_path=str(new))
        denied = await start(codex, "new", "new")
        denied.reply_error.assert_awaited_once()
    else:
        await codex.db.execute("UPDATE sessions SET engine_thread_id=NULL WHERE id='one'")
        denied = await settle(codex, await start(codex, "new", "new"))
        assert denied["status"] == "failed" and "missing" in denied["error"]
    assert len(requests(codex, "thread/start")) == 1
    assert len(requests(codex, "turn/start")) == 1


async def test_history_refresh_preserves_unknown_usage_and_rejects_local_edits(codex):
    from minimax_code.ipc.handlers_sessions import register_session_handlers
    register_session_handlers(codex.server, dao=SessionsDAO(codex.db))
    run = await settle(codex, await start(codex, "unknown"))
    rows = await MessagesDAO(codex.db).list_for_session("one")
    assert rows[-1]["tokens_in"] is None and rows[-1]["tokens_out"] is None
    totals = await MessagesDAO(codex.db).token_totals_for_session("one")
    assert totals == {"tokens_in": None, "tokens_out": None, "usage_complete": False}
    await codex.db.close()
    await codex.db.connect()
    restored = await rpc(codex, "message.list", {"session_id": "one"})
    answer = restored.reply.call_args.args[0]["messages"][-1]
    assert answer["metadata"]["tokens_in"] is None and answer["metadata"]["usage_certainty"] == "unknown"
    detail = await rpc(codex, "session.get", {"session_id": "one"})
    recent = detail.reply.call_args.args[0]["recent_messages"]
    assistant = next(m for m in recent if m["role"] == "assistant")
    assert assistant["tokens_in"] is None and assistant["tokens_out"] is None
    for method in ("message.update", "message.delete"):
        params = {"message_id": run["assistant_message_id"]}
        if method == "message.update":
            params["content"] = "edit"
        denied = await rpc(codex, method, params)
        denied.reply_error.assert_awaited_once()
    denied = await rpc(codex, "session.update", {"session_id": "one", "title": "new", "engine": "native"})
    denied.reply_error.assert_awaited_once()


async def test_engine_specific_empty_session_reuse(codex):
    from minimax_code.ipc.handlers_sessions import register_session_handlers
    register_session_handlers(codex.server, dao=SessionsDAO(codex.db))
    native_session = await rpc(codex, "session.create", {"engine": "native", "reuse_empty_session_id": "one"})
    assert native_session.reply.call_args.args[0]["reused"] is False
    codex_session = await rpc(codex, "session.create", {"engine": "codex", "reuse_empty_session_id": "one"})
    assert codex_session.reply.call_args.args[0]["reused"] is True


async def test_queued_cancellation_still_has_one_durable_user(codex):
    active = await start(codex, "hang")
    queued = await start(codex, "queued", "queued")
    queued_id = queued.reply.call_args.args[0]["run"]["id"]
    get_run_service(codex.server).cancel_run(queued_id)
    run = await settle(codex, queued)
    assert run["status"] == "cancelled" and run["user_message_id"]
    rows = await MessagesDAO(codex.db).list_for_session("one")
    assert len([m for m in rows if m["id"] == run["user_message_id"] and m["content"] == "queued"]) == 1
    get_run_service(codex.server).cancel_run(active.reply.call_args.args[0]["run"]["id"])
    await settle(codex, active)


async def test_missing_usage_pauses_and_reviewed_interval_is_not_precise_new_turn(codex):
    first = await settle(codex, await start(codex, "unknown"))
    assert first["status"] == "budget_paused"
    session = await SessionsDAO(codex.db).get("one")
    assert session["engine_state"]["uncertain_interval"] is True
    denied = await start(codex, "next", "next")
    denied.reply_error.assert_awaited_once()
    await reconcile_interrupted_attempt(codex, first)
    second = await settle(codex, await rpc(codex, "run.resume", {"run_id": first["id"], "idempotency_key": "next", "reviewed": True, "budget_tokens": 1000}))
    result = await codex.dao.result_payload(second["id"])
    assert result["tokens_total"] == 150 and result["usage_certainty"] == "recovery_interval"


async def test_failed_predecessor_does_not_make_queued_successor_another_review_blocker(codex):
    first = await start(codex, "eof")
    queued = await start(codex, "queued", "queued")
    original = await settle(codex, first)
    blocked = await settle(codex, queued)
    assert original["status"] == "failed"
    assert blocked["status"] == "failed" and not blocked.get("started_at")
    cursor = (await codex.dao.events(original["id"]))["next_seq"]
    await reconcile_interrupted_attempt(codex, original)
    resumed = await rpc(codex, "run.resume", {"run_id": original["id"], "idempotency_key": "recover", "reviewed": True})
    assert (await settle(codex, resumed))["status"] == "completed"
    events = await codex.dao.events(original["id"], after_seq=cursor)
    assert events["events"][-1]["data"]["run"]["metadata"]["recovery_child_id"] == resumed.reply.call_args.args[0]["run"]["id"]


async def test_active_external_thread_with_empty_turn_list_cannot_start_fresh_turn(codex):
    first = await settle(codex, await start(codex, "failed"))
    (codex.root / "active.flag").touch()
    resumed = await rpc(codex, "run.resume", {"run_id": first["id"], "idempotency_key": "recover", "reviewed": True})
    run = await settle(codex, resumed)
    assert run["status"] == "failed"
    assert "active external turn" in run["error"]
    assert len(requests(codex, "turn/start")) == 1
    assert run["metadata"]["codex_thread_id"] == "thread-1"
    (codex.root / "active.flag").unlink()
    await reconcile_interrupted_attempt(codex, first)
    retry = await rpc(codex, "run.resume", {"run_id": run["id"], "idempotency_key": "retry-child", "reviewed": True})
    assert (await settle(codex, retry))["status"] == "completed"
    assert len(requests(codex, "thread/start")) == 1
    assert len(requests(codex, "thread/resume")) == 2


async def test_cancel_flushes_text_and_usage_emitted_only_during_interrupt(codex):
    (codex.root / "late.flag").touch()
    ctx = await start(codex, "hang")
    run_id = ctx.reply.call_args.args[0]["run"]["id"]
    for _ in range(200):
        rows = await MessagesDAO(codex.db).list_for_session("one")
        if any(m["role"] == "assistant" and m["tokens_in"] == 95 for m in rows):
            break
        await asyncio.sleep(.01)
    get_run_service(codex.server).cancel_run(run_id)
    run = await settle(codex, ctx)
    assert run["status"] == "cancelled"
    result = await codex.dao.result_payload(run_id)
    assert result["text"] == "answer 1 final tail" and result["tokens_total"] == 150
    session = await SessionsDAO(codex.db).get("one")
    assert session["engine_state"]["usage_watermark"]["totalTokens"] == 150
    rows = await MessagesDAO(codex.db).list_for_session("one")
    assert rows[-1]["content"] == result["text"] and rows[-1]["tokens_in"] == 145


async def test_concurrent_external_approval_ids_are_scoped_to_each_run(codex):
    from minimax_code.perm_consent import resolve_any_gater
    other = codex.root / "other"
    other.mkdir()
    await codex.db.execute("UPDATE sessions SET engine='codex',workspace_path=? WHERE id='two'", (str(other),))
    a = await start(codex, "approval")
    b = await rpc(codex, "run.start", {"session_id": "two", "content": "approval", "idempotency_key": "two"})
    for _ in range(300):
        gates = getattr(codex.server, "_permission_gaters", {})
        if len(gates) == 2 and all(g.pending_ids() for g in gates.values()):
            break
        await asyncio.sleep(.01)
    a_id, b_id = gates["one"].pending_ids()[0], gates["two"].pending_ids()[0]
    assert a_id != b_id and a_id.endswith(":1") and b_id.endswith(":1")
    assert resolve_any_gater(codex.server, b_id, True)
    assert (await settle(codex, b))["status"] == "completed"
    assert gates["one"].has_pending(a_id) and not (codex.root / "decision.json").exists()
    assert json.loads((other / "decision.json").read_text())["result"]["decision"] == "accept"
    assert resolve_any_gater(codex.server, a_id, False)
    await settle(codex, a)
    assert json.loads((codex.root / "decision.json").read_text())["result"]["decision"] == "decline"


@pytest.mark.parametrize("history,safe", [("unique", True), ("mixed", False), ("multiple", False), ("missing", False)])
def test_legacy_engine_migration_requires_unique_verifiable_context(tmp_path, monkeypatch, history, safe):
    import importlib

    from test_run_admission import old_database

    from minimax_code.storage.migrations import discover_migrations
    monkeypatch.setattr("minimax_code.storage.db.discover_migrations",
                        lambda applied: [(v, run) for v, run in discover_migrations(applied) if v < 34])
    with old_database(tmp_path / "legacy.db", version=30) as db:
        db.migrate()
        md = {"engine": "codex", "codex_thread_id": "thread-1", "workspace_snapshot": str(tmp_path),
              "codex_protocol": "codex-cli-0.163.0"}
        if history == "missing":
            md.pop("workspace_snapshot")
        db.execute("UPDATE agent_runs SET metadata=? WHERE id='r'", (json.dumps(md),))
        if history in {"mixed", "multiple"}:
            other = {} if history == "mixed" else {**md, "codex_thread_id": "thread-2"}
            db.execute("INSERT INTO agent_runs(id,session_id,created_at,metadata) VALUES('other','s','t',?)", (json.dumps(other),))
        migration = importlib.import_module("minimax_code.storage.migrations.034_session_engine")
        with db.transaction() as conn:
            migration.run(conn)
        session = db.fetchone("SELECT * FROM sessions WHERE id='s'")
        assert session["engine"] == "codex"
        assert json.loads(session["engine_state"])["handoff_required"] is not safe
        assert session["engine_thread_id"] == ("thread-1" if safe else None)
