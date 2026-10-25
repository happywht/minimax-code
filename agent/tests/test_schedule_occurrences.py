"""Local, offline schedule context and durable occurrence integration."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_project_validation import native  # noqa: F401

from minimax_code.model_snapshot import ModelSnapshot
from minimax_code.runs import get_run_service
from minimax_code.schedule_context import FrozenPermissions, capture_context, validate_context
from minimax_code.scheduler import JobScheduler, SchedulerError, _next_due
from minimax_code.storage.dao.providers import ProviderDAO


@pytest.fixture
async def scheduled(native, monkeypatch):  # noqa: F811
    import minimax_code.schedule_context as context_module
    import minimax_code.scheduler as module
    from minimax_code import app

    provider = await ProviderDAO(native.db).create(
        name="saved",
        protocol="openai",
        base_url="https://offline.invalid",
        models=[{"id": "saved-model"}],
    )
    await native.projects.create(id="p", name="p", root_path=str(native.root))
    permissions = SimpleNamespace(lookup=lambda tool: {"action": "allow"})
    monkeypatch.setattr(
        "minimax_code.ipc.handlers_permissions._ensure_permission_store",
        AsyncMock(return_value=permissions),
    )
    native.models = []

    async def offline_model(db, context):
        await validate_context(db, context)
        native.models.append(
            (context["provider_id"], context["model"], context["reasoning_effort"])
        )
        client = SimpleNamespace(default_model=context["model"], mock=True, close=AsyncMock())
        return ModelSnapshot(
            client,
            context["model"],
            context["reasoning_effort"],
            10000,
            context["provider_id"],
            context["protocol"],
        )

    native.real_resolve_model = context_module.resolve_model
    monkeypatch.setattr(context_module, "resolve_model", offline_model)
    monkeypatch.setattr(module, "_IPC_SERVER", native.server)
    monkeypatch.setattr(
        app, "get_subagent_llm", lambda: SimpleNamespace(default_model="changed-global", mock=True)
    )
    native.scheduler = JobScheduler(native.db)
    native.options = {
        "project_id": "p",
        "provider_id": provider["id"],
        "model": "saved-model",
        "reasoning_effort": "high",
        "budget_tokens": 1000,
    }
    native.provider = provider
    try:
        yield native
    finally:
        native.release.set()
        await get_run_service(native.server).shutdown()
        await asyncio.gather(*list(native.scheduler._inflight), return_exceptions=True)


async def create(state, **kwargs):
    return await state.scheduler.add_job(
        name="scheduled",
        cron_expr="* * * * *",
        payload={"prompt": "do work"},
        context=state.options,
        **kwargs,
    )


async def wait_until(predicate):
    for _ in range(500):
        if await predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Condition did not settle")


async def settled(state, oid):
    return (await state.scheduler._occurrences.get(oid))["status"] not in {"pending", "running"}


async def test_manual_concurrency_replay_lease_and_fresh_conversations(scheduled):
    state = scheduled
    job = await create(state)
    a, b = await asyncio.gather(
        *[
            state.scheduler.run_now(job["id"], idempotency_key="same", expected_revision=1)
            for _ in range(2)
        ]
    )
    assert a["occurrence_id"] == b["occurrence_id"] and a["task_id"] == b["task_id"]
    with pytest.raises(SchedulerError, match="busy"):
        await state.scheduler.run_now(job["id"], idempotency_key="different", expected_revision=1)
    state.release.set()
    await wait_until(lambda: settled(state, a["occurrence_id"]))
    first = await state.scheduler._occurrences.get(a["occurrence_id"])
    second = await state.scheduler.run_now(job["id"], idempotency_key="next", expected_revision=1)
    await wait_until(lambda: settled(state, second["occurrence_id"]))
    other = await state.scheduler._occurrences.get(second["occurrence_id"])
    assert first["session_id"] != other["session_id"] and first["run_id"] != other["run_id"]
    assert len(await state.dao.list_runs()) == 2
    assert state.models == [(state.provider["id"], "saved-model", "high")] * 2
    await state.scheduler.disable(job["id"])
    await state.db.close()
    await state.db.connect()
    again = await state.scheduler.run_now(job["id"], idempotency_key="same", expected_revision=1)
    assert again["occurrence_id"] == a["occurrence_id"] and again["run_id"] == first["run_id"]
    with pytest.raises(SchedulerError, match="conflict"):
        await state.scheduler.run_now(job["id"], idempotency_key="same", expected_revision=2)


@pytest.mark.parametrize("drift", ["root", "provider", "missing-key"])
async def test_drift_and_missing_credentials_make_zero_calls(
    scheduled, monkeypatch, tmp_path, drift
):
    state = scheduled
    job = await create(state)
    if drift == "root":
        changed = tmp_path / "changed"
        changed.mkdir()
        await state.projects.update("p", root_path=str(changed))
    elif drift == "provider":
        await state.db.execute(
            "UPDATE providers SET base_url='https://changed.invalid' WHERE id=?",
            (state.provider["id"],),
        )
    else:
        # Restore only the factory; retain isolated DB/runtime wiring.
        monkeypatch.setattr("minimax_code.schedule_context.resolve_model", state.real_resolve_model)
        monkeypatch.setattr("minimax_code.secrets.get_provider_key", lambda _: None)
        import minimax_code.scheduler as module

        monkeypatch.setattr(module, "_IPC_SERVER", state.server)
    ack = await state.scheduler.run_now(job["id"], idempotency_key="one", expected_revision=1)
    await wait_until(lambda: settled(state, ack["occurrence_id"]))
    assert (await state.scheduler._occurrences.get(ack["occurrence_id"]))["status"] == "blocked"
    assert await state.dao.list_runs() == [] and not state.models


@pytest.mark.parametrize("budget", [None, 0, -1, True])
async def test_missing_or_invalid_budget_is_rejected(scheduled, monkeypatch, budget):
    monkeypatch.delenv("MINIMAX_CODE_RUN_BUDGET_TOKENS", raising=False)
    monkeypatch.delenv("MINIMAX_CODE_SCHEDULED_BUDGET_TOKENS", raising=False)
    with pytest.raises(ValueError, match="positive"):
        await capture_context(scheduled.db, {**scheduled.options, "budget_tokens": budget})


async def test_scheduled_budget_env_and_partial_model_selection(scheduled, monkeypatch):
    monkeypatch.setenv("MINIMAX_CODE_SCHEDULED_BUDGET_TOKENS", "2345")
    context = await capture_context(scheduled.db, {**scheduled.options, "budget_tokens": None})
    assert context["budget_tokens"] == 2345
    with pytest.raises(ValueError, match="together"):
        await capture_context(scheduled.db, {**scheduled.options, "model": None})


@pytest.mark.parametrize(
    "payload",
    [
        {"command": "bad"},
        {"prompt": "x", "command": "bad"},
        {"prompt": "x", "use_runservice": False},
        {"self_evolution": True},
        {"prompt": "x", "unknown": 1},
    ],
)
async def test_unsupported_payloads_never_dispatch(scheduled, payload):
    with pytest.raises(ValueError, match="Only local"):
        await scheduled.scheduler.add_job(
            name="bad", cron_expr="* * * * *", payload=payload, context=scheduled.options
        )
    assert await scheduled.dao.list_runs() == []


@pytest.mark.parametrize(
    "saved,current,expected",
    [
        ("allow", "ask", "ask"),
        ("allow", None, "ask"),
        ("ask", "allow", "ask"),
        ("allow", "deny", "deny"),
        ("deny", "allow", "deny"),
    ],
)
def test_permission_intersection_never_widens(saved, current, expected):
    policy = FrozenPermissions(
        [{"tool_pattern": "exec_*", "action": saved}],
        SimpleNamespace(lookup=lambda _: {"action": current} if current else None),
    )
    assert policy.lookup("exec_command")["action"] == expected
    assert (
        FrozenPermissions([], SimpleNamespace(lookup=lambda _: {"action": "allow"})).lookup(
            "new_tool"
        )["action"]
        == "ask"
    )


async def test_update_cas_delete_and_old_occurrence_snapshot(scheduled):
    state = scheduled
    job = await create(state)
    ack = await state.scheduler.run_now(job["id"], idempotency_key="old", expected_revision=1)
    updated = await state.scheduler.update_job(
        job["id"],
        expected_revision=1,
        name="new",
        cron_expr="0 5 * * *",
        payload={"prompt": "changed"},
        context={**state.options, "mode": "plan"},
    )
    assert updated["revision"] == 2
    with pytest.raises(SchedulerError, match="conflict"):
        await state.scheduler.update_job(
            job["id"],
            expected_revision=1,
            name="new",
            cron_expr="0 5 * * *",
            payload={"prompt": "changed"},
            context=state.options,
        )
    await state.scheduler.remove_job(job["id"])
    state.release.set()
    await wait_until(lambda: settled(state, ack["occurrence_id"]))
    occurrence = await state.scheduler._occurrences.get(ack["occurrence_id"])
    assert (
        occurrence["revision"] == 1
        and occurrence["prompt"] == "do work"
        and occurrence["context"]["mode"] == "execute"
    )
    assert (await state.scheduler.run_now(job["id"], idempotency_key="old", expected_revision=1))[
        "occurrence_id"
    ] == ack["occurrence_id"]
    assert not await state.scheduler.list_jobs()


def test_timezone_due_instants_are_utc_and_dst_aware():
    from datetime import UTC, datetime

    assert (
        _next_due("0 8 * * *", "Asia/Shanghai", datetime(2026, 1, 1, tzinfo=UTC))
        == "2026-01-02T00:00:00Z"
    )
    assert (
        _next_due("0 9 * * *", "America/New_York", datetime(2026, 3, 8, 8, tzinfo=UTC))
        == "2026-03-08T13:00:00Z"
    )


@pytest.mark.parametrize("action", ["disable", "delete"])
async def test_pending_claim_does_not_dispatch_after_owner_stops(scheduled, action):
    state = scheduled
    job = await create(state)
    occ, _ = await state.scheduler._occurrences.claim(
        job["id"], source="manual", manual_key="lost-ack", expected_revision=1
    )
    if action == "delete":
        await state.scheduler.remove_job(job["id"])
    else:
        await state.scheduler.disable(job["id"])
    ack = await state.scheduler.run_now(job["id"], idempotency_key="lost-ack", expected_revision=1)
    assert ack["occurrence_id"] == occ["id"] and not ack["created"]
    await state.scheduler._reconcile_occurrences(dispatch_pending=True)
    await state.scheduler._execute_occurrence(occ)
    assert not state.models and not await state.dao.list_runs()
    if action == "delete":
        assert (await state.scheduler._occurrences.get(occ["id"]))["status"] == "cancelled"


async def test_unadmitted_rebind_retires_snapshot_and_releases_lease(scheduled):
    state = scheduled
    job = await create(state)
    old, _ = await state.scheduler._occurrences.claim(
        job["id"], source="manual", manual_key="old", expected_revision=1
    )
    await state.scheduler.update_job(
        job["id"],
        expected_revision=1,
        name="rebound",
        cron_expr="* * * * *",
        payload={"prompt": "new work"},
        context=state.options,
    )
    await state.scheduler._execute_occurrence(old)
    assert (await state.scheduler._occurrences.get(old["id"]))["status"] == "superseded"
    assert not state.models
    state.release.set()
    new = await state.scheduler.run_now(job["id"], idempotency_key="new", expected_revision=2)
    await wait_until(lambda: settled(state, new["occurrence_id"]))
    assert len(await state.dao.list_runs()) == 1


async def test_cron_cursor_cas_and_coalescing_on_original_loop(scheduled, monkeypatch):
    from datetime import UTC, datetime

    import minimax_code.scheduler as module

    state = scheduled
    job = await create(state)
    loop = asyncio.get_running_loop()
    due = "2026-01-01T00:00:00Z"
    now = "2026-01-03T00:00:00Z"
    await state.scheduler._dao.update_next_run(job["id"], due)
    row = await state.scheduler._dao.get(job["id"])
    monkeypatch.setattr(module, "now_iso", lambda: now)
    await state.scheduler._fire(row, source="cron")
    await wait_until(lambda: async_value(bool(state.models)))
    assert asyncio.get_running_loop() is loop
    await state.scheduler._fire(row, source="cron")
    assert len(await state.scheduler._occurrences.list(job["id"])) == 1
    first = (await state.scheduler._occurrences.list(job["id"]))[0]
    assert first["scheduled_for"] == due and first["coalesced_through"] == now
    assert (await state.scheduler._dao.get(job["id"]))["next_run_at"] > now
    # An overdue cursor remains pending for the entire writer lease.
    overdue = "2026-01-02T00:00:00Z"
    await state.scheduler._dao.update_next_run(job["id"], overdue)
    await state.scheduler._dispatch_due()
    await asyncio.sleep(0.02)
    assert len(await state.scheduler._occurrences.list(job["id"])) == 1
    state.release.set()
    await wait_until(lambda: settled(state, first["id"]))
    await state.scheduler._dispatch_due()
    await wait_until(lambda: async_count(state, job["id"], 2))
    # A stale pre-disable cron row must not rewind the newly enabled cursor.
    await asyncio.gather(*list(state.scheduler._inflight), return_exceptions=True)
    stale = await state.scheduler._dao.get(job["id"])
    await state.scheduler.disable(job["id"])
    await state.scheduler.enable(job["id"])
    await state.scheduler._fire(stale, source="cron")
    assert len(await state.scheduler._occurrences.list(job["id"])) == 2
    assert _next_due("* * * * *", "Asia/Shanghai", datetime(2026, 1, 3, tzinfo=UTC)) > now


async def async_value(value):
    return value


async def async_count(state, job_id, count):
    return len(await state.scheduler._occurrences.list(job_id)) == count


async def test_stop_fences_catchup_and_transport_quiesces_before_service(scheduled, monkeypatch):
    import minimax_code.scheduler as module
    from minimax_code.http_server import build_app

    state = scheduled
    job = await create(state)
    monkeypatch.setattr(module, "_SCHEDULER", state.scheduler)
    state.scheduler._started = True
    ack = await state.scheduler.run_now(job["id"], idempotency_key="held", expected_revision=1)
    await wait_until(lambda: async_value(bool(state.models)))
    await state.scheduler._dao.update_next_run(job["id"], "2020-01-01T00:00:00Z")
    # Exercise the actual production lifespan order, without closing this fixture DB.
    from minimax_code import app

    monkeypatch.setattr(app, "get_db", lambda: None)
    application = build_app(state.server)
    async with application.router.lifespan_context(application):
        pass
    assert state.scheduler._stopping
    assert len(await state.scheduler._occurrences.list(job["id"])) == 1
    assert len(state.models) == 1 and not state.scheduler._inflight
    assert (
        await state.dao.get_run(
            (await state.scheduler._occurrences.get(ack["occurrence_id"]))["run_id"]
        )
    )["status"] == "cancelled"


async def test_owned_client_closed_on_admission_failure(scheduled, monkeypatch):
    from test_project_validation import _Ctx

    from minimax_code.ipc import builtins

    state = scheduled
    job = await create(state)
    occ, _ = await state.scheduler._occurrences.claim(
        job["id"], source="manual", manual_key="client", expected_revision=1
    )
    client = SimpleNamespace(default_model="saved-model", mock=True, close=AsyncMock())
    monkeypatch.setattr(
        "minimax_code.schedule_context.resolve_model",
        AsyncMock(
            return_value=ModelSnapshot(
                client, "saved-model", "high", 10000, state.provider["id"], "openai"
            )
        ),
    )
    monkeypatch.setattr(
        get_run_service(state.server),
        "admit",
        AsyncMock(side_effect=ValueError("injected rejection")),
    )
    ctx = _Ctx(state.server)
    await builtins.handle_agent_send_message(
        {"session_id": occ["session_id"], "content": "x", "idempotency_key": "reject"},
        ctx,
        wait=False,
        internal_schedule={
            "context": occ["context"],
            "occurrence_id": occ["id"],
            "job_id": job["id"],
            "revision": 1,
        },
    )
    client.close.assert_awaited_once()
    ctx.reply_error.assert_awaited_once()
    assert not await state.dao.list_runs()


async def test_occurrence_settlement_cas_ignores_old_attempt_and_history_reconciles(scheduled):
    from test_project_validation import _Ctx

    from minimax_code.ipc import builtins
    from minimax_code.ipc.handlers_runs import register_run_handlers
    from minimax_code.ipc.handlers_scheduled import register_scheduled_handlers

    state = scheduled
    state.result_flags = {"budget_exhausted": True}
    state.release.set()
    job = await create(state)
    ack = await state.scheduler.run_now(job["id"], idempotency_key="paused", expected_revision=1)
    await wait_until(lambda: settled(state, ack["occurrence_id"]))
    old = await state.scheduler._occurrences.get(ack["occurrence_id"])
    assert old["status"] == "budget_paused"
    assert (await state.scheduler._tasks.get(old["task_id"]))["progress"] < 100
    state.result_flags = {}
    state.release.clear()
    register_run_handlers(state.server)
    ctx = _Ctx(state.server)
    await state.server._handlers["run.resume"](
        {
            "run_id": old["run_id"],
            "reviewed": True,
            "budget_tokens": 2000,
            "idempotency_key": "resume",
        },
        ctx,
    )
    assert not ctx.reply_error.called, ctx.reply_error.call_args
    child = ctx.reply.call_args.args[0]["run"]
    assert child["resumed_from_id"] == old["run_id"]
    await state.scheduler._occurrences.settle(
        old["id"], "budget_paused", expected_run_id=old["run_id"]
    )
    assert (await state.scheduler._occurrences.get(old["id"]))["status"] == "running"
    with pytest.raises(SchedulerError, match="execution/cleanup"):
        await state.scheduler.enable(job["id"], acknowledge_occurrence_id=old["id"])
    state.release.set()
    child_job = await get_run_service(state.server).job_for_run(child["id"])
    if child_job:
        await get_run_service(state.server).wait(child_job)
    # No cron pulse: reading history reconciles authoritative child completion.
    register_scheduled_handlers(state.server, state.scheduler)
    history = _Ctx(state.server)
    await state.server._handlers["schedule.occurrences"]({"job_id": job["id"]}, history)
    actual = history.reply.call_args.args[0]["occurrences"][0]
    assert actual["status"] == "completed" and actual["run_id"] == child["id"]
    assert not (await state.scheduler._dao.get(job["id"]))["active_occurrence_id"]
    bypass = _Ctx(state.server)
    await builtins.handle_agent_send_message(
        {"session_id": old["session_id"], "content": "bypass"}, bypass
    )
    bypass.reply_error.assert_awaited_once()


async def test_real_core_two_projects_permission_wait_and_hooks_disabled(
    scheduled, monkeypatch, tmp_path
):
    import sys

    from test_agent_core import FakeLLM, _text_response, _tool_call, _tool_response
    from test_project_validation import _Ctx

    from minimax_code import agent, app
    from minimax_code.agent.core import AgentCore
    from minimax_code.hooks import HookManager
    from minimax_code.ipc import builtins
    from minimax_code.perm_consent import resolve_any_gater

    state = scheduled
    root2 = tmp_path / "second"
    root2.mkdir()
    await state.projects.create(id="p2", name="second", root_path=str(root2))
    state.options["budget_tokens"] = 100000
    first = await create(state)
    second = await state.scheduler.add_job(
        name="second",
        cron_expr="* * * * *",
        payload={"prompt": "write"},
        context={**state.options, "project_id": "p2"},
    )
    # Ambient lifecycle/tool/model hooks installed AFTER both snapshots.
    marker = tmp_path / "ambient-hook.txt"
    manager = HookManager()
    command = [
        sys.executable,
        "-c",
        f'from pathlib import Path;Path({str(marker)!r}).write_text("ambient hook executed")',
    ]
    manager.load_hooks(
        {
            event: [{"command": command}]
            for event in ["session_start", "session_end", "pre_tool_use", "pre_loop_iteration"]
        }
    )
    monkeypatch.setattr(app, "ensure_hook_manager", lambda: manager)
    monkeypatch.setattr(agent, "AgentCore", AgentCore)
    clients = []

    async def factory(db, context):
        await validate_context(db, context)
        llm = FakeLLM(
            [
                _tool_response(
                    _tool_call(
                        "write_file", {"path": "owned.txt", "content": context["project_id"]}
                    )
                ),
                _text_response("finished"),
            ]
        )
        llm.default_model = context["model"]
        llm.mock = True
        llm.close = AsyncMock()
        clients.append(llm)
        return ModelSnapshot(
            llm,
            context["model"],
            context["reasoning_effort"],
            10000,
            context["provider_id"],
            context["protocol"],
        )

    monkeypatch.setattr("minimax_code.schedule_context.resolve_model", factory)
    events = []

    def observe(envelope):
        if envelope.get("event") == "permission.request":
            events.append(envelope["data"])

    state.server.register_listener(observe)
    a = await state.scheduler.run_now(first["id"], idempotency_key="a", expected_revision=1)
    b = await state.scheduler.run_now(second["id"], idempotency_key="b", expected_revision=1)
    await wait_until(lambda: async_value(len(events) == 2))
    assert all(
        event["tool"] == "write_file" and event["approval_policy"] == "explicit_once"
        for event in events
    )
    assert (
        not marker.exists()
        and not (state.root / "owned.txt").exists()
        and not (root2 / "owned.txt").exists()
    )
    rows = await state.dao.list_runs()
    assert {run["status"] for run in rows} == {"awaiting_approval"}
    with pytest.raises(SchedulerError, match="busy"):
        await state.scheduler.run_now(first["id"], idempotency_key="overlap", expected_revision=1)
    # Global allows are already true; saved unknown tool still required consent.
    assert resolve_any_gater(state.server, events[0]["request_id"], True)
    assert resolve_any_gater(state.server, events[1]["request_id"], False)
    await wait_until(lambda: settled(state, a["occurrence_id"]))
    await wait_until(lambda: settled(state, b["occurrence_id"]))
    assert sum(path.exists() for path in [state.root / "owned.txt", root2 / "owned.txt"]) == 1
    allowed = next(event["session_id"] for event in events if event == events[0])
    actual = await state.sessions.get(allowed)
    assert (state.root / "owned.txt").exists() == (actual["project_id"] == "p")
    assert not marker.exists()
    assert all(client.call_count == 2 and client.close.await_count == 1 for client in clients)
    for occurrence in [
        await state.scheduler._occurrences.get(a["occurrence_id"]),
        await state.scheduler._occurrences.get(b["occurrence_id"]),
    ]:
        messages = await state.sessions.get_messages(occurrence["session_id"])
        assert any(message["role"] == "user" for message in messages) and any(
            message["role"] == "assistant" for message in messages
        )
    # Ordinary Native chat still runs installed lifecycle/model hooks.
    ordinary = FakeLLM([_text_response("normal")])
    ordinary.default_model = "normal"
    ordinary.mock = True
    monkeypatch.setattr(app, "get_subagent_llm", lambda: ordinary)
    ctx = _Ctx(state.server)
    ctx.emit = AsyncMock()
    await builtins.handle_agent_send_message(
        {"session_id": "normal-session", "content": "normal"}, ctx
    )
    assert marker.read_text() == "ambient hook executed"


def test_migration_036_disables_populated_legacy_preserving_payload(tmp_path, monkeypatch):
    import minimax_code.storage.db as module
    from minimax_code.storage.db import Database

    discover = module.discover_migrations
    monkeypatch.setattr(
        module,
        "discover_migrations",
        lambda applied: [(version, run) for version, run in discover(applied) if version < 36],
    )
    with Database(tmp_path / "upgrade.db") as db:
        db.migrate()
        conn = db._conn
        conn.execute(
            "INSERT INTO scheduled_jobs(id,name,cron_expr,payload,enabled,created_at) VALUES('legacy','legacy','* * * * *','{\"command\":\"unsafe\"}',1,'now')"
        )
        monkeypatch.setattr(module, "discover_migrations", discover)
        assert db.migrate() == [36, 37]
        row = conn.execute(
            "SELECT enabled,payload,binding_state,revision FROM scheduled_jobs WHERE id='legacy'"
        ).fetchone()
        assert tuple(row) == (0, '{"command":"unsafe"}', "needs_rebind", 1)
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='idx_occurrence_manual'"
        ).fetchone()
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize(
    "payload",
    [{"command": "bad"}, {"prompt": "x", "use_runservice": False}, {"self_evolution": True}],
)
async def test_public_rpc_and_tampered_dispatcher_reject_unsupported(scheduled, payload):
    from test_project_validation import _Ctx

    from minimax_code.ipc.handlers_scheduled import register_scheduled_handlers
    from minimax_code.storage.dao._base import dumps_json

    state = scheduled
    register_scheduled_handlers(state.server, state.scheduler)
    ctx = _Ctx(state.server)
    await state.server._handlers["schedule.create"](
        {"name": "bad", "cron_expr": "* * * * *", "payload": payload, "context": state.options}, ctx
    )
    ctx.reply_error.assert_awaited_once()
    job = await create(state)
    await state.db.execute(
        "UPDATE scheduled_jobs SET payload=? WHERE id=?", (dumps_json(payload), job["id"])
    )
    for method, params in [
        ("schedule.enable", {"job_id": job["id"]}),
        ("schedule.run_now", {"job_id": job["id"], "idempotency_key": "x", "expected_revision": 1}),
        (
            "schedule.update",
            {
                "job_id": job["id"],
                "expected_revision": 1,
                "name": "bad",
                "cron_expr": "* * * * *",
                "payload": payload,
                "context": state.options,
            },
        ),
    ]:
        rejected = _Ctx(state.server)
        await state.server._handlers[method](params, rejected)
        rejected.reply_error.assert_awaited_once()
    await state.scheduler._dao.update_next_run(job["id"], "2020-01-01T00:00:00Z")
    await state.scheduler._fire(await state.scheduler._dao.get(job["id"]), source="cron")
    assert not state.models and not await state.dao.list_runs()


@pytest.mark.parametrize("cancel", [False, True])
async def test_scheduled_descendants_hold_lease_and_drain_before_resources_close(
    scheduled, monkeypatch, cancel
):
    from minimax_code import agent
    from minimax_code.orchestrator.subagent import register_background_run, resolve_subagent_emit

    state = scheduled
    state.release.set()
    original = agent.AgentCore
    child_ready = asyncio.Event()
    child_release = asyncio.Event()
    cleaned = asyncio.Event()
    marker = state.root / "late-child.txt"
    heartbeat = state.root / "child-heartbeat.txt"
    processes = []
    handles = []

    class Parent(original):
        async def run(self, **kwargs):
            result = await super().run(**kwargs)

            async def grandchild():
                try:
                    if cancel:
                        import sys

                        from minimax_code.execution import communicate_bounded, spawn_exec

                        worker = (
                            "import time; from pathlib import Path; "
                            f"p=Path({str(heartbeat)!r}); "
                            "exec(\"while True:\\n p.open('a').write('x')\\n time.sleep(0.03)\")"
                        )
                        command = (
                            "import subprocess,sys,time; from pathlib import Path; "
                            f"subprocess.Popen([sys.executable,'-c',{worker!r}]); "
                            f"time.sleep(60); Path({str(marker)!r}).write_text('late write')"
                        )
                        proc = await spawn_exec(sys.executable, "-c", command, cwd=state.root)
                        processes.append(proc)
                        for _ in range(200):
                            if heartbeat.exists():
                                break
                            await asyncio.sleep(0.01)
                        assert heartbeat.exists()
                        child_ready.set()
                        await communicate_bounded(proc, timeout=65)
                    child_ready.set()
                    await child_release.wait()
                    marker.write_text("after parent answer")
                    return {"reported": False, "partial": False}
                finally:
                    await asyncio.sleep(0.03)
                    cleaned.set()

            async def child():
                task = asyncio.create_task(grandchild())
                register_background_run("grandchild", task)
                handles.append(task)
                await task
                return {"reported": True}

            task = asyncio.create_task(child())
            register_background_run("child", task)
            handles.append(task)
            return result

    monkeypatch.setattr(agent, "AgentCore", Parent)
    clients = []

    async def factory(db, context):
        client = SimpleNamespace(default_model=context["model"], mock=True, close=AsyncMock())
        clients.append(client)
        return ModelSnapshot(
            client, context["model"], "high", 10000, context["provider_id"], context["protocol"]
        )

    monkeypatch.setattr("minimax_code.schedule_context.resolve_model", factory)
    job = await create(state)
    ack = await state.scheduler.run_now(job["id"], idempotency_key="children", expected_revision=1)
    await asyncio.wait_for(child_ready.wait(), 5)
    occurrence = await state.scheduler._occurrences.get(ack["occurrence_id"])
    assert occurrence["status"] == "running" and not marker.exists()
    assert (await state.scheduler._dao.get(job["id"]))["active_occurrence_id"] == occurrence["id"]
    assert clients[0].close.await_count == 0
    assert await resolve_subagent_emit(occurrence["session_id"]) is not None
    assert occurrence["session_id"] in state.server._permission_gaters
    with pytest.raises(SchedulerError, match="busy"):
        await state.scheduler.run_now(job["id"], idempotency_key="next", expected_revision=1)
    if cancel:
        assert get_run_service(state.server).cancel_run(occurrence["run_id"])
    else:
        child_release.set()
    await wait_until(lambda: settled(state, occurrence["id"]))
    assert all(task.done() for task in handles) and cleaned.is_set()
    assert clients[0].close.await_count == 1
    assert await resolve_subagent_emit(occurrence["session_id"]) is None
    actual = await state.scheduler._occurrences.get(occurrence["id"])
    assert actual["status"] == ("cancelled" if cancel else "requires_review")
    assert marker.exists() is not cancel
    if cancel:
        assert processes and all(proc.returncode is not None for proc in processes)
        stopped_at = heartbeat.stat().st_size
        await asyncio.sleep(0.15)
        assert heartbeat.stat().st_size == stopped_at
    run = await state.dao.get_run(actual["run_id"])
    assert (await state.dao.result_payload(actual["run_id"]))["text"] == "done"
    assert any(child["requires_review"] for child in run["metadata"]["verification"]["children"])


@pytest.mark.parametrize("cleanup_source", ["truncated", "cancelled", "budget", "timeout", "success"])
async def test_root_cancel_during_child_process_cleanup_retains_recursive_ownership(
    scheduled, monkeypatch, cleanup_source
):
    import sys

    from minimax_code import agent, execution
    from minimax_code.orchestrator.subagent import register_background_run, resolve_subagent_emit

    state = scheduled
    state.release.set()
    state.result_flags = {
        "truncated": cleanup_source == "truncated",
        "cancelled": cleanup_source == "cancelled",
        "budget_exhausted": cleanup_source == "budget",
    }
    original = agent.AgentCore
    real_terminate = execution.terminate_tree
    cleanup_entered, cleanup_release, cleanup_done = (asyncio.Event() for _ in range(3))
    late_entered, late_release, late_done = (asyncio.Event() for _ in range(3))
    child_started, child_done = asyncio.Event(), asyncio.Event()
    handles, processes, clients, cleanup_calls = [], [], [], []

    async def late_descendant():
        try:
            late_entered.set()
            await asyncio.Event().wait()
        finally:
            await late_release.wait()
            late_done.set()

    async def controlled_terminate(proc):
        # The managed baseline Git probe also crosses terminate_tree now.
        # Gate only the actual process started by this test's child.
        if proc not in processes:
            return await real_terminate(proc)
        cleanup_calls.append(proc.pid)
        # Register after cancellation began, while the drain's first snapshot
        # is already waiting. Its cleanup must remain owned by the next pass.
        late = asyncio.create_task(late_descendant())
        handles.append(late)
        await late_entered.wait()
        register_background_run("late-grandchild", late)
        cleanup_entered.set()
        await cleanup_release.wait()
        await real_terminate(proc)
        cleanup_done.set()

    class Parent(original):
        async def run(self, **kwargs):
            result = await super().run(**kwargs)

            async def child():
                command = "print('finished')" if cleanup_source == "success" else "import time; time.sleep(60)"
                proc = await execution.spawn_exec(sys.executable, "-c", command, cwd=state.root)
                processes.append(proc)
                child_started.set()
                try:
                    await execution.communicate_bounded(
                        proc, timeout=0.05 if cleanup_source == "timeout" else 65
                    )
                finally:
                    child_done.set()

            task = asyncio.create_task(child())
            register_background_run("child", task)
            handles.append(task)
            await child_started.wait()
            return result

    async def factory(db, context):
        client = SimpleNamespace(default_model=context["model"], mock=True, close=AsyncMock())
        clients.append(client)
        return ModelSnapshot(
            client, context["model"], "high", 10000, context["provider_id"], context["protocol"]
        )

    monkeypatch.setattr(agent, "AgentCore", Parent)
    monkeypatch.setattr(execution, "terminate_tree", controlled_terminate)
    monkeypatch.setattr("minimax_code.schedule_context.resolve_model", factory)
    job = await create(state)
    ack = await state.scheduler.run_now(job["id"], idempotency_key="cleanup-race", expected_revision=1)
    try:
        await asyncio.wait_for(cleanup_entered.wait(), 5)
        assert processes and cleanup_calls == [processes[0].pid]
        occurrence = await state.scheduler._occurrences.get(ack["occurrence_id"])
        service = get_run_service(state.server)
        owned_job = await service.job_for_run(occurrence["run_id"])
        assert owned_job is not None
        assert service.cancel_run(occurrence["run_id"])
        await asyncio.sleep(0.03)
        assert not owned_job.task.done() and not child_done.is_set() and not cleanup_done.is_set()
        assert clients[0].close.await_count == 0
        assert await resolve_subagent_emit(occurrence["session_id"]) is not None
        assert occurrence["session_id"] in state.server._permission_gaters
        assert (await state.scheduler._dao.get(job["id"]))["active_occurrence_id"] == occurrence["id"]
        assert (await state.scheduler._occurrences.get(occurrence["id"]))["status"] == "running"
        with pytest.raises(SchedulerError, match="busy"):
            await state.scheduler.run_now(job["id"], idempotency_key="overlap", expected_revision=1)
        # Repeated owner cancellation still cannot detach the retained cleanup.
        owned_job.task.cancel()
        await asyncio.sleep(0.03)
        assert not owned_job.task.done() and not cleanup_done.is_set()
        cleanup_release.set()
        await asyncio.wait_for(child_done.wait(), 5)
        assert cleanup_done.is_set() and not owned_job.task.done() and not late_done.is_set()
        assert all(proc.returncode is not None for proc in processes)
        assert clients[0].close.await_count == 0
        assert await resolve_subagent_emit(occurrence["session_id"]) is not None
        assert occurrence["session_id"] in state.server._permission_gaters
        assert (await state.scheduler._dao.get(job["id"]))["active_occurrence_id"] == occurrence["id"]
        late_release.set()
        await wait_until(lambda: settled(state, occurrence["id"]))
        await asyncio.gather(owned_job.task, return_exceptions=True)
        assert owned_job.task.cancelled()
        assert all(task.done() for task in handles) and late_done.is_set()
        assert cleanup_calls == [processes[0].pid]
        assert clients[0].close.await_count == 1
        assert await resolve_subagent_emit(occurrence["session_id"]) is None
        actual = await state.scheduler._occurrences.get(occurrence["id"])
        assert actual["status"] == "cancelled"
        paused = await state.scheduler._dao.get(job["id"])
        assert not paused["enabled"] and paused["binding_state"] == "requires_review"
        receipt = await state.dao.result_payload(actual["run_id"])
        assert receipt["text"] == "done" and receipt["cancelled"]
        assert {child["run_id"] for child in receipt["verification"]["children"]} == {
            "child", "late-grandchild"
        }
    finally:
        cleanup_release.set()
        late_release.set()
        await get_run_service(state.server).shutdown()
        for proc in processes:
            await real_terminate(proc)


@pytest.mark.parametrize(
    ("result", "review"),
    [
        ({"reported": True, "text": "read-only result"}, False),
        ({"reported": False, "text": "read-only result"}, False),
        (
            {
                "reported": True,
                "workspace": {
                    "collection_required": True,
                    "state": "completed",
                    "files_written": [],
                },
            },
            False,
        ),
        (
            {
                "reported": True,
                "workspace": {
                    "collection_required": True,
                    "state": "collected",
                    "files_written": ["fixed.py"],
                },
            },
            False,
        ),
        (
            {
                "reported": True,
                "workspace": {
                    "collection_required": True,
                    "state": "completed",
                    "files_written": ["fixed.py"],
                },
            },
            True,
        ),
        ({"reported": True, "verification": {"requires_review": True}}, True),
        ({"reported": True, "partial": True}, True),
        ({"reported": True, "error": "child failed"}, True),
        ({}, True),
    ],
)
async def test_child_evidence_preserves_uncollected_artifacts_without_penalizing_readonly(
    result, review
):
    from minimax_code.schedule_context import ScheduledChildren

    async def finished():
        return result

    owner = ScheduledChildren()
    owner.register("child", asyncio.create_task(finished()))
    evidence = await owner.drain()
    assert evidence[0]["requires_review"] is review
    if "workspace" in result:
        assert evidence[0]["workspace"] == result["workspace"]


async def test_double_stop_is_idempotent_and_restart_orderly(scheduled):
    state = scheduled
    loop = asyncio.get_running_loop()
    errors = []
    old_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda loop, context: errors.append(context))
    try:
        await state.scheduler.start()
        await state.scheduler.stop(wait=False)
        await state.scheduler.stop(wait=True)
        await asyncio.sleep(0.01)
        assert not errors
        await state.scheduler.start()
        assert state.scheduler.started
        await state.scheduler.stop(wait=False)
        await state.scheduler.stop(wait=True)
        await asyncio.sleep(0.01)
        assert not errors
    finally:
        loop.set_exception_handler(old_handler)
