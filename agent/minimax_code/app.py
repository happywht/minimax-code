"""Application bootstrap — wires IPC handlers to the server.

This module is the seam where the :mod:`minimax_code.ipc` layer
meets the rest of the agent. At startup it:

1. Spins up the storage layer (asyncio DB + migrations).
2. Builds a :class:`~.agent.skills.SkillRuntime` wired to the
   storage, the LLM client, and the global tool registry.
3. Loads the built-in skills + any user skills on disk.
4. Registers the IPC handlers: ``agent.*``, ``skill.*``, and
   ``task.*``.

The runtime is built lazily on the first ``skill.*`` call, so
tests can construct an :class:`IPCServer` without dragging in
storage or the LLM client. The application entry point
(:mod:`minimax_code.__main__`) calls :func:`init_runtime` once
at startup so the first user request is fast.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from .agent.skills import SkillRuntime, bootstrap

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy runtime singleton
# ---------------------------------------------------------------------------


_RUNTIME: SkillRuntime | None = None
_RUNTIME_LOCK = asyncio.Lock()
_PROVIDER_DAO_SINGLETON: Any = None  # type: ignore[no-untyped-def]
_REPO_MAP_INDEXER: Any = None  # type: ignore[no-untyped-def]


def get_runtime() -> SkillRuntime | None:
    """Return the process-wide :class:`SkillRuntime`, or ``None`` if not yet built."""
    return _RUNTIME


async def init_runtime(*, skills_root: Path | str | None = None) -> SkillRuntime:
    """Build the runtime once and cache it.

    Subsequent calls return the cached instance. Safe to call
    concurrently — guarded by an :class:`asyncio.Lock`.
    """
    global _RUNTIME
    async with _RUNTIME_LOCK:
        if _RUNTIME is not None:
            return _RUNTIME
        root = Path(skills_root) if skills_root else _default_skills_root()
        db = await _maybe_open_db()
        runtime = await bootstrap(
            skills_root=root,
            db=db,
            llm=None,
            tool_registry=None,
        )
        _RUNTIME = runtime
        return _RUNTIME


def set_runtime(runtime: SkillRuntime | None) -> None:
    """Replace the cached runtime (used by tests that inject a fake one)."""
    global _RUNTIME
    _RUNTIME = runtime


async def _maybe_open_db() -> Any:
    """Open the async database if storage can be initialised.

    As a side effect, this *also* populates the
    :data:`_PROGRESS_TRACKER` and :data:`_SESSIONS_DAO` singletons
    once a DB handle is available, so the ``task.*`` and
    ``session.*`` IPC handlers can find them without having to
    open the DB themselves.
    """
    if os.environ.get("MINIMAX_CODE_NO_DB") == "1":
        return None
    try:
        from .storage.db import AsyncDatabase, default_database_path
        from .storage.dao.sessions import SessionsDAO
        from .storage.dao.tasks import TaskDAO
        from .storage.dao.mobile_devices import MobileDeviceDAO
        from .mobile import (
            PairingManagerWithDAO,
            set_pairing_manager,
            set_mobile_dao,
        )
        from .progress import ProgressTracker
        from .agent.llm import MiniMaxClient
    except Exception:  # pragma: no cover — storage not yet bootstrapped
        logger.debug("storage layer not importable; running with in-memory skill registry")
        return None
    try:
        global _DB_SINGLETON
        db = AsyncDatabase(default_database_path())
        await db.connect()
        await db.migrate()
        _DB_SINGLETON = db
        # Spin up the process-wide progress tracker that backs
        # the ``task.*`` IPC namespace. Doing it here (next to
        # the DB open) keeps the singleton's lifetime tied to
        # the DB's lifetime.
        _set_progress_tracker(ProgressTracker(TaskDAO(db)))
        # Same lifecycle for the sessions DAO that backs the
        # ``session.*`` IPC namespace.
        _set_sessions_dao(SessionsDAO(db))
        # And for the mobile-pairing surface: a process-wide
        # :class:`PairingManager` wired to the device DAO. The
        # ``public_key`` is opaque for the PoC (Phase 2 swaps
        # in a verified cert fingerprint).
        mobile_dao = MobileDeviceDAO(db)
        set_mobile_dao(mobile_dao)
        set_pairing_manager(PairingManagerWithDAO(mobile_dao))
        # Build a ProviderDAO singleton so both ``model.*`` and
        # ``provider.*`` handlers can share it without opening a
        # second DB connection.
        from .storage.dao.providers import ProviderDAO
        global _PROVIDER_DAO_SINGLETON
        _PROVIDER_DAO_SINGLETON = ProviderDAO(db)
        # Sub-agent runtime — wire a process-wide MiniMaxClient
        # built from the stored model preference + provider config
        # (or fall back to defaults when no DB preference exists).
        _set_subagent_llm(await _rebuild_subagent_llm(db))
        return db
    except Exception:  # pragma: no cover — defensive
        logger.exception("failed to open storage; running with in-memory skill registry")
        return None


def _default_skills_root() -> Path:
    env = os.environ.get("MINIMAX_CODE_SKILLS_DIR")
    if env:
        return Path(env)
    # agent/minimax_code/app.py  ->  agent/skills/
    # parents[0] = agent/minimax_code/  parents[1] = agent/  parents[2] = repo root
    return Path(__file__).resolve().parents[2] / "agent" / "skills"


# ---------------------------------------------------------------------------
# Progress tracker singleton
# ---------------------------------------------------------------------------


_PROGRESS_TRACKER: Any = None  # type: ignore[no-untyped-def]
_DB_SINGLETON: Any = None  # process-wide AsyncDatabase


def get_db() -> Any:
    """Return the process-wide :class:`AsyncDatabase`, or ``None``."""
    return _DB_SINGLETON


def get_progress_tracker() -> Any:
    """Return the process-wide :class:`ProgressTracker`, or ``None``.

    The tracker is created by :func:`_maybe_open_db` the first
    time the storage layer is opened. Tests can replace it via
    :func:`set_progress_tracker`.
    """
    return _PROGRESS_TRACKER


def set_progress_tracker(tracker: Any) -> None:
    """Replace the cached progress tracker (test seam)."""
    global _PROGRESS_TRACKER
    _PROGRESS_TRACKER = tracker


def _set_progress_tracker(tracker: Any) -> None:
    """Internal setter used by :func:`_maybe_open_db`."""
    global _PROGRESS_TRACKER
    _PROGRESS_TRACKER = tracker


# ---------------------------------------------------------------------------
# Sessions DAO singleton
# ---------------------------------------------------------------------------


_SESSIONS_DAO: Any = None  # type: ignore[no-untyped-def]


def get_sessions_dao() -> Any:
    """Return the process-wide :class:`SessionsDAO`, or ``None``.

    The DAO is created by :func:`_maybe_open_db` the first time
    the storage layer is opened. Tests can replace it via
    :func:`set_sessions_dao`.
    """
    return _SESSIONS_DAO


def set_sessions_dao(dao: Any) -> None:
    """Replace the cached sessions DAO (test seam)."""
    global _SESSIONS_DAO
    _SESSIONS_DAO = dao


def _set_sessions_dao(dao: Any) -> None:
    """Internal setter used by :func:`_maybe_open_db`."""
    global _SESSIONS_DAO
    _SESSIONS_DAO = dao


# ---------------------------------------------------------------------------
# Sub-agent LLM singleton
# ---------------------------------------------------------------------------
#
# The ``agent.invoke`` IPC handler drives sub-agents through
# :class:`~minimax_code.orchestrator.subagent.SubAgentRuntime`. That
# runtime needs a :class:`MiniMaxClient` to make real model calls;
# without one it falls back to a deterministic stub response.
#
# The client is created once at process boot (from
# :func:`_maybe_open_db`, next to the other storage-bound
# singletons) so the underlying ``httpx.AsyncClient`` connection
# pool is shared across every ``agent.invoke`` request.

_SUBAGENT_LLM: Any = None  # type: ignore[no-untyped-def]


def get_subagent_llm() -> Any:
    """Return the process-wide sub-agent :class:`MiniMaxClient`, or ``None``.

    The client is created by :func:`_maybe_open_db` the first time
    the storage layer is opened. Tests can replace it via
    :func:`set_subagent_llm`.
    """
    return _SUBAGENT_LLM


def set_subagent_llm(llm: Any) -> None:
    """Replace the cached sub-agent LLM client (test seam)."""
    global _SUBAGENT_LLM
    _SUBAGENT_LLM = llm


def _set_subagent_llm(llm: Any) -> None:
    """Internal setter used by :func:`_maybe_open_db`.

    Also builds the :class:`SubAgentRuntime` singleton (lazily) so
    the IPC layer can grab a runtime that already has the LLM
    wired in. The runtime lives in
    :mod:`minimax_code.orchestrator.subagent`; we update it via
    :func:`minimax_code.orchestrator.subagent.set_subagent_runtime`.
    """
    global _SUBAGENT_LLM
    _SUBAGENT_LLM = llm
    try:
        from .orchestrator.subagent import SubAgentRuntime, set_subagent_runtime

        set_subagent_runtime(SubAgentRuntime(llm=llm))
    except Exception:  # pragma: no cover — defensive
        logger.debug("could not set subagent runtime; continuing")


# ---------------------------------------------------------------------------
# HTTP app singleton (v0.7.0 — mobile push needs WSManager)
# ---------------------------------------------------------------------------

_HTTP_APP: Any = None


def get_http_app() -> Any:
    """Return the process-wide FastAPI app, or ``None``."""
    return _HTTP_APP


def set_http_app(app: Any) -> None:
    """Replace the cached FastAPI app (called by ``__main__`` after build_app)."""
    global _HTTP_APP
    _HTTP_APP = app


# ---------------------------------------------------------------------------
# Provider DAO singleton
# ---------------------------------------------------------------------------


def get_provider_dao() -> Any:
    """Return the process-wide :class:`ProviderDAO`, or ``None``."""
    return _PROVIDER_DAO_SINGLETON


# ---------------------------------------------------------------------------
# Rebuild sub-agent LLM from stored preferences
# ---------------------------------------------------------------------------


async def _rebuild_subagent_llm(db: Any) -> Any:
    """Build a :class:`MiniMaxClient` from the stored model preference.

    Reads the current model + provider from the DB, resolves the
    protocol / base_url / api_key, and constructs a client ready for
    real API calls (or mock mode when no key is configured).

    Falls back to ``MiniMaxClient()`` defaults on any error so the
    agent always boots — even with a corrupt DB or missing provider.
    """
    try:
        from .storage.dao.model_prefs import ModelPrefsDAO
        from .storage.dao.providers import ProviderDAO
        from .agent.llm import MiniMaxClient
        from . import secrets

        prefs_dao = ModelPrefsDAO(db)
        pref = await prefs_dao.get_current()
        model_id = pref.get("model_id", "MiniMax-M3") if isinstance(pref, dict) else "MiniMax-M3"
        provider_id = pref.get("provider_id", "builtin-minimax") if isinstance(pref, dict) else "builtin-minimax"

        prov_dao = ProviderDAO(db)
        provider = await prov_dao.get(provider_id)

        if provider is None:
            logger.debug("provider %s not found; falling back to defaults", provider_id)
            return MiniMaxClient()

        protocol = provider.get("protocol", "anthropic")
        base_url = provider.get("base_url", "")
        api_key = secrets.get_provider_key(provider_id) or ""

        return MiniMaxClient(
            protocol=protocol,
            api_key=api_key or None,
            base_url=base_url or None,
            model=model_id,
        )
    except Exception:
        logger.exception("rebuild_subagent_llm failed; falling back to defaults")
        from .agent.llm import MiniMaxClient
        return MiniMaxClient()


async def rebuild_subagent_llm() -> Any:
    """Public helper — rebuild the sub-agent LLM client from stored prefs.

    Called after ``model.set_current`` and ``provider.*`` mutations so
    the next ``agent.invoke`` / ``agent.send_message`` uses the updated
    config.  No-op when the DB singleton is not yet available.
    """
    db = get_db()
    if db is None:
        return
    llm = await _rebuild_subagent_llm(db)
    _set_subagent_llm(llm)


# ---------------------------------------------------------------------------
# Handler registration
# ---------------------------------------------------------------------------


def register_app_handlers(server: Any, *, runtime: SkillRuntime | None = None) -> None:
    """Register all application-level JSON-RPC methods on ``server``.

    The ``skill.*`` handlers call :func:`init_runtime` on every
    invocation and cache the resulting runtime in
    :data:`_RUNTIME`. The ``task.*`` handlers look up the
    :class:`ProgressTracker` singleton in
    :data:`_PROGRESS_TRACKER`. Tests can pre-build and inject
    either via the matching ``runtime=`` / ``tracker=`` kwargs
    to skip the lazy path.
    """
    from .ipc.builtins import handle_agent_send_message, handle_agent_cancel
    from .ipc.handlers_agents import register_agent_handlers
    from .ipc.handlers_audit import register_audit_handlers
    from .ipc.handlers_webhooks import register_webhook_handlers
    from .ipc.handlers_git import register_git_handlers
    from .ipc.handlers_model import register_model_handlers
    from .ipc.handlers_patch import register_patch_handlers
    from .ipc.handlers_permissions import register_permission_handlers
    from .ipc.handlers_providers import register_provider_handlers
    from .ipc.handlers_runs import register_run_handlers
    from .ipc.handlers_scheduled import register_scheduled_handlers
    from .ipc.handlers_sessions import register_session_handlers
    from .ipc.handlers_skills import register_skill_handlers
    from .ipc.handlers_tasks import register_task_handlers
    from .ipc.handlers_secrets import register_secret_handlers
    from .ipc.handlers_workspace import register_workspace_handlers

    server.register("agent.send_message", handle_agent_send_message)
    server.register("agent.cancel", handle_agent_cancel)
    # The agent.* (sub-agent) namespace — config CRUD + invoke.
    # The DAO is built lazily on the first call (same pattern
    # as the other storage-backed namespaces).
    register_agent_handlers(server)
    if runtime is not None:
        set_runtime(runtime)
    # The skill handlers build the runtime themselves via
    # :func:`init_runtime` if it's not pre-set. The
    # ``runtime=`` argument is optional and only used as a
    # cache hint.
    register_skill_handlers(server, runtime=runtime)
    # The task handlers resolve the tracker lazily via
    # :func:`get_progress_tracker`, which is itself lazy (it
    # opens the DB on first call). Inject a tracker when you
    # need to bypass the storage layer (e.g. unit tests).
    register_task_handlers(server)
    # Run timeline read APIs — lets the frontend replay persisted
    # agent runs instead of relying only on live WebSocket events.
    register_run_handlers(server)
    # The session handlers resolve the sessions DAO via
    # :func:`get_sessions_dao` (also lazy, also opens the DB on
    # first call). Tests can inject a DAO via the ``dao=`` kwarg
    # to skip the lazy path.
    register_session_handlers(server)
    register_workspace_handlers(server)
    # The permission handlers lazily open the async DB and build a
    # :class:`~.permissions.PermissionStore` on first call. Tests
    # that pre-built a store can pass it via the ``store=`` kwarg
    # (see :func:`register_permission_handlers`).
    register_permission_handlers(server)
    # The schedule handlers build the JobScheduler lazily via
    # :func:`minimax_code.scheduler.get_scheduler`, which opens
    # the DB on first call. Tests can inject a scheduler with
    # the ``scheduler=`` kwarg to skip the lazy path.
    register_scheduled_handlers(server)
    # The mobile handlers lazily resolve the DAO + pairing manager
    # (or accept them via the ``dao=`` / ``manager=`` kwargs for
    # tests). Token cache is process-local; DB rows persist.
    from .ipc.handlers_mobile import register_mobile_handlers
    register_mobile_handlers(server)
    # The model handlers expose ``model.list`` / ``model.get_current``
    # / ``model.set_current`` and lazily open the async DB to build
    # a :class:`~.storage.dao.ModelPrefsDAO` on first call. Tests
    # can inject a DAO via the ``dao=`` kwarg to skip the lazy path.
    register_model_handlers(server, provider_dao=_PROVIDER_DAO_SINGLETON)
    # The secrets handlers expose ``secrets.status`` / ``secrets.set``
    # / ``secrets.clear`` for the Settings page's API-key tab. They
    # are stateless — every call goes straight to
    # :mod:`minimax_code.secrets`.
    register_secret_handlers(server)
    # The provider handlers expose ``provider.list`` / ``provider.create``
    # / ``provider.update`` / ``provider.delete`` / ``provider.set_api_key``
    # / ``provider.clear_api_key`` for the Settings page's Providers tab.
    # Pass the process-wide ProviderDAO singleton (if available) so
    # handlers reuse the same DB connection instead of opening extras.
    register_provider_handlers(server, dao=_PROVIDER_DAO_SINGLETON)
    # The git handlers expose ``git.status`` / ``git.diff`` /
    # ``git.log`` for the v0.3.0 code-review flow and the top-bar
    # ``GitStatusBar`` widget. Stateless — every call shells out
    # to ``git`` and parses the result.
    register_git_handlers(server)
    # Structured diff preview for UI approval/review surfaces. This
    # derives from git diff but keeps ``git.diff`` raw and backward
    # compatible for LLM/code-review flows.
    register_patch_handlers(server)
    # The audit handlers expose ``audit.list`` / ``audit.stats``
    # / ``audit.purge`` for the Settings page's Audit tab. The DAO
    # is built lazily on first call (same pattern as scheduled jobs).
    register_audit_handlers(server)
    # The webhook handlers expose ``webhook.list`` / ``webhook.create``
    # / ``webhook.update`` / ``webhook.delete`` /
    # ``webhook.regenerate_secret`` for the Settings page's Webhooks tab.
    # The DAO is built lazily on first call (same pattern as audit).
    register_webhook_handlers(server)
    # The notification handlers expose ``notification.list`` /
    # ``notification.mark_read`` / ``notification.mark_all_read`` /
    # ``notification.delete`` / ``notification.purge`` for the
    # NotificationBell / NotificationCenter UI.  The DAO is built lazily
    # on first call (same pattern as audit / webhooks).
    from .ipc.handlers_notifications import register_notification_handlers
    register_notification_handlers(server)
    # The workflow handlers expose ``workflow.list`` / ``workflow.create``
    # / ``workflow.update`` / ``workflow.delete`` / ``workflow.enable``
    # / ``workflow.disable`` / ``workflow.trigger`` for the Settings page's
    # Workflows tab.  The DAO is built lazily on first call (same pattern
    # as audit / webhooks / notifications).
    from .ipc.handlers_workflows import register_workflow_handlers
    register_workflow_handlers(server)
    # The team handlers expose ``team.list`` / ``team.create`` /
    # ``team.get`` / ``team.update`` / ``team.delete`` /
    # ``team.enable`` / ``team.disable`` for the Settings page's
    # Teams tab.  The DAO is built lazily on first call.
    from .ipc.handlers_teams import register_team_handlers
    register_team_handlers(server)
    logger.info(
        "registered application handlers "
        "(1 agent.* + 7 agent.* + 5 skill.* + 6 task.* + 5 session.* + 3 workspace.* + "
        "5 permission.* + 6 schedule.* + 7 mobile.* + 3 model.* + "
        "7 provider.* + 3 secrets.* + 3 git.* + 1 patch.* + 3 audit.* + 5 webhook.* + "
        "5 notification.* + 7 workflow.* + 7 team.*)"
    )


# ---------------------------------------------------------------------------
# Repo-Map indexer singleton (v0.4.0 Perception Engine)
# ---------------------------------------------------------------------------


def get_repo_map_indexer() -> Any:
    """Return the process-wide :class:`RepoMapIndexer`, or ``None``."""
    return _REPO_MAP_INDEXER


def set_repo_map_indexer(indexer: Any) -> None:
    """Replace the cached repo-map indexer (test seam)."""
    global _REPO_MAP_INDEXER
    _REPO_MAP_INDEXER = indexer


async def ensure_repo_map_indexer() -> Any:
    """Build the repo-map indexer on first call, cache it, and return it.

    The indexer walks the workspace and extracts a compressed symbol
    tree that is injected into the LLM's system prompt. Building the
    map is async-safe and idempotent — a second call returns the
    cached instance.
    """
    global _REPO_MAP_INDEXER
    if _REPO_MAP_INDEXER is not None:
        return _REPO_MAP_INDEXER
    try:
        from .agent.perception.indexer import RepoMapIndexer

        workspace = Path(os.environ.get("MINIMAX_CODE_WORKSPACE", os.getcwd()))
        indexer = RepoMapIndexer(workspace, max_tokens=2000)
        _REPO_MAP_INDEXER = indexer
        logger.info("repo-map indexer initialised (workspace=%s)", workspace)
        return indexer
    except Exception:
        logger.exception("failed to initialise repo-map indexer; continuing without")
        return None


__all__ = [
    "ensure_repo_map_indexer",
    "get_http_app",
    "get_progress_tracker",
    "get_provider_dao",
    "get_repo_map_indexer",
    "get_runtime",
    "get_sessions_dao",
    "get_subagent_llm",
    "init_runtime",
    "rebuild_subagent_llm",
    "register_app_handlers",
    "set_http_app",
    "set_progress_tracker",
    "set_repo_map_indexer",
    "set_runtime",
    "set_sessions_dao",
    "set_subagent_llm",
]
