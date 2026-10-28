"""Server profiles freeze targets; no user switch bypasses missing governance."""
from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_run_service import Context

from minimax_code.execution_profiles import ExecutionProfileCatalog
from minimax_code.process_binding import HOST_TARGET
from minimax_code.process_types import ProcessBoundaryError, canonical_hash
from minimax_code.storage.db import AsyncDatabase


@pytest.fixture
async def async_db(tmp_path):
    db = AsyncDatabase(tmp_path / "profiles.sqlite")
    await db.connect()
    await db.migrate()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture(autouse=True)
def runtime_clock_fixture(monkeypatch):
    from minimax_code import process_preflight
    monkeypatch.setattr("minimax_code.process_deadline.read_clock", lambda: ("boot", 10))
    monkeypatch.setattr(process_preflight, "time_namespace", lambda: "namespace")


def config(tmp_path, **profile):
    (tmp_path / "execution-profiles.json").write_text(json.dumps({"version": 1, "profiles": [
        {"id": "linux-podman-l1", "image_id": "sha256:" + "a" * 64, **profile}]}))


def proven_catalog(tmp_path):
    if not (tmp_path / "execution-profiles.json").exists():
        config(tmp_path)
    catalog = ExecutionProfileCatalog(data_dir=tmp_path)
    catalog._load()
    catalog._manager = SimpleNamespace(identity=SimpleNamespace(install_id="installation"),
        _ready=True, _error=None, _runtime_evidence={"runtime": "fixture"}, _mapping_fingerprint="mapping")
    catalog._podman_reason = None
    from minimax_code.process_preflight import RuntimeAttestation, probe_argv_hash
    catalog._governor = RuntimeAttestation("preflight-run", "execution", canonical_hash(
        catalog._targets["linux-podman-l1"]), "installation", "boot", "namespace", probe_argv_hash(),
        canonical_hash(catalog._manager._runtime_evidence), "mapping")
    return catalog


async def test_missing_configuration_preserves_host_default(tmp_path):
    catalog = await ExecutionProfileCatalog(data_dir=tmp_path).initialize()
    assert catalog.resolve(None) == HOST_TARGET
    assert catalog.resolve("native-host") == HOST_TARGET
    cards = {card["id"]: card for card in catalog.cards()}
    assert cards["native-host"]["available"]
    assert not cards["linux-podman-l1"]["available"]
    assert cards["linux-podman-l1"]["unattended_supported"] is False


def test_missing_deadline_governance_cannot_be_bypassed_by_manual_profile(tmp_path):
    catalog = proven_catalog(tmp_path)
    catalog._governor = None
    cards = {card["id"]: card for card in catalog.cards()}
    assert not cards["linux-podman-l1"]["available"]
    assert cards["linux-podman-l1"]["reason"] == "deadline 治理未就绪"
    with pytest.raises(ProcessBoundaryError, match="deadline"):
        catalog.resolve("linux-podman-l1")
    with pytest.raises(ProcessBoundaryError, match="deadline"):
        catalog.resolve(None, parent_target=catalog._targets["linux-podman-l1"])


@pytest.mark.parametrize("damage", ["empty", "guard", "seccomp"])
def test_unaccepted_governor_pair_rejects_even_a_current_attestation(tmp_path, monkeypatch, damage):
    from minimax_code import process_preflight

    catalog = proven_catalog(tmp_path)
    target = catalog.resolve("linux-podman-l1")
    governor = target["governor"]
    pair = [governor["guard_sha256"], governor["seccomp_sha256"]]
    if damage != "empty":
        pair[0 if damage == "guard" else 1] = "0" * 64
    accepted = frozenset() if damage == "empty" else frozenset({tuple(pair)})
    monkeypatch.setattr(process_preflight, "ACCEPTED_GOVERNORS", accepted)
    assert not catalog.cards()[1]["available"]
    for resolve in (lambda: catalog.resolve("linux-podman-l1"),
                    lambda: catalog.assert_compatible(target, "installation")):
        with pytest.raises(ProcessBoundaryError, match="尚未通过产品验收"):
            resolve()
    assert catalog.resolve(None) == HOST_TARGET


@pytest.mark.parametrize("field", [
    "target_hash", "installation_id", "boot_id", "time_namespace", "probe_hash",
])
def test_accepted_pair_cannot_bypass_stale_runtime_attestation(tmp_path, field):
    catalog = proven_catalog(tmp_path)
    target = catalog.resolve("linux-podman-l1")
    catalog._governor = replace(catalog._governor, **{field: "stale"})
    assert not catalog.cards()[1]["available"]
    with pytest.raises(ProcessBoundaryError, match="运行时证明已失效"):
        catalog.assert_compatible(target, "installation")


def test_child_null_inherits_exact_parent_and_cannot_replace_it(tmp_path):
    catalog = proven_catalog(tmp_path)
    parent = catalog.resolve("linux-podman-l1")
    assert catalog.resolve(None, parent_target=parent) == parent
    assert catalog.resolve("linux-podman-l1", parent_target=parent) == parent
    with pytest.raises(ProcessBoundaryError, match="不能替换"):
        catalog.resolve("native-host", parent_target=parent)
    with pytest.raises(ProcessBoundaryError, match="不能替换"):
        catalog.resolve("linux-podman-l1", parent_target=HOST_TARGET)


def test_frozen_target_rejects_policy_changes_but_not_label_changes(tmp_path):
    catalog = proven_catalog(tmp_path)
    accepted = catalog.resolve("linux-podman-l1")
    config(tmp_path, label="Renamed display")
    renamed = proven_catalog(tmp_path)
    renamed._labels["linux-podman-l1"] = "Renamed display"
    renamed.assert_compatible(accepted, "installation")
    renamed._targets["linux-podman-l1"]["pids_limit"] = 32
    with pytest.raises(ProcessBoundaryError, match="策略改变"):
        renamed.assert_compatible(accepted, "installation")
    with pytest.raises(ProcessBoundaryError, match="其他安装"):
        catalog.assert_compatible(accepted, "another-installation")


@pytest.mark.parametrize("override", [{"env": {"HOME": "/host"}}, {"network": "host"}, {"_governor": True}])
async def test_configuration_has_no_arbitrary_runtime_or_governance_override(tmp_path, override):
    config(tmp_path, **override)
    catalog = await ExecutionProfileCatalog(data_dir=tmp_path).initialize()
    assert catalog.resolve(None) == HOST_TARGET
    assert not catalog.cards()[1]["available"]


async def test_catalog_is_loaded_only_once(tmp_path):
    catalog = await ExecutionProfileCatalog(data_dir=tmp_path).initialize()
    config(tmp_path)
    await catalog.initialize()
    with pytest.raises(ProcessBoundaryError, match="未知"):
        catalog.resolve("linux-podman-l1")


def test_unknown_disabled_and_corrupt_saved_targets_fail_closed(tmp_path):
    catalog = proven_catalog(tmp_path)
    with pytest.raises(ProcessBoundaryError, match="未知"):
        catalog.resolve("unknown-profile")
    target = catalog.resolve("linux-podman-l1")
    catalog._targets.pop("linux-podman-l1")
    with pytest.raises(ProcessBoundaryError, match="停用"):
        catalog.assert_compatible(target, "installation")
    with pytest.raises(ProcessBoundaryError, match="missing or incompatible"):
        catalog.assert_compatible(None, None)


async def test_agent_crud_profile_missing_null_and_unknown_id(async_db):
    from minimax_code.ipc.handlers_agents import register_agent_handlers
    from minimax_code.storage.dao.agents import AgentDAO, AgentsDAO

    handlers = {}
    server = SimpleNamespace(register=lambda name, handler: handlers.__setitem__(name, handler))
    dao = AgentDAO(async_db)
    register_agent_handlers(server, dao=dao)
    async def request(method, values):
        ctx = Context(server)
        await handlers[method](values, ctx)
        ctx.reply_error.assert_not_awaited()
        return ctx.reply.await_args.args[0]
    created = (await request("agent.create", {"name": "profile-worker", "system_prompt": "test"}))["agent"]
    assert created["execution_profile_id"] is None
    updated = (await request("agent.update", {"name": "profile-worker", "execution_profile_id": "linux-podman-l1"}))["agent"]
    assert updated["execution_profile_id"] == "linux-podman-l1"
    preserved = (await request("agent.update", {"name": "profile-worker", "description": "updated"}))["agent"]
    assert preserved["execution_profile_id"] == "linux-podman-l1"
    reset = (await request("agent.update", {"name": "profile-worker", "execution_profile_id": None}))["agent"]
    assert reset["execution_profile_id"] is None
    unknown = (await request("agent.update", {"name": "profile-worker", "execution_profile_id": "future-profile"}))["agent"]
    assert unknown["execution_profile_id"] == "future-profile"
    low = AgentsDAO(async_db)
    assert (await low.update(created["id"], description="preserve"))["execution_profile_id"] == "future-profile"
    assert (await low.update(created["id"], execution_profile_id=None))["execution_profile_id"] is None


@pytest.mark.parametrize("invalid", ["", "../profile", 123, False, {}, []])
async def test_invalid_profile_crud_does_not_erase_saved_config(async_db, invalid):
    from minimax_code.ipc.handlers_agents import register_agent_handlers
    from minimax_code.storage.dao.agents import AgentDAO

    dao = AgentDAO(async_db)
    await dao.upsert("profile-worker", execution_profile_id="linux-podman-l1")
    handlers = {}
    server = SimpleNamespace(register=lambda name, handler: handlers.__setitem__(name, handler))
    register_agent_handlers(server, dao=dao)
    ctx = Context(server)
    await handlers["agent.update"]({"name": "profile-worker", "execution_profile_id": invalid}, ctx)
    ctx.reply_error.assert_awaited_once()
    assert (await dao.get("profile-worker"))["execution_profile_id"] == "linux-podman-l1"

@pytest.mark.parametrize("field", ["_runtime_evidence", "_mapping_fingerprint"])
def test_runtime_or_mapping_drift_invalidates_private_attestation(tmp_path, field):
    catalog = proven_catalog(tmp_path)
    setattr(catalog._manager, field, {"changed": "runtime"} if field == "_runtime_evidence" else "changed")
    with pytest.raises(ProcessBoundaryError, match="运行时证明已失效"):
        catalog.resolve("linux-podman-l1")
