"""Server-owned public agent profiles, executed by the existing Native loop."""
from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from pathlib import Path

from .model_snapshot import ModelSnapshot
from .skill_bindings import SkillBindings, check_bindings, skill_ids, tool_identity
from .skill_execution import SkillExecutionSpec, _digest


def _tool_identity(names):
    from .agent.tools import get_default_registry

    registry = get_default_registry()
    return tool_identity([registry.get(name) for name in names])


def canonical_intent(values, method):
    return {"method": method, **{key: copy.deepcopy(values.get(key)) for key in (
        "name", "agent_id", "request", "session_id", "parent_session_id",
        "context_message_id", "display_name", "model", "budget_tokens",
    )}}


@dataclass(frozen=True)
class AgentExecutionSpec:
    data: dict
    tools: tuple = ()

    def __post_init__(self):
        required = {"agent_id", "agent", "instructions", "model_snapshot", "permission_rules",
                    "workspace", "source_workspace", "source_identity", "workspace_id",
                    "project_id", "source_session_id", "execution_session_id", "intent",
                    "max_iterations", "system_prompt_extra", "prepared", "directory_identity",
                    "mock", "model_provider_fingerprint", "allowlist", "temperature", "tool_identity", "verified_models"}
        if (not isinstance(self.data, dict) or not required.issubset(self.data)
                or self.data.get("version") not in (1, 2)
                or self.data.get("digest") != _digest({k: v for k, v in self.data.items() if k != "digest"})):
            raise ValueError("Saved agent profile is incompatible; start a new task")
        if self.data["version"] == 2:
            check_bindings(self.data.get("skill_bindings"))
            if not isinstance(self.data.get("agent_instructions"), str):
                raise ValueError("Saved agent instructions are incompatible")
        elif self.data.get("skill_bindings") or self.data.get("skills"):
            raise ValueError("Legacy agent profiles support only known no-skills execution")

    def snapshot(self):
        return copy.deepcopy(self.data)

    @classmethod
    async def capture(cls, row, db, server, values, intent, workspace, project_id, session_id, workspace_id):
        from .agent.tools import get_default_registry
        from .ipc.builtins import _build_system_prompt_extra
        from .ipc.handlers_permissions import _ensure_permission_store
        from .models import context_window_for
        from .schedule_context import directory_identity, provider_fingerprint
        from .storage.dao.providers import ProviderDAO
        from .workspace_ctx import reset_current_root, set_current_root

        if not row.get("enabled", True):
            raise ValueError("Agent is disabled")
        from .app import init_runtime
        bindings = SkillBindings.capture(await init_runtime(), row.get("skills"))
        tools = bindings.candidates(get_default_registry(), row.get("tool_allowlist"))
        names = [t.name for t in tools]
        identity = tool_identity(tools)
        selected = ModelSnapshot.capture()
        provider = await ProviderDAO(db).get(selected.provider_id) if selected.provider_id else None
        if not selected.client.mock and (
            not provider or not provider.get("enabled")
            or selected.client.provider_id != provider["id"]
            or selected.client.protocol != provider["protocol"]
            or str(selected.client.base_url).rstrip("/") != str(provider["base_url"]).rstrip("/")
        ):
            raise ValueError("Agent model configuration is changing or unavailable")
        model = values.get("model") or row.get("model") or selected.model
        if model != selected.model and (not provider or model not in {m["id"] for m in provider["models"]}):
            raise ValueError("Agent model is not verified on the selected provider")
        selected = replace(selected, model=model, context_window=context_window_for(model))
        permissions = await _ensure_permission_store(server)
        if permissions is None:
            raise ValueError("Agent permission policy is unavailable")
        target = Path(workspace) / ".minimax" / "child-workspaces" / workspace_id / "workspace"
        token = set_current_root(Path(workspace))
        try:
            extra = await _build_system_prompt_extra(session_id=values["session_id"], project_id=project_id,
                workspace_advisory_override=str(target))
        finally:
            reset_current_root(token)
        data = dict(version=2, agent_id=row["id"], agent=row["name"],
                    instructions=row["system_prompt"] + bindings.instructions,
                    agent_instructions=row["system_prompt"], skill_bindings=bindings.data,
                    model_snapshot=selected.metadata(), permission_rules=copy.deepcopy(permissions.list_rules()),
                    workspace=str(target), source_workspace=workspace, source_identity=directory_identity(workspace),
                    workspace_id=workspace_id, project_id=project_id, source_session_id=values["session_id"],
                    execution_session_id=session_id, intent=intent, max_iterations=row.get("max_iterations") or 100,
                    system_prompt_extra=extra, prepared=False, directory_identity=None,
                    mock=bool(selected.client.mock), model_provider_fingerprint=provider_fingerprint(provider) if provider else None,
                    allowlist=names, tool_identity=identity, temperature=row.get("temperature"),
                    verified_models=sorted({selected.model, *(m["id"] for m in provider["models"])}) if provider else [selected.model],
                    client_run_id=values.get("run_id"),
                    permission_policy_version=1, hooks_policy="disabled")
        data["digest"] = _digest(data)
        return cls(data, tools), selected

    @classmethod
    async def restore(cls, data, runtime, db):
        if not isinstance(data, dict) or not data.get("prepared"):
            raise ValueError("Agent workspace preparation is unconfirmed; inspect it and start a new task")
        return await SkillExecutionSpec.restore.__func__(cls, data, runtime, db)

    async def validate(self, runtime, db):
        from . import secrets
        from .schedule_context import directory_identity, provider_fingerprint
        from .storage.dao.agents import AgentsDAO
        from .storage.dao.providers import ProviderDAO
        from .workspace.child import load_child

        self.__post_init__()
        row = await AgentsDAO(db).get(self.data["agent_id"])
        if row is None or not row.get("enabled", True):
            raise ValueError("Saved agent is unavailable or disabled; start a new task")
        if self.data["version"] == 1:
            if skill_ids(row.get("skills")):
                raise ValueError("Legacy agent dependencies changed; start a new task")
            try:
                if _tool_identity(self.data["allowlist"]) != self.data["tool_identity"]:
                    raise ValueError("Saved agent tool provider changed; start a new task")
            except KeyError as exc:
                raise ValueError("Saved agent tool provider is unavailable; start a new task") from exc
            tools = []
        else:
            from .agent.tools import get_default_registry
            bindings = SkillBindings(self.data["skill_bindings"]).validate(runtime, row.get("skills"))
            tools = bindings.candidates(get_default_registry(), self.data["allowlist"])
            if tool_identity(tools) != self.data["tool_identity"]:
                raise ValueError("Saved agent tool provider changed; start a new task")
        # Both supported profile versions obey current revocations. Additions
        # never expand the frozen accepted tool set.
        current = row.get("tool_allowlist")
        if current is not None and set(self.data["allowlist"]) - set(current):
            raise ValueError("Saved agent tool capability was revoked; start a new task")
        provider_id = self.data["model_snapshot"]["provider_id"]
        provider = await ProviderDAO(db).get(provider_id) if provider_id else None
        if self.data["model_provider_fingerprint"] is not None and (
            not provider or not provider.get("enabled") or provider_fingerprint(provider) != self.data["model_provider_fingerprint"]
        ):
            raise ValueError("Agent model provider changed; start a new task")
        if not self.data["mock"] and (not provider or not secrets.get_provider_key(provider_id)):
            raise ValueError("Agent provider credentials are unavailable")
        if self.data["prepared"]:
            child = load_child(Path(self.data["source_workspace"]), self.data["workspace_id"])
            if (child is None or str(child.root) != self.data["workspace"]
                    or directory_identity(str(child.root)) != self.data["directory_identity"]
                    or child.record["state"] in {"preparing", "collected", "needs_review"}):
                raise ValueError("Agent workspace identity is unconfirmed; inspect it and start a new task")
        return tools

    async def prepare(self, db, run_id):
        from .budget_ledger import owned_cleanup
        from .schedule_context import directory_identity
        from .storage.dao._base import dumps_json
        from .workspace.child import prepare_child

        if self.data["prepared"]:
            return self
        if directory_identity(self.data["source_workspace"]) != self.data["source_identity"]:
            raise ValueError("Source workspace identity changed before preparation")
        child = await prepare_child(Path(self.data["source_workspace"]), self.data["workspace_id"])
        data = {**self.data, "prepared": True, "directory_identity": directory_identity(str(child.root))}
        data["digest"] = _digest({k: v for k, v in data.items() if k != "digest"})
        spec = type(self)(data, self.tools)

        async def save():
            async with db.transaction() as conn:
                cursor = await conn.execute("SELECT metadata FROM agent_runs WHERE id=?", (run_id,))
                import json
                metadata = json.loads((await cursor.fetchone())["metadata"])
                metadata["agent_execution"] = spec.snapshot()
                await conn.execute("UPDATE agent_runs SET metadata=? WHERE id=?", (dumps_json(metadata), run_id))
                cursor = await conn.execute("UPDATE sessions SET workspace_path=? WHERE id=?", (data["workspace"], data["execution_session_id"]))
                if cursor.rowcount != 1:
                    raise RuntimeError("Agent execution session disappeared during preparation")
        await owned_cleanup(save())
        return spec

    def install(self, registry):
        allowlist = self.data["allowlist"]
        if self.data["version"] == 2:
            registry.clear()
            for tool in self.tools:
                registry.register(tool)
            return
        missing = set(allowlist) - set(registry.names())
        if missing:
            raise ValueError(f"Saved agent tools unavailable: {sorted(missing)}")
        # Run-local mutable tool objects; neither filtering nor installation
        # mutates global registry/provider instances.
        tools = [copy.deepcopy(registry.get(name)) for name in allowlist]
        registry.clear()
        for tool in tools:
            registry.register(tool)

    def configure_offline_review(self, client):
        pass

    def receipt(self, receipt, *, tool_calls=None):
        from .workspace.child import load_child
        result = {**receipt, "agent": self.data["agent"], "agent_id": self.data["agent_id"],
                  "agent_run_id": receipt.get("run_id"), "request": self.data["intent"]["request"],
                  "client_run_id": self.data.get("client_run_id"),
                  "parent_session_id": self.data["source_session_id"],
                  "context_message_id": self.data["intent"].get("context_message_id"),
                  "display_name": self.data["intent"].get("display_name") or self.data["agent"],
                  "status": receipt.get("run_status", "interrupted"), "stub": self.data["mock"],
                  "tool_calls": list(tool_calls) if tool_calls is not None else receipt.get("tool_calls", []),
                  "partial": bool(receipt.get("error") or receipt.get("cancelled") or receipt.get("truncated")
                                  or receipt.get("budget_paused") or (receipt.get("verification") or {}).get("requires_review")),
                  "budget_exhausted": bool(receipt.get("budget_paused"))}
        if self.data["prepared"]:
            child = load_child(Path(self.data["source_workspace"]), self.data["workspace_id"])
            if child is None:
                raise ValueError("Agent workspace ownership disappeared")
            if result["status"] in {"completed", "failed", "cancelled"}:
                child.finish("cancelled" if result.get("cancelled") else "failed" if result["partial"] else result["status"])
            result["workspace"] = child.summary()
        return result


def interrupted_receipt(profile, run, receipt):
    """Recovery never loads configuration, workspace manifests or model clients."""
    return terminal_receipt(profile, run, receipt, "interrupted")


def terminal_receipt(profile, run, receipt, status):
    """Project aliases from saved data, including failures before preparation."""
    data = profile if isinstance(profile, dict) else {}
    intent = data.get("intent") if isinstance(data.get("intent"), dict) else {}
    return {"text": "", **(receipt or {}), "session_id": run["session_id"],
            "message_id": run["assistant_message_id"], "run_id": run["id"],
            "run_status": status, "status": status, "partial": True,
            "cancelled": status == "cancelled", "agent": data.get("agent", "unknown"),
            "agent_id": data.get("agent_id", "unknown"), "agent_run_id": run["id"],
            "client_run_id": data.get("client_run_id"), "stub": data.get("mock", True),
            "request": intent.get("request"), "context_message_id": intent.get("context_message_id"),
            "display_name": intent.get("display_name") or data.get("agent", "unknown"),
            "parent_session_id": data.get("source_session_id")}
