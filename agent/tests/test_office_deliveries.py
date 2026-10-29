"""Real temporary DB/vault generation, atomic receipts and historical authority."""
from __future__ import annotations

import asyncio
import hashlib
import threading
from unittest.mock import AsyncMock

import pytest

from minimax_code import office_deliveries as module
from minimax_code.execution import ExecutionPolicy, execution_scope
from minimax_code.ipc.protocol import OfficeGenerateParams
from minimax_code.office_deliveries import OfficeService
from minimax_code.office_xlsx import verify
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase


@pytest.fixture
async def office(tmp_path):
    db = AsyncDatabase(tmp_path / "data" / "test.db")
    await db.connect()
    await db.migrate()
    root = tmp_path / "workspace"
    root.mkdir()
    await ProjectsDAO(db).create(id="p", name="Project", root_path=str(root))
    await ProjectsDAO(db).create(id="other", name="Other", root_path=str(tmp_path))
    service = OfficeService(db, db.path.parent)
    identity = (await service.list("p"))["service_id"]
    yield service, db, root, identity
    await db.close()


def request(identity, **changes):
    values = {"project_id": "p", "expected_service_id": identity, "request_key": "intent_1",
              "filename": "清单.xlsx", "spec": {"sheet_name": "清单", "columns": [
                  {"name": "名称", "kind": "text"}, {"name": "数量", "kind": "number"}],
                  "rows": [["<script>literal</script>", 3], ["=1+1", 5]]}}
    values.update(changes)
    return OfficeGenerateParams.model_validate(values)


async def existing_run(db, root, *, project="p", mode="execute", engine="native", metadata=None):
    await SessionsDAO(db).create(id="s", project_id=project, workspace_path=str(root), engine=engine)
    return await AgentRunsDAO(db).create_run(id="r", session_id="s", mode=mode,
                                           metadata={} if metadata is None else metadata)


async def test_generate_register_restart_repeat_and_original_preservation(office):
    service, db, root, identity = office
    sentinel = root / "清单.xlsx"
    sentinel.write_bytes(b"preexisting workspace original")
    original = sentinel.read_bytes()
    first = await service.generate(request(identity))
    attachment, data = await service.originals.content("p", first["attachment"]["id"])
    assert first["delivery"]["validation_report"] == verify(data, request(identity).spec)
    assert first["delivery"]["run_id"] is None and attachment["quality_status"] == "unverified"
    assert first["delivery"]["validation_report"]["business_quality"] == "unverified"
    assert sentinel.read_bytes() == original
    repeated = await service.generate(request(identity))
    assert repeated == first
    second = await service.generate(request(identity, request_key="intent_2"))
    assert second["attachment"]["version"] == 2
    assert first["attachment"]["snapshot_id"] != second["attachment"]["snapshot_id"]
    assert len((await service.list("p"))["deliveries"]) == 2
    await db.close()
    await db.connect()
    restarted = OfficeService(db, db.path.parent)
    assert await restarted.generate(request(identity)) == first
    assert (await restarted.originals.content("p", attachment["id"]))[1] == data
    with pytest.raises(ValueError, match="this project"):
        await restarted.get("other", first["delivery"]["id"])
    with pytest.raises(ValueError, match="different input"):
        await restarted.generate(request(identity, filename="changed.xlsx"))
    assert len(await db.fetchall("SELECT * FROM attachments")) == 2


@pytest.mark.parametrize("root", ["", "relative/project", "."])
async def test_generation_requires_explicit_absolute_project_root(office, monkeypatch, root):
    service, db, _, identity = office
    await db.execute("UPDATE projects SET root_path=? WHERE id='p'", (root,))
    worker = AsyncMock(side_effect=AssertionError("invalid project must not start worker"))
    monkeypatch.setattr(module, "generate_xlsx", worker)
    with pytest.raises(ValueError, match="explicit absolute project"):
        await service.generate(request(identity))
    worker.assert_not_awaited()
    assert not service.originals.vault.exists()


async def test_actual_generated_file_bundle7_exact_restore_and_read(office, tmp_path):
    from minimax_code import bundle

    service, db, _, identity = office
    first = await service.generate(request(identity))
    original = (await service.originals.content("p", first["attachment"]["id"]))[1]
    data_dir = db.path.parent
    # Bundle requires its canonical data.db name, matching a real product data dir.
    await db.close()
    (data_dir / "test.db").rename(data_dir / "data.db")
    archive = tmp_path / "generated.zip"
    assert bundle.create_bundle(data_dir, archive)["office_delivery_count"] == 1
    assert bundle.verify_bundle(archive)["format_version"] == 7
    target = tmp_path / "restored"
    bundle.restore_bundle(archive, target)
    restored = AsyncDatabase(target / "data.db")
    await restored.connect()  # Data-only restore did not migrate or run generator.
    try:
        recovered = OfficeService(restored, target)
        assert await recovered.get("p", first["delivery"]["id"]) == first
        assert (await recovered.originals.content("p", first["attachment"]["id"]))[1] == original
        assert await recovered.generate(request(identity)) == first
    finally:
        await restored.close()


async def test_existing_run_reference_never_completes_or_executes_it(office, monkeypatch):
    service, db, root, identity = office
    run = await existing_run(db, root)
    from minimax_code.agent.llm import MiniMaxClient

    model = AsyncMock(side_effect=AssertionError("must not invoke a model"))
    monkeypatch.setattr(MiniMaxClient, "chat", model)
    result = await service.generate(request(identity, run_id=run["id"]))
    assert result["delivery"]["run_id"] == "r"
    saved = await AgentRunsDAO(db).get_run("r")
    assert saved["status"] == run["status"] and await AgentRunsDAO(db).result_payload("r") is None
    model.assert_not_awaited()
    await db.execute("DELETE FROM agent_runs WHERE id='r'")
    assert await service.generate(request(identity, run_id="r")) == result
    assert (await service.get("p", result["delivery"]["id"]))["delivery"]["run_id"] == "r"


@pytest.mark.parametrize("reason", ["missing", "foreign", "workspace", "plan", "codex", "admitted", "internal", "corrupt", "duplicate"])
async def test_run_association_refuses_authority_expansion_before_worker(office, monkeypatch, reason):
    service, db, root, identity = office
    if reason != "missing":
        await existing_run(db, root if reason != "workspace" else root.parent,
                           project="other" if reason == "foreign" else "p",
                           mode="plan" if reason == "plan" else "execute",
                           engine="codex" if reason == "codex" else "native")
        raw = {"admitted": '{"agent_execution":{}}', "internal": '{"process_execution_owner":{}}',
               "corrupt": "not-json", "duplicate": '{"x":1,"x":2}'}.get(reason)
        if raw:
            await db.execute("UPDATE agent_runs SET metadata=? WHERE id='r'", (raw,))
    generate = AsyncMock(side_effect=AssertionError("no worker expected"))
    monkeypatch.setattr(module, "generate_xlsx", generate)
    with pytest.raises((ValueError, OSError)):
        await service.generate(request(identity, run_id="r"))
    generate.assert_not_awaited()
    assert not await db.fetchall("SELECT * FROM office_deliveries")
    assert not service.originals.vault.exists()


async def test_read_only_and_wrong_service_fail_before_generation(office, monkeypatch):
    service, db, root, identity = office
    generate = AsyncMock()
    monkeypatch.setattr(module, "generate_xlsx", generate)
    with execution_scope(ExecutionPolicy(workspace=root, read_only=True)), pytest.raises(PermissionError):
        await service.generate(request(identity))
    with pytest.raises(ValueError, match="identity changed"):
        await service.generate(request("svc_" + "0" * 32))
    generate.assert_not_awaited()
    assert not await db.fetchall("SELECT * FROM office_deliveries")


async def test_receipt_insert_failure_rolls_back_attachment_and_retains_only_private_orphan(office, monkeypatch):
    service, db, _, identity = office
    original = db._conn.execute

    def fail(sql, *args, **kwargs):
        if sql.startswith("INSERT INTO office_deliveries"):
            raise RuntimeError("injected commit-path failure")
        return original(sql, *args, **kwargs)

    monkeypatch.setattr(db._conn, "execute", fail)
    with pytest.raises(RuntimeError):
        await service.generate(request(identity))
    assert not await db.fetchall("SELECT * FROM attachments")
    assert not await db.fetchall("SELECT * FROM office_deliveries")
    files = list(service.originals.vault.iterdir())
    assert len(files) == 1 and not files[0].name.startswith(".pending-")
    assert files[0].stat().st_size > 0  # Explicitly retained orphan, not a delivered row.


async def test_project_rebind_during_generation_never_commits(office, monkeypatch):
    service, db, root, identity = office
    original = module.generate_xlsx

    async def change(spec):
        result = await original(spec)
        await db.execute("UPDATE projects SET root_path=? WHERE id='p'", (str(root.parent),))
        return result

    monkeypatch.setattr(module, "generate_xlsx", change)
    with pytest.raises(ValueError, match="binding changed"):
        await service.generate(request(identity))
    assert not await db.fetchall("SELECT * FROM attachments")
    assert not await db.fetchall("SELECT * FROM office_deliveries")


async def test_same_key_cannot_silently_rebind_to_changed_project_root(office):
    service, db, root, identity = office
    first = await service.generate(request(identity))
    await db.execute("UPDATE projects SET root_path=? WHERE id='p'", (str(root.parent),))
    with pytest.raises(ValueError, match="different input"):
        await service.generate(request(identity))
    assert await service.get("p", first["delivery"]["id"]) == first
    assert len(await db.fetchall("SELECT * FROM office_deliveries")) == 1


@pytest.mark.parametrize("reason", ["relative", "oversized", "history", "nested", "nonfinite"])
async def test_run_association_history_is_complete_bounded_and_strict(office, monkeypatch, reason):
    from pathlib import Path

    service, db, root, identity = office
    await existing_run(db, root)
    if reason == "relative":
        # A relative path that would otherwise resolve to the same real root,
        # independent of the drive chosen for pytest's temporary directory.
        await db.execute("UPDATE projects SET root_path=? WHERE id='p'", (str(Path.cwd()),))
        await db.execute("UPDATE sessions SET workspace_path='.' WHERE id='s'")
    elif reason == "history":
        for index in range(256):
            await AgentRunsDAO(db).create_run(id=f"historical_{index}", session_id="s", mode="chat", metadata={})
    else:
        raw = {"oversized": '{"padding":"' + "x" * 65536 + '"}',
               "nested": '{"nest":' + "[" * 1100 + "0" + "]" * 1100 + "}",
               "nonfinite": '{"value":NaN}'}[reason]
        await db.execute("UPDATE agent_runs SET metadata=? WHERE id='r'", (raw,))
    worker = AsyncMock(side_effect=AssertionError("invalid association must not start worker"))
    monkeypatch.setattr(module, "generate_xlsx", worker)
    with pytest.raises(ValueError):
        await service.generate(request(identity, run_id="r"))
    worker.assert_not_awaited()
    assert not service.originals.vault.exists()


async def test_concurrent_same_intent_is_one_committed_version(office):
    service, db, _, identity = office
    first, second = await asyncio.gather(service.generate(request(identity)), service.generate(request(identity)))
    assert first == second
    assert len(await db.fetchall("SELECT * FROM attachments")) == 1
    assert len(await db.fetchall("SELECT * FROM office_deliveries")) == 1


async def test_cancellation_during_publish_drains_handles_without_receipt(office, monkeypatch):
    service, db, _, identity = office
    entered, release = threading.Event(), threading.Event()
    original = module._Upload.publish

    def slow(upload):
        entered.set()
        release.wait(5)
        return original(upload)

    monkeypatch.setattr(module._Upload, "publish", slow)
    task = asyncio.create_task(service.generate(request(identity)))
    while not entered.is_set():
        await asyncio.sleep(.01)
    task.cancel()
    await asyncio.sleep(.02)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not await db.fetchall("SELECT * FROM attachments")
    assert not await db.fetchall("SELECT * FROM office_deliveries")
    assert not any(path.name.startswith(".pending-") for path in service.originals.vault.iterdir())
    assert module._GATE.acquire(blocking=False)
    module._GATE.release()


async def test_repeated_cancellation_waits_for_atomic_transaction_rollback(office, monkeypatch):
    service, db, _, identity = office
    inserted, rolling_back, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    execute, rollback = db._conn.execute, db._conn.rollback

    def delayed(sql, *args, **kwargs):
        if sql.startswith("INSERT INTO office_deliveries"):
            async def hold():
                inserted.set()
                await asyncio.Event().wait()
            return hold()
        return execute(sql, *args, **kwargs)

    async def slow_rollback():
        rolling_back.set()
        await release.wait()
        return await rollback()

    monkeypatch.setattr(db._conn, "execute", delayed)
    monkeypatch.setattr(db._conn, "rollback", slow_rollback)
    task = asyncio.create_task(service.generate(request(identity)))
    await asyncio.wait_for(inserted.wait(), 10)
    task.cancel()
    await asyncio.wait_for(rolling_back.wait(), 5)
    task.cancel()
    await asyncio.sleep(.02)
    assert not task.done()  # Retained commit owner must finish rollback first.
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not await db.fetchall("SELECT * FROM attachments")
    assert not await db.fetchall("SELECT * FROM office_deliveries")
    assert not db._conn.in_transaction
    assert module._GATE.acquire(blocking=False)
    module._GATE.release()


async def test_selected_content_rejects_actual_snapshot_corruption(office):
    service, _, _, identity = office
    result = await service.generate(request(identity))
    path = service.originals.vault / result["attachment"]["snapshot_id"]
    path.write_bytes(b"different bytes")
    with pytest.raises(ValueError, match="corrupt"):
        await service.get("p", result["delivery"]["id"])
    assert hashlib.sha256(path.read_bytes()).hexdigest() != result["attachment"]["sha256"]
