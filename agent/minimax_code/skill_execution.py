"""Server-resolved skill profiles for the existing durable Native executor.

The client supplies intent only. This module copies executable configuration at
admission; it never delegates to SkillRuntime.invoke or mutates global tools.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from .model_snapshot import ModelSnapshot


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def canonical_request(params: dict) -> dict:
    """Stable intent fingerprint: deliberately excludes resolved live defaults."""
    diff = params.get("diff")
    prompt = params["request"]
    if diff is not None:
        if params["skill_id"] != "code-review:code-review" or not diff.strip():
            raise ValueError("A non-empty diff is supported only for code-review")
        prompt = (
            "Review the following unified diff. Identify bugs, style issues and complexity "
            "hot-spots. Use available analyzers when applicable.\n\n"
            f"User request: {prompt}\n\n```diff\n{diff}\n```"
        )
    intent = {
        key: copy.deepcopy(params.get(key))
        for key in (
            "skill_id",
            "request",
            "diff",
            "max_iterations",
            "model",
            "budget_tokens",
            "expected_workspace",
            "label",
        )
    }
    return {
        "session_id": params["session_id"],
        "mode": "execute",
        "content": prompt,
        "skill_intent": intent,
    }


def _provider_tools(runtime, skill):
    from .agent.tools import ToolRegistry

    provider = runtime._tool_providers.get(skill.skill_id)
    if provider is None:
        if skill.tools:
            raise ValueError("Skill tool provider is unavailable; start a new task after repair")
        return None, [], _digest(None)
    # Providers may contain mutable installer state. Never install a shared
    # provider or use its uninstall against another run's registry.
    provider = copy.deepcopy(provider)
    registry = ToolRegistry()
    installed = set(provider.install(registry))
    if set(registry.names()) != installed or not set(skill.tools).issubset(installed):
        raise ValueError("Skill provider did not supply its declared tools")
    identity = {
        "provider": f"{type(provider).__module__}.{type(provider).__qualname__}",
        "tools": registry.to_llm_functions(),
    }
    return provider, registry.list(), _digest(identity)


@dataclass(frozen=True)
class SkillExecutionSpec:
    data: dict
    tools: tuple = field(default=(), repr=False, compare=False)

    def __post_init__(self):
        data = self.data
        required = {
            "workspace",
            "project_id",
            "model_snapshot",
            "skill_id",
            "skill_version",
            "instructions",
            "manifest_tools",
            "provider_digest",
            "permission_rules",
            "permission_policy_version",
            "hooks_policy",
            "system_prompt_extra",
            "result_format",
            "diff",
            "directory_identity",
            "model_provider_fingerprint",
            "mock",
            "max_iterations",
            "digest",
        }
        if (
            not isinstance(data, dict)
            or not required.issubset(data)
            or data.get("version") != 1
            or data.get("digest") != _digest({k: v for k, v in data.items() if k != "digest"})
        ):
            raise ValueError("Saved skill profile is incompatible; start a new task")

    def snapshot(self):
        return copy.deepcopy(self.data)

    @classmethod
    async def capture(cls, runtime, db, server, params, workspace, project_id):
        from .ipc.handlers_permissions import _ensure_permission_store
        from .schedule_context import directory_identity, provider_fingerprint
        from .storage.dao.providers import ProviderDAO

        skill = runtime.registry.get(params["skill_id"])
        if (
            params.get("expected_workspace") is not None
            and params["expected_workspace"] != workspace
        ):
            raise ValueError("Session workspace changed since diff capture; start a new review")
        if not skill.enabled:
            raise ValueError("Skill is disabled")
        _, tools, provider_digest = _provider_tools(runtime, skill)
        selected = ModelSnapshot.capture()
        if params.get("model") and params["model"] != selected.model:
            raise ValueError("Select the requested model before invoking the skill")
        permissions = await _ensure_permission_store(server)
        if permissions is None:
            raise ValueError("Skill permission policy is unavailable")
        provider = await ProviderDAO(db).get(selected.provider_id) if selected.provider_id else None
        if not selected.client.mock and (not provider or not provider.get("enabled")):
            raise ValueError("Skill model provider is unavailable")
        if not selected.client.mock and (
            selected.client.provider_id != provider["id"]
            or selected.client.protocol != provider["protocol"]
            or str(selected.client.base_url).rstrip("/") != str(provider["base_url"]).rstrip("/")
        ):
            # provider.update commits before rebuilding the borrowed global
            # client. Refuse that window instead of saving a new fingerprint
            # while sending the first request through an old endpoint.
            raise ValueError(
                "Skill model configuration is changing; retry after selecting the provider again"
            )
        from pathlib import Path

        from .ipc.builtins import _build_system_prompt_extra
        from .workspace_ctx import reset_current_root, set_current_root

        token = set_current_root(Path(workspace))
        try:
            extra = await _build_system_prompt_extra(
                session_id=params["session_id"], project_id=project_id
            )
        finally:
            reset_current_root(token)
        data = {
            "version": 1,
            "skill_id": skill.skill_id,
            "skill_version": skill.version,
            "instructions": skill.instructions,
            "manifest_tools": list(skill.tools),
            "provider_tools": [tool.name for tool in tools],
            "provider_digest": provider_digest,
            "max_iterations": params.get("max_iterations") or 100,
            "result_format": "diff_review" if params.get("diff") is not None else "text",
            "diff": params.get("diff"),
            "model_snapshot": selected.metadata(),
            "label": params.get("label"),
            "model_provider_fingerprint": provider_fingerprint(provider) if provider else None,
            "mock": bool(selected.client.mock),
            "workspace": workspace,
            "directory_identity": directory_identity(workspace),
            "project_id": project_id,
            "permission_rules": copy.deepcopy(permissions.list_rules()),
            "system_prompt_extra": extra,
            "permission_policy_version": 1,
            "hooks_policy": "disabled",
        }
        data["digest"] = _digest(data)
        spec = cls(data, tuple(tools))
        return spec, selected

    @classmethod
    async def restore(cls, data, runtime, db):
        spec = cls(copy.deepcopy(data))
        tools = await spec.validate(runtime, db)
        from types import SimpleNamespace

        from . import secrets
        from .schedule_context import provider_fingerprint
        from .storage.dao.providers import ProviderDAO

        saved = data["model_snapshot"]
        provider = await ProviderDAO(db).get(saved["provider_id"]) if saved["provider_id"] else None
        if data["model_provider_fingerprint"] is not None and (
            not provider
            or not provider.get("enabled")
            or provider_fingerprint(provider) != data["model_provider_fingerprint"]
        ):
            raise ValueError("Skill model provider changed during restoration; start a new task")
        credential = "" if data["mock"] else secrets.get_provider_key(saved["provider_id"])
        if not data["mock"] and not credential:
            raise ValueError("Skill provider credentials are unavailable")
        client = SimpleNamespace(
            protocol="mock" if data["mock"] else provider["protocol"],
            api_key=credential,
            base_url=provider["base_url"] if provider else None,
            default_model=saved["model"],
            mock=data["mock"],
            timeout=180.0,
            max_retries=3,
        )
        client.provider_id = saved["provider_id"]
        return cls(copy.deepcopy(data), tuple(tools)), ModelSnapshot(
            client,
            saved["model"],
            saved["reasoning_effort"],
            saved["context_window"],
            saved["provider_id"],
            saved["protocol"],
        )

    async def validate(self, runtime, db):
        from . import secrets
        from .schedule_context import directory_identity, provider_fingerprint
        from .storage.dao.providers import ProviderDAO

        data = self.data
        if data.get("version") != 1 or data.get("digest") != _digest(
            {k: v for k, v in data.items() if k != "digest"}
        ):
            raise ValueError("Saved skill profile is incompatible; start a new task")
        if data.get("hooks_policy") != "disabled" or data.get("permission_policy_version") != 1:
            raise ValueError("Saved skill permission policy is incompatible; start a new task")
        try:
            skill = runtime.registry.get(data["skill_id"])
        except KeyError:
            raise ValueError("Saved skill is unavailable; start a new task") from None
        if (
            not skill.enabled
            or skill.version != data["skill_version"]
            or list(skill.tools) != data["manifest_tools"]
        ):
            raise ValueError("Skill is disabled or its version/tools changed; start a new task")
        _, tools, digest = _provider_tools(runtime, skill)
        if digest != data["provider_digest"]:
            raise ValueError("Skill tool provider changed; start a new task")
        if directory_identity(data["workspace"]) != data["directory_identity"]:
            raise ValueError("Skill workspace identity changed; start a new task")
        provider_id = data["model_snapshot"]["provider_id"]
        provider = await ProviderDAO(db).get(provider_id) if provider_id else None
        if data["model_provider_fingerprint"] is not None and (
            not provider
            or not provider.get("enabled")
            or provider_fingerprint(provider) != data["model_provider_fingerprint"]
        ):
            raise ValueError("Skill model provider changed; start a new task")
        if not data["mock"] and (not provider or not secrets.get_provider_key(provider_id)):
            raise ValueError("Skill provider credentials are unavailable")
        return tools

    def install(self, registry):
        for tool in self.tools:
            registry.register(tool)

    def configure_offline_review(self, client):
        if self.data["mock"] and self.data["result_format"] == "diff_review":
            client._transport = _LocalReviewTransport(self.data["diff"])

    def receipt(self, receipt, *, tool_calls=None):
        result = {
            **receipt,
            "skill_id": self.data["skill_id"],
            "output": receipt.get("text", ""),
            "status": receipt.get("run_status", "interrupted"),
            "budget_exhausted": bool(receipt.get("budget_paused")),
            "partial": bool(
                receipt.get("cancelled")
                or receipt.get("truncated")
                or receipt.get("budget_paused")
                or receipt.get("error")
                or (receipt.get("verification") or {}).get("requires_review")
            ),
            "tool_calls": len(tool_calls)
            if tool_calls is not None
            else receipt.get("tool_calls", 0),
        }
        if self.data["result_format"] == "diff_review":
            from .agent.skills._builtin.code_review import _diff_stats, _review_diff

            result["stats"] = _diff_stats(self.data["diff"])
            result["comments"] = (
                _review_diff(self.data["diff"])["comments"]
                if self.data["mock"] and result["status"] == "completed" and not result["partial"]
                else []
            )
        return result


def _private_model(selected):
    from .agent.llm import MiniMaxClient

    original = selected.client
    client = MiniMaxClient(
        require_budget=True,
        protocol=original.protocol,
        api_key=original.api_key,
        base_url=original.base_url,
        model=selected.model,
        timeout=original.timeout,
        max_retries=original.max_retries,
        mock=original.mock,
    )
    client.provider_id = selected.provider_id
    return ModelSnapshot(
        client,
        selected.model,
        selected.reasoning_effort,
        selected.context_window,
        selected.provider_id,
        selected.protocol,
    )


class _LocalReviewTransport:
    """Actual deterministic diff walk, run through the same single AgentCore."""

    thinking_count = 0
    last_reasoning_effort = None

    def __init__(self, diff):
        from .agent.skills._builtin.code_review import _review_diff

        self.text = _review_diff(diff)["text"]

    async def stream_chat(self, messages, **kwargs):
        from .agent.types import StreamChunk

        yield StreamChunk(delta=self.text)
        yield StreamChunk(
            finish_reason="stop",
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )

    async def close(self):
        pass


def interrupted_receipt(profile, run, receipt):
    """Pure recovery projection; never resolves or executes a saved profile."""
    base = {
        "session_id": run["session_id"],
        "message_id": run["assistant_message_id"],
        "run_id": run["id"],
        "text": "",
        "iterations": 0,
        "truncated": False,
        **(receipt or {}),
        "run_status": "interrupted",
        "cancelled": False,
        "budget_paused": False,
    }
    try:
        projected = SkillExecutionSpec(profile).receipt(base)
        for key in ("comments", "stats", "tool_calls"):
            if key in base:
                projected[key] = base[key]
        return {**projected, "partial": True}
    except (KeyError, TypeError, ValueError):
        # Old/corrupt metadata remains identifiable and explicitly incomplete.
        return {
            **base,
            "skill_id": profile.get("skill_id", "unknown")
            if isinstance(profile, dict)
            else "unknown",
            "output": base["text"],
            "status": "interrupted",
            "partial": True,
            "budget_exhausted": False,
            "tool_calls": 0,
            "error": base.get("error")
            or {"code": -32602, "message": "Saved skill profile is incompatible; start a new task"},
        }
