"""Immutable local schedule context. Never accepts authority from model/RPC payloads."""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .model_snapshot import ModelSnapshot
from .storage.dao.permissions import PermissionRuleDAO
from .storage.dao.projects import ProjectsDAO
from .storage.dao.providers import ProviderDAO
from .workspace_ctx import require_directory

scheduled_runtime: ContextVar[dict | None] = ContextVar("scheduled_runtime", default=None)


def validate_payload(payload: Any) -> str:
    if not isinstance(payload, dict) or set(payload) != {"prompt"}:
        raise ValueError(
            "Only local Native prompt schedules are supported; recreate unsupported payloads"
        )
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("A non-empty prompt is required")
    return prompt


def directory_identity(root: str) -> list[int]:
    stat = Path(root).stat()
    return [stat.st_dev, stat.st_ino]


def provider_fingerprint(provider: dict) -> str:
    data = {key: provider.get(key) for key in ("id", "protocol", "base_url")}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


async def capture_context(db, options: dict) -> dict:
    if not isinstance(options, dict):
        raise ValueError("Schedule context must be an object")
    if options.get("host", "local") != "local" or options.get("engine", "native") != "native":
        raise ValueError("Schedules support Local / Native only")
    project = await ProjectsDAO(db).get(str(options.get("project_id") or ""))
    if not project or project["id"] == "inbox" or not project.get("root_path"):
        raise ValueError("Bind an explicit project with an existing workspace")
    root = str(require_directory(project["root_path"]))
    budget = options.get("budget_tokens")
    if budget is None:
        raw = os.environ.get("MINIMAX_CODE_SCHEDULED_BUDGET_TOKENS") or os.environ.get(
            "MINIMAX_CODE_RUN_BUDGET_TOKENS"
        )
        budget = int(raw) if raw and raw.isdigit() else None
    if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
        raise ValueError("Scheduled tasks require an explicit positive token budget")
    mode = options.get("mode", "execute")
    if mode not in {"chat", "execute", "plan"}:
        raise ValueError("Unsupported schedule mode")
    timezone = str(options.get("timezone") or "Asia/Shanghai")
    ZoneInfo(timezone)
    if bool(options.get("provider_id")) != bool(options.get("model")):
        raise ValueError("Provider and model must be selected together")
    if options.get("provider_id") and options.get("model"):
        provider_id, model = options["provider_id"], options["model"]
        effort = options.get("reasoning_effort")
    else:
        selected = ModelSnapshot.capture()
        provider_id, model, effort = selected.provider_id, selected.model, selected.reasoning_effort
    provider = await ProviderDAO(db).get(str(provider_id))
    if not provider or not provider.get("enabled") or not provider.get("base_url"):
        raise ValueError("Selected provider is unavailable")
    from .models import context_window_for
    from .permissions import PermissionStore

    permissions = PermissionStore(PermissionRuleDAO(db))
    await permissions.warm()
    return {
        "version": 1,
        "host": "local",
        "engine": "native",
        "project_id": project["id"],
        "workspace": root,
        "directory_identity": directory_identity(root),
        "provider_id": provider_id,
        "protocol": provider["protocol"],
        "provider_fingerprint": provider_fingerprint(provider),
        "model": str(model),
        "reasoning_effort": effort,
        "context_window": context_window_for(str(model)),
        "mode": mode,
        "budget_tokens": budget,
        "validate_command": project.get("validate_command") or "",
        "permission_policy_version": 1,
        "permission_rules": permissions.list_rules(),
        "hooks_policy": "disabled",
        "timezone": timezone,
    }


async def validate_context(db, context: dict) -> dict:
    if (
        context.get("version") != 1
        or context.get("host") != "local"
        or context.get("engine") != "native"
    ):
        raise ValueError("Schedule requires context rebind")
    if context.get("permission_policy_version") != 1:
        raise ValueError("Permission policy version changed; rebind schedule")
    if context.get("hooks_policy") != "disabled":
        raise ValueError("Schedule has no authorized hook policy; rebind schedule")
    if (
        isinstance(context.get("budget_tokens"), bool)
        or not isinstance(context.get("budget_tokens"), int)
        or context["budget_tokens"] <= 0
    ):
        raise ValueError("Schedule has no positive budget")
    project = await ProjectsDAO(db).get(context["project_id"])
    root = str(require_directory(context["workspace"]))
    if (
        not project
        or str(require_directory(project["root_path"])) != root
        or directory_identity(root) != context["directory_identity"]
    ):
        raise ValueError("Project/workspace identity changed; rebind schedule")
    provider = await ProviderDAO(db).get(context["provider_id"])
    if (
        not provider
        or not provider.get("enabled")
        or provider_fingerprint(provider) != context["provider_fingerprint"]
    ):
        raise ValueError("Provider configuration changed; rebind schedule")
    return provider


async def resolve_model(db, context: dict) -> ModelSnapshot:
    provider = await validate_context(db, context)
    from . import secrets
    from .agent.llm import MiniMaxClient

    key = secrets.get_provider_key(provider["id"])
    if not key:
        raise ValueError("Scheduled provider credentials are unavailable")
    client = MiniMaxClient(
        protocol=provider["protocol"],
        base_url=provider["base_url"],
        api_key=key,
        model=context["model"],
        mock=False,
    )
    if client.mock:
        await client.close()
        raise ValueError("Production schedules cannot fall back to a mock model")
    client.provider_id = provider["id"]
    return ModelSnapshot(
        client,
        context["model"],
        context.get("reasoning_effort"),
        context["context_window"],
        provider["id"],
        provider["protocol"],
    )


class FrozenPermissions:
    def __init__(self, rules: list[dict], current: Any):
        self.rules, self.current = rules, current

    def lookup(self, tool: str):
        # Revocations can tighten a schedule; later global allows cannot widen it.
        current = self.current.lookup(tool) or {"action": "ask"}
        if current and current.get("action") == "deny":
            return current
        saved = next(
            (rule for rule in self.rules if fnmatch.fnmatchcase(tool, rule["tool_pattern"])),
            {"tool_pattern": tool, "action": "ask"},
        )
        if saved["action"] == "deny":
            return saved
        return {"tool_pattern": tool, "action": "ask"} if current["action"] == "ask" else saved

    def is_allowed(self, tool: str, **kwargs):
        return self.lookup(tool)["action"] != "deny"

    def is_denied(self, tool: str, **kwargs):
        return self.lookup(tool)["action"] == "deny"


class ScheduledChildren:
    """One mutable owner inherited recursively by every scheduled descendant."""

    def __init__(self):
        self.tasks = {}
        self.cancelling = False
        self._cancelled_tasks = set()
        self._drain_task = None

    def register(self, run_id, task):
        self.tasks[task] = run_id
        if self.cancelling:
            self._cancel_once(task)

    def _cancel_once(self, task):
        if task not in self._cancelled_tasks and not task.done():
            self._cancelled_tasks.add(task)
            # A child may already be reaping a cancelled process. Repeating
            # cancellation can interrupt its cleanup instead of stopping work.
            if not task.cancelling():
                task.cancel()

    def _cancel_pending(self):
        self.cancelling = True
        for task in self.tasks:
            self._cancel_once(task)

    async def drain(self, *, cancel=False):
        if cancel:
            self._cancel_pending()
        if self._drain_task is None:
            self._drain_task = asyncio.create_task(self._drain())
        cancelled = None
        while not self._drain_task.done():
            try:
                await asyncio.shield(self._drain_task)
            except asyncio.CancelledError as exc:
                cancelled = cancelled or exc
                self._cancel_pending()
        evidence = self._drain_task.result()
        if cancelled is not None:
            raise cancelled
        return evidence

    async def _drain(self):
        while pending := [task for task in self.tasks if not task.done()]:
            if self.cancelling:
                for task in pending:
                    self._cancel_once(task)
            # Each child owns cancellation of its tools/process tree. Retain the
            # parent lease/client/gater until that cleanup has actually drained.
            await asyncio.gather(*pending, return_exceptions=True)
        evidence = []
        for task, run_id in self.tasks.items():
            if task.cancelled():
                evidence.append({"run_id": run_id, "requires_review": True, "reason": "cancelled"})
                continue
            if error := task.exception():
                evidence.append({"run_id": run_id, "requires_review": True, "reason": str(error)})
                continue
            result = task.result()
            result = result if isinstance(result, dict) else {}
            workspace = result.get("workspace") or {}
            if workspace.get("path") and workspace.get("run_id"):
                try:
                    from .workspace.child import load_child

                    live = load_child(Path(workspace["path"]).parents[3], workspace["run_id"])
                    if live is not None:
                        workspace = live.summary()
                except (OSError, ValueError, IndexError):
                    workspace = {**workspace, "state": "needs_review"}
            review = (
                bool((result.get("verification") or {}).get("requires_review"))
                or (not result.get("reported") and not result.get("text"))
                or any(result.get(key) for key in ("error", "cancelled", "partial", "truncated"))
                or workspace.get("state") in {"failed", "cancelled", "needs_review"}
                or (
                    workspace.get("collection_required")
                    and workspace.get("files_written")
                    and workspace.get("state") != "collected"
                )
            )
            evidence.append(
                {
                    "run_id": run_id,
                    "requires_review": bool(review),
                    "reported": bool(result.get("reported")),
                    **({"workspace": workspace} if workspace else {}),
                    **(
                        {"files_written": result["files_written"]}
                        if "files_written" in result
                        else {}
                    ),
                    **({"text": str(result["text"])} if result.get("text") else {}),
                }
            )
        return evidence
