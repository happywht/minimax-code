"""Historical uncertainty upgrade, using temporary SQLite and no paid calls."""
from __future__ import annotations

import importlib
import inspect
import json
import sqlite3
from types import SimpleNamespace

import pytest

from minimax_code.budget import BudgetExhausted
from minimax_code.budget_ledger import BudgetHandle, budget_scope, governed_request
from minimax_code.storage import db as db_module
from minimax_code.storage.dao.budgets import BudgetsDAO
from minimax_code.storage.db import AsyncDatabase, Database

migration = importlib.import_module(
    "minimax_code.storage.migrations.044_anthropic_usage_uncertainty"
)
DAY = "2026-10-25"
STAMP = "2026-10-25T01:00:00+00:00"


def snapshot(protocol="anthropic", provider="builtin-minimax", model="MiniMax-M3"):
    return {"engine": "native", "model_snapshot": {
        "protocol": protocol, "provider_id": provider, "model": model}}


@pytest.fixture
def old(tmp_path, monkeypatch):
    discover = db_module.discover_migrations
    monkeypatch.setattr(db_module, "discover_migrations", lambda applied: [
        (version, run) for version, run in discover(applied) if version < 44])
    db = Database(tmp_path / "upgrade.db")
    db.migrate()
    monkeypatch.setattr(db_module, "discover_migrations", discover)
    db.execute("INSERT INTO sessions(id,title,created_at,updated_at) VALUES('s','',?,?)",
               (STAMP, STAMP))
    db.execute("UPDATE budget_coverage SET started_at=?", (STAMP,))
    try:
        yield db
    finally:
        db.close()


def run_row(db, rid, metadata):
    db.execute("INSERT INTO agent_runs(id,session_id,status,created_at,metadata,result) "
               "VALUES(?,'s','completed',?,?,?)",
               (rid, STAMP, json.dumps(metadata), '{"verification":{"verdict":"failed"}}'))


def seed(db, aid="a", *, metadata=None, root_metadata=None, provider="builtin-minimax",
         model="MiniMax-M3", purpose="inference", prompt=0, completion=10, state="settled",
         provenance="provider", missing_owner=False):
    root, owner, family = f"root_{aid}", f"owner_{aid}", f"family_{aid}"
    run_row(db, root, root_metadata if root_metadata is not None else {})
    if not missing_owner:
        run_row(db, owner, metadata if metadata is not None else snapshot())
    db.execute("INSERT INTO budget_families(id,root_run_id,cap_tokens,created_at) "
               "VALUES(?,?,60000,?)", (family, root, STAMP))
    usage = json.dumps({"prompt_tokens": prompt, "completion_tokens": completion,
                        "total_tokens": prompt + completion,
                        "complete": True, "raw": {"safe": 1}})
    db.execute("INSERT INTO budget_attempts(id,family_id,run_id,provider,model,purpose,pricing,"
               "reservation_day,estimated_tokens,state,known_tokens,usage,provenance,created_at,"
               "sent_at,settled_at,reason) VALUES(?,?,?,?,?,?,'{}',?,100,?,?,?,?,?,?,?,'original')",
               (aid, family, owner, provider, model, purpose, DAY, state, prompt + completion,
                usage, provenance, STAMP, STAMP, STAMP))
    return family


def rows(db, table):
    return [dict(row) for row in db.fetchall(f"SELECT * FROM {table} ORDER BY rowid")]


@pytest.mark.parametrize("prompt,completion", [(0, 0), (0, 10), (23, 10)])
def test_zero_and_positive_are_uncertain_not_recomputed(old, prompt, completion):
    seed(old, prompt=prompt, completion=completion)
    old.execute("INSERT INTO run_events(id,run_id,seq,type,created_at,data) "
                "VALUES('original_event','owner_a',1,'status',?,'{}')", (STAMP,))
    original_event = rows(old, "run_events")[0]
    before = rows(old, "budget_attempts")[0]
    untouched = {table: rows(old, table) for table in (
        "budget_families", "budget_days", "budget_coverage", "agent_runs", "messages")}
    assert old.migrate() == [44]
    after = rows(old, "budget_attempts")[0]
    assert after.pop("state") == "unknown"
    assert after.pop("reason") == "original; " + migration.REASON
    before.pop("state")
    before.pop("reason")
    assert after == before
    for table, original in untouched.items():
        assert rows(old, table) == original
    assert rows(old, "run_events")[0] == original_event
    event = rows(old, "run_events")[1]
    assert event["seq"] == 2
    assert event["run_id"] == "owner_a" and event["type"] == "budget"
    data = json.loads(event["data"])
    assert data["classification"] == "anthropic"
    assert data["known_lower_bound"] == prompt + completion
    assert data["previous_state"] == "settled" and data["state"] == "unknown"
    assert old.migrate() == []
    assert len(rows(old, "run_events")) == 2


@pytest.mark.parametrize("metadata,root_metadata,purpose,missing_owner,expected", [
    (snapshot("openai"), {}, "inference", False, "settled"),
    ({"engine": "codex", "codex_settings": {"modelProvider": "builtin-minimax",
                                           "model": "MiniMax-M3"}}, {},
     "codex_external_turn", False, "settled"),
    (snapshot(None), {}, "inference", False, "unknown"),
    ({}, {}, "inference", False, "unknown"),
    ({"model_snapshot": "malformed"}, {}, "inference", False, "unknown"),
    (snapshot("openai", model="different"), {}, "inference", False, "unknown"),
    ({}, snapshot("openai"), "subagent", False, "settled"),
    ({}, snapshot("openai", provider="different"), "subagent", False, "unknown"),
    ({}, snapshot("anthropic"), "subagent", True, "unknown"),
    ({"engine": "codex"}, {}, "inference", False, "unknown"),
])
def test_persisted_matching_routing_only(old, metadata, root_metadata, purpose,
                                       missing_owner, expected):
    seed(old, metadata=metadata, root_metadata=root_metadata, purpose=purpose,
         missing_owner=missing_owner)
    # Today's provider is deliberately misleading and must not affect history.
    old.execute("UPDATE providers SET protocol='openai' WHERE id='builtin-minimax'")
    old.migrate()
    assert rows(old, "budget_attempts")[0]["state"] == expected
    events = rows(old, "run_events")
    assert len(events) == int(expected == "unknown")
    if missing_owner:
        assert events[0]["run_id"] == "root_a"


@pytest.mark.parametrize("state,provenance", [
    ("unknown", "provider"), ("released", "provider"), ("reconciled", "reconciled"),
    ("reserved", None), ("sent", None), ("settled", "reconciled"),
])
def test_other_states_and_authorizations_untouched(old, state, provenance):
    seed(old, state=state, provenance=provenance)
    before = rows(old, "budget_attempts")
    old.migrate()
    assert rows(old, "budget_attempts") == before
    assert rows(old, "run_events") == []


def test_event_failure_rolls_back_data_and_version_then_retry_once(old):
    seed(old)
    old.execute("CREATE TRIGGER deny_event BEFORE INSERT ON run_events "
                "BEGIN SELECT RAISE(ABORT,'audit unavailable'); END")
    before = rows(old, "budget_attempts")
    with pytest.raises(sqlite3.IntegrityError, match="audit unavailable"):
        old.migrate()
    assert rows(old, "budget_attempts") == before
    assert 44 not in old.applied_versions()
    assert rows(old, "run_events") == []
    old.execute("DROP TRIGGER deny_event")
    assert old.migrate() == [44]
    assert old.migrate() == []


def test_fresh_database_is_clean_and_future_rows_not_reclassified(tmp_path):
    db = Database(tmp_path / "fresh.db")
    try:
        assert 44 in db.migrate()
        assert rows(db, "budget_attempts") == [] and rows(db, "run_events") == []
        db.execute("INSERT INTO sessions(id,title,created_at,updated_at) VALUES('s','',?,?)",
                   (STAMP, STAMP))
        seed(db)
        assert db.migrate() == []
        assert rows(db, "budget_attempts")[0]["state"] == "settled"
    finally:
        db.close()


async def test_upgrade_holds_before_dispatch_and_review_does_not_forge_measurement(old):
    family = seed(old)
    seed(old, "clean", metadata=snapshot("openai"))
    old.migrate()
    db = AsyncDatabase(old.path)
    await db.connect()
    dao = BudgetsDAO(db)
    handle = BudgetHandle(dao, family, "owner_a", daily_limit=60000)
    before = await dao.attempts(family)
    dispatched = False
    try:
        state = await dao.snapshot(family, day=DAY)
        assert state["family"]["known_tokens"] == 10
        assert state["family"]["reserved_tokens"] == 90
        assert not state["family"]["usage_complete"]
        with budget_scope(handle), pytest.raises(BudgetExhausted, match="unresolved_attempt"):
            async with governed_request({"max_tokens": 4096}, protocol="anthropic"):
                dispatched = True
        assert not dispatched and await dao.attempts(family) == before
        with pytest.raises(BudgetExhausted, match="daily_accounting_incomplete"):
            await dao.reserve(family_id="family_clean", run_id="owner_clean", provider="safe",
                              model="safe", estimate=1, daily_limit=60000, day=DAY)
        future = (await dao.snapshot(day="2026-10-26"))["day"]
        assert future["accounting_resolved"] and future["usage_complete"]
        await dao.reserve(family_id="family_clean", run_id="owner_clean", provider="safe",
                          model="safe", estimate=1, daily_limit=60000, day="2026-10-26")
        with pytest.raises(ValueError, match="lower bound"):
            await dao.reconcile(target="attempt", target_id="a", liability_tokens=9, reason="review")
        await db.execute("UPDATE agent_runs SET status='running' WHERE id='owner_a'")
        with pytest.raises(ValueError, match="owner execution"):
            await dao.reconcile(target="attempt", target_id="a", liability_tokens=100, reason="review")
        await db.execute("UPDATE agent_runs SET status='completed' WHERE id='owner_a'")
        await dao.reconcile(target="attempt", target_id="a", liability_tokens=100,
                            reason="explicit offline liability authorization; usage remains uncertain")
        state = await dao.snapshot(family, day=DAY)
        assert state["family"]["accounting_resolved"] and not state["family"]["usage_complete"]
        assert state["day"]["accounting_resolved"] and not state["day"]["usage_complete"]
        assert (await dao.attempts(family))[0]["usage"] == before[0]["usage"]
    finally:
        await db.close()


def test_cli_lease_precedes_runtime_and_startup_migration_precedes_producers(tmp_path, monkeypatch):
    from minimax_code import __main__ as main
    from minimax_code import app
    from minimax_code.runtime import crash_detect, service_lease

    steps = []
    lease = SimpleNamespace(data_dir=tmp_path, close=lambda: steps.append("lease_closed"))
    marker = SimpleNamespace(mark_clean=lambda: steps.append("clean"))
    runtime_source = inspect.getsource(main.amain)
    monkeypatch.setattr(main, "parse_args", lambda: SimpleNamespace(log_level=None, mode="stdio"))
    monkeypatch.setattr(service_lease, "acquire_service_lease",
                        lambda path: steps.append("lease") or lease)
    monkeypatch.setattr(main, "configure_logging", lambda level: None)
    monkeypatch.setattr(crash_detect, "prepare_startup_marker", lambda value: marker)
    monkeypatch.setattr(crash_detect, "install_faulthandler", lambda path: None)
    monkeypatch.setattr(main, "_wire_xai_crash_handler", lambda version: None)

    async def runtime(*args, **kwargs):
        steps.append("runtime")
        return 0

    monkeypatch.setattr(main, "amain", runtime)
    with pytest.raises(SystemExit) as result:
        main.cli_entry()
    assert result.value.code == 0
    assert steps == ["lease", "runtime", "clean", "lease_closed"]
    # The real call chain migrates before profile setup / recovery / handler
    # admission. This guards ordering without booting optional live producers.
    opening = inspect.getsource(app._maybe_open_db)
    assert opening.index("await db.migrate()") < opening.index("await ensure_execution_profiles(db)")
    assert runtime_source.index("await init_runtime()") < runtime_source.index(
        "register_app_handlers(server)")


def test_competing_service_cannot_reach_migration_or_admission(tmp_path, monkeypatch):
    from minimax_code import __main__ as main
    from minimax_code.runtime.service_lease import acquire_service_lease

    monkeypatch.setenv("MINIMAX_CODE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(main, "parse_args", lambda: SimpleNamespace(log_level=None, mode="stdio"))

    async def forbidden_runtime(*args, **kwargs):
        pytest.fail("A competing service reached runtime/migration")

    monkeypatch.setattr(main, "amain", forbidden_runtime)
    with acquire_service_lease(tmp_path), pytest.raises(SystemExit) as result:
        main.cli_entry()
    assert result.value.code == 1
    assert not (tmp_path / "data.db").exists()
