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
_DB_SINGLETON: Any = None  # process-wide AsyncDatabase
_DB_LOCK = asyncio.Lock()
# Plugin registry singleton (platform pillar #3 — Plugins). Lazily
# built by ensure_plugin_registry(); tests inject via set_plugin_registry().
_PLUGIN_REGISTRY: Any = None  # type: ignore[no-untyped-def]
# Hook manager singleton (platform pillar #2 — Hooks, wired end-to-end in
# R10). Lazily built by ensure_hook_manager(); tests inject via
# set_hook_manager(). Plugins contribute their hooks into this manager's
# registry so plugin lifecycle hooks become agent-active with one build.
_HOOK_MANAGER: Any = None  # type: ignore[no-untyped-def]
# Telemetry engine singleton (R11 — observability pillar). Lazily built by
# ensure_telemetry_engine(); tests inject via set_telemetry_engine().
# In-memory, fail-open event bus: session lifecycle, tool dispatch, hook
# fire, permission. Optional everywhere — None means disabled, zero overhead.
_TELEMETRY_ENGINE: Any = None  # type: ignore[no-untyped-def]
# Boot-time crash recovery snapshot (R12 — reliability pillar). Populated
# exactly once by _run_crash_recovery() inside _maybe_open_db(); exposed
# read-only via get_recovery_result() so the runtime.* IPC handlers can
# surface "recovered N orphan runs after an unexpected shutdown" without
# re-running recovery. None = recovery has not run yet this boot.
_RECOVERY_RESULT: Any = None  # type: ignore[no-untyped-def]


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
        db = await ensure_db()
        runtime = await bootstrap(
            skills_root=root,
            extra_roots=[_custom_skills_root()],
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
    db: Any = None
    try:
        from .mobile import (
            PairingManagerWithDAO,
            set_mobile_dao,
            set_pairing_manager,
        )
        from .progress import ProgressTracker
        from .storage.dao.mobile_devices import MobileDeviceDAO
        from .storage.dao.sessions import SessionsDAO
        from .storage.dao.tasks import TaskDAO
        from .storage.db import AsyncDatabase, default_database_path
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
        # R12 — boot-time crash recovery. Fail-open by design: the inner
        # call already swallows its own errors, and this outer guard is
        # belt-and-suspenders so a recovery fault never blocks boot.
        # Runs exactly once per boot; snapshots into _RECOVERY_RESULT
        # for the runtime.* IPC handlers.
        try:
            await _run_crash_recovery(db)
        except Exception:
            logger.exception("crash recovery orchestration failed (fail-open)")
        return db
    except Exception:  # pragma: no cover — defensive
        logger.exception("failed to open storage; running with in-memory skill registry")
        if db is not None:
            try:
                await db.close()
            except Exception:
                logger.debug("failed to close partially initialised storage", exc_info=True)
            if _DB_SINGLETON is db:
                _DB_SINGLETON = None
        return None


def _default_skills_root() -> Path:
    env = os.environ.get("MINIMAX_CODE_SKILLS_DIR")
    if env:
        return Path(env)
    # agent/minimax_code/app.py  ->  agent/skills/
    # parents[0] = agent/minimax_code/  parents[1] = agent/  parents[2] = repo root
    return Path(__file__).resolve().parents[2] / "agent" / "skills"


def _custom_skills_root() -> Path:
    configured = os.environ.get("MINIMAX_CODE_CUSTOM_SKILLS_DIR")
    if configured:
        return Path(configured).expanduser()
    from .storage.db import default_data_dir

    return default_data_dir() / "skills"


def _default_plugins_root() -> Path:
    """Resolve the plugin discovery root.

    Honors ``MINIMAX_CODE_PLUGINS_DIR``; otherwise defaults to
    ``<repo>/agent/plugins`` (sibling of the skills root).
    """
    env = os.environ.get("MINIMAX_CODE_PLUGINS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "agent" / "plugins"


def discover_plugins() -> list[Any]:
    """Discover plugins under the configured root (fail-open).

    Returns an empty list on any error so a broken root never blocks
    agent startup. Each entry is a :class:`~minimax_code.plugins.Plugin`;
    entries whose manifest failed to parse carry an ``error`` field.
    """
    from .plugins import PluginLoader

    try:
        return PluginLoader().discover(_default_plugins_root())
    except Exception:
        logger.exception("plugin discovery failed; running with no plugins")
        return []


# ---------------------------------------------------------------------------
# Progress tracker singleton
# ---------------------------------------------------------------------------


_PROGRESS_TRACKER: Any = None  # type: ignore[no-untyped-def]


def get_db() -> Any:
    """Return the process-wide :class:`AsyncDatabase`, or ``None``."""
    return _DB_SINGLETON


async def ensure_db() -> Any:
    """Return the process-wide DB, opening it once when needed."""
    existing = get_db()
    if existing is not None:
        return existing
    async with _DB_LOCK:
        existing = get_db()
        if existing is not None:
            return existing
        return await _maybe_open_db()


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
        from . import secrets
        from .agent.llm import MiniMaxClient
        from .storage.dao.model_prefs import ModelPrefsDAO
        from .storage.dao.providers import ProviderDAO

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
    from .ipc.builtins import handle_agent_cancel, handle_agent_send_message
    from .ipc.handlers_agents import register_agent_handlers
    from .ipc.handlers_audit import register_audit_handlers
    from .ipc.handlers_git import register_git_handlers
    from .ipc.handlers_model import register_model_handlers
    from .ipc.handlers_patch import register_patch_handlers
    from .ipc.handlers_permissions import register_permission_handlers
    from .ipc.handlers_plugins import register_plugin_handlers
    from .ipc.handlers_providers import register_provider_handlers
    from .ipc.handlers_runner import register_runner_handlers
    from .ipc.handlers_runs import register_run_handlers
    from .ipc.handlers_runtime import register_runtime_handlers
    from .ipc.handlers_scheduled import register_scheduled_handlers
    from .ipc.handlers_secrets import register_secret_handlers
    from .ipc.handlers_sessions import register_session_handlers
    from .ipc.handlers_skills import register_skill_handlers
    from .ipc.handlers_tasks import register_task_handlers
    from .ipc.handlers_telemetry import register_telemetry_handlers
    from .ipc.handlers_terminal import register_terminal_handlers
    from .ipc.handlers_webhooks import register_webhook_handlers
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
    # Runtime diagnostics — exposes the boot-time crash-recovery
    # snapshot (R12) so the Settings page can surface "recovered N
    # orphan runs after an unexpected shutdown". Read-only handler.
    register_runtime_handlers(server)
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
    # Lightweight command sessions for the inspector terminal panel.
    register_terminal_handlers(server)
    # Product-facing runner adapters. Native runner delegates to terminal
    # sessions; external CLI adapters are detected before executable wiring.
    register_runner_handlers(server)
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
    # The plugin handlers expose ``plugins.list`` / ``plugins.info`` /
    # ``plugins.enable`` / ``plugins.disable`` / ``plugins.reload`` for the
    # Settings page's Plugins tab (platform pillar #3). The registry is
    # built lazily via :func:`ensure_plugin_registry` on first call (same
    # defensive posture as the DB singleton). Tests can inject a registry
    # via the ``registry=`` kwarg to skip discovery.
    register_plugin_handlers(server)
    # The telemetry handlers expose ``telemetry.recent`` / ``telemetry.metrics``
    # / ``telemetry.clear`` for the in-memory observability bus (R11). The
    # engine is built lazily via :func:`ensure_telemetry_engine` and is
    # None-safe (returns ``enabled: False`` when telemetry is off).
    register_telemetry_handlers(server)
    logger.info(
        "registered application handlers "
        "(1 agent.* + 7 agent.* + 5 skill.* + 6 task.* + 5 session.* + 3 workspace.* + "
        "5 permission.* + 6 schedule.* + 7 mobile.* + 3 model.* + "
        "7 provider.* + 3 secrets.* + 3 git.* + 3 patch.* + "
        "4 terminal.* + 2 runner.* + 3 audit.* + 5 webhook.* + "
        "5 notification.* + 7 workflow.* + 7 team.* + 5 plugins.* + 3 telemetry.*)"
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


# ---------------------------------------------------------------------------
# Plugin registry singleton (v0.8.x platform pillar #3 — Plugins)
# ---------------------------------------------------------------------------


def get_plugin_registry() -> Any:
    """Return the process-wide :class:`PluginRegistry`, or ``None``."""
    return _PLUGIN_REGISTRY


def set_plugin_registry(registry: Any) -> None:
    """Inject a pre-built registry (tests bypass disk discovery)."""
    global _PLUGIN_REGISTRY
    _PLUGIN_REGISTRY = registry


def ensure_plugin_registry() -> Any:
    """Return the process-wide plugin registry, building it once on demand.

    Fail-safe: if discovery raises, an empty registry is returned so the
    agent always starts (same defensive posture as :func:`_maybe_open_db`).
    """
    global _PLUGIN_REGISTRY
    if _PLUGIN_REGISTRY is not None:
        return _PLUGIN_REGISTRY
    from .plugins import PluginRegistry

    registry = PluginRegistry()
    try:
        registry.add_many(discover_plugins())
    except Exception:
        logger.exception("plugin registry init failed; running with empty registry")
        registry = PluginRegistry()
    _PLUGIN_REGISTRY = registry
    return registry


# ---------------------------------------------------------------------------
# Hook manager singleton (v0.8.x platform pillar #2 — Hooks, wired in R10)
# ---------------------------------------------------------------------------


def get_hook_manager() -> Any:
    """Return the process-wide :class:`HookManager`, or ``None``."""
    return _HOOK_MANAGER


def set_hook_manager(manager: Any) -> None:
    """Inject a pre-built HookManager (tests bypass plugin apply)."""
    global _HOOK_MANAGER
    _HOOK_MANAGER = manager


def ensure_hook_manager() -> Any:
    """Return the process-wide HookManager, building it once on demand.

    This is the **integration seam** (R10) that turns the three platform
    pillars into one running system: it builds a :class:`HookManager`,
    then pours every *enabled* plugin's hooks into the manager's
    registry via ``PluginRegistry.apply_hooks``. From that point on,
    plugin-contributed ``pre_tool_use`` / ``session_start`` / ... hooks
    are agent-active — the main agent (``agent.send_message``) and any
    other ``AgentCore`` that reuses this singleton get them for free.

    Fail-open on every layer: discovery failure → empty registry →
    manager with no hooks. The agent always starts.
    """
    global _HOOK_MANAGER
    if _HOOK_MANAGER is not None:
        return _HOOK_MANAGER
    from .hooks import HookManager

    manager = HookManager()
    try:
        registry = ensure_plugin_registry()
        contributed = registry.apply_hooks(manager.registry)
        if contributed:
            logger.info(
                "hook manager initialised; plugins contributed %d hook(s)",
                contributed,
            )
        else:
            logger.info("hook manager initialised; no plugin hooks contributed")
    except Exception:
        logger.exception("hook manager init failed; running with no hooks")
    _HOOK_MANAGER = manager
    return manager


# ---------------------------------------------------------------------------
# Telemetry engine singleton (R11 — observability pillar)
# ---------------------------------------------------------------------------


def get_telemetry_engine() -> Any:
    """Return the process-wide :class:`TelemetryEngine`, or ``None``."""
    return _TELEMETRY_ENGINE


def set_telemetry_engine(engine: Any) -> None:
    """Inject a pre-built TelemetryEngine (tests bypass lazy build)."""
    global _TELEMETRY_ENGINE
    _TELEMETRY_ENGINE = engine


def ensure_telemetry_engine() -> Any:
    """Return the process-wide TelemetryEngine, building it once on demand.

    Fail-open: a build failure logs and returns ``None`` rather than
    raising, so ``engine = ensure_telemetry_engine()`` followed by
    ``if engine: engine.emit(...)`` keeps working even when telemetry
    cannot initialise. In-memory, no external deps — rarely hit, but the
    contract is "telemetry never breaks the agent".
    """
    global _TELEMETRY_ENGINE
    if _TELEMETRY_ENGINE is not None:
        return _TELEMETRY_ENGINE
    # Env switch (default on). "0"/"false"/"off"/"no" disables telemetry
    # entirely so the handlers report ``enabled: False`` without ever
    # building an engine — useful for sandboxes that forbid observability.
    flag = os.environ.get("MINIMAX_CODE_TELEMETRY", "").strip().lower()
    if flag in ("0", "false", "off", "no"):
        logger.debug("telemetry disabled by MINIMAX_CODE_TELEMETRY env")
        return None
    try:
        from .telemetry import TelemetryEngine

        _TELEMETRY_ENGINE = TelemetryEngine()
        logger.info("telemetry engine initialised")
    except Exception:
        logger.exception("telemetry engine init failed; running without telemetry")
        _TELEMETRY_ENGINE = None
    return _TELEMETRY_ENGINE


# ---------------------------------------------------------------------------
# Filesystem change bus singleton (R16 — causality pillar)
# ---------------------------------------------------------------------------

_FS_BUS: Any = None


def get_fs_bus() -> Any:
    """Return the process-wide :class:`FsEventBus`, or ``None``."""
    return _FS_BUS


def set_fs_bus(bus: Any) -> None:
    """Inject a pre-built FsEventBus (tests bypass lazy build)."""
    global _FS_BUS
    _FS_BUS = bus


def ensure_fs_bus() -> Any:
    """Return the process-wide FsEventBus, building it once on demand.

    Fail-open: a build failure logs and returns ``None`` rather than
    raising, so ``bus = ensure_fs_bus()`` followed by
    ``if bus: bus.emit(...)`` keeps working even when the bus cannot
    initialise. The write tools call this after a successful write and
    treat ``None`` as "disabled, zero overhead" — the contract is "file
    writes never break because the change bus broke".
    """
    global _FS_BUS
    if _FS_BUS is not None:
        return _FS_BUS
    # Env switch (default on). "0"/"false"/"off"/"no" disables the bus so
    # the write tools short-circuit without ever building one — useful for
    # sandboxes that forbid in-memory event collection.
    flag = os.environ.get("MINIMAX_CODE_FS_BUS", "").strip().lower()
    if flag in ("0", "false", "off", "no"):
        logger.debug("fs bus disabled by MINIMAX_CODE_FS_BUS env")
        return None
    try:
        from .fsnotify import FsEventBus

        _FS_BUS = FsEventBus()
        logger.info("fs event bus initialised")
    except Exception:
        logger.exception("fs bus init failed; running without fs events")
        _FS_BUS = None
    return _FS_BUS


# ---------------------------------------------------------------------------
# Circuit-breaker registry (R17 — resilience pillar)
# ---------------------------------------------------------------------------

_BREAKER_REGISTRY: Any = None


def get_breaker_registry() -> Any:
    """Return the process-wide :class:`CircuitBreakerRegistry`, or ``None``."""
    return _BREAKER_REGISTRY


def set_breaker_registry(registry: Any) -> None:
    """Inject a pre-built CircuitBreakerRegistry (tests bypass lazy build)."""
    global _BREAKER_REGISTRY
    _BREAKER_REGISTRY = registry


def _wire_breaker_telemetry(registry: Any) -> None:
    """Bridge every transport-layer breaker into the telemetry engine (R21).

    Installs a registry-wide observer factory so each lazily-created breaker
    (keyed by endpoint, e.g. ``"llm:anthropic"``) carries a
    :class:`ReliabilityTelemetryObserver` resolved against the live engine at
    emit time. The engine is read through :func:`ensure_telemetry_engine`
    (lazy, fail-open) — the **same adapter** the reliability stack uses, so
    both breaker fleets feed one event stream and stay distinguishable by
    ``name`` (reliability breaker = ``"llm"``, transport breaker = endpoint).

    Fail-open: a missing adapter or a registry fault is logged and swallowed;
    the registry still works without telemetry.
    """
    try:
        from .telemetry.observer_adapter import ReliabilityTelemetryObserver

        def factory(key: str) -> Any:
            # Lazy engine getter: reads the live engine at emit time so the
            # observer tolerates the engine being injected after the breaker.
            return ReliabilityTelemetryObserver(ensure_telemetry_engine, name=key)

        registry.attach_observer_factory(factory)
    except Exception:
        logger.exception("breaker telemetry wiring failed; running uninstrumented")


def ensure_breaker_registry() -> Any:
    """Return the process-wide CircuitBreakerRegistry, building it once on demand.

    Fail-open: a build failure logs and returns ``None`` rather than raising,
    so ``reg = ensure_breaker_registry()`` followed by
    ``br = reg.get(key) if reg else None`` keeps callers working even when
    the registry cannot initialise. Callers treat ``None`` (or a disabled
    registry returning ``None`` from ``get``) as "no protection, proceed
    unprotected" — the contract is "business calls never break because the
    breaker registry broke".
    """
    global _BREAKER_REGISTRY
    if _BREAKER_REGISTRY is not None:
        return _BREAKER_REGISTRY
    # Env switch (default on). "0"/"false"/"off"/"no" disables the registry
    # so callers short-circuit without ever building one — useful for
    # environments that want zero resilience overhead.
    flag = os.environ.get("MINIMAX_CODE_BREAKER", "").strip().lower()
    if flag in ("0", "false", "off", "no"):
        logger.debug("breaker registry disabled by MINIMAX_CODE_BREAKER env")
        return None
    try:
        from .resilience import BreakerConfig, CircuitBreakerRegistry

        _BREAKER_REGISTRY = CircuitBreakerRegistry(BreakerConfig.from_env())
        _wire_breaker_telemetry(_BREAKER_REGISTRY)
        logger.info("circuit-breaker registry initialised")
    except Exception:
        logger.exception("breaker registry init failed; running unprotected")
        _BREAKER_REGISTRY = None
    return _BREAKER_REGISTRY


# ---------------------------------------------------------------------------
# Boot-time crash recovery (R12 — reliability pillar)
# ---------------------------------------------------------------------------


def get_recovery_result() -> Any:
    """Return the snapshot produced by the last boot's crash recovery.

    ``None`` until :func:`_run_crash_recovery` has executed, or when the
    boot path skipped the DB (``MINIMAX_CODE_NO_DB=1``). The
    ``runtime.*`` IPC handlers call this rather than re-running recovery,
    so recovery is strictly one-shot per boot.
    """
    return _RECOVERY_RESULT


def _set_recovery_result(result: Any) -> None:
    """Inject the recovery snapshot (internal setter, also used by tests)."""
    global _RECOVERY_RESULT
    _RECOVERY_RESULT = result


async def _run_crash_recovery(db: Any) -> None:
    """Boot-time crash recovery (R12, fail-open).

    Orchestrates two independent mechanisms fused from grok-build:

    1. :func:`check_previous_crash` — marker-file protocol. A leftover
       ``.minimax_code_running`` marker means the previous PID never
       reached ``atexit`` → it crashed (OOM / segfault / ``kill -9``).
    2. :func:`AgentRunsDAO.recover_orphans` — flips every in-flight run
       to ``failed`` and appends a recovery ``status`` step (append-only
       audit, grok ``RewindMarker`` semantics).

    Each can fail independently without blocking the other, and the
    whole orchestration is fail-open: a recovery fault never blocks
    boot. The snapshot lands in :data:`_RECOVERY_RESULT` for the
    ``runtime.recovery_status`` IPC handler, and a WARN telemetry event
    (R11) is emitted when recovery actually did something.
    """
    from pathlib import Path

    try:
        from .runtime.crash_detect import check_previous_crash, crash_report_to_dict
        from .storage.dao.runs import AgentRunsDAO
        from .storage.db import default_database_path
    except Exception:
        logger.exception("crash recovery imports failed; skipping (fail-open)")
        _set_recovery_result({"available": False, "reason": "imports failed"})
        return

    # (1) Marker-file protocol — did the previous PID crash?
    report = None
    crashed = False
    try:
        data_dir = Path(default_database_path()).parent
        report = check_previous_crash(data_dir)
        crashed = crash_report_to_dict(report) is not None
    except Exception:
        logger.exception("previous-crash detection failed (fail-open)")
        report = None
        crashed = False

    # (2) Orphan-run recovery — flip every in-flight run to failed and
    #     append an audit step. Idempotent on a clean DB.
    try:
        dao = AgentRunsDAO(db)
        recovery = await dao.recover_orphans()
    except Exception:
        logger.exception("orphan-run recovery failed (fail-open)")
        recovery = {"recovered": 0, "run_ids": [], "sessions": []}

    snapshot = {
        "available": True,
        "clean_start": not crashed and int(recovery.get("recovered", 0)) == 0,
        "previous_crash": crashed,
        "crash": crash_report_to_dict(report),
        "recovered_runs": int(recovery.get("recovered", 0)),
        "run_ids": recovery.get("run_ids", []),
        "sessions": recovery.get("sessions", []),
    }
    _set_recovery_result(snapshot)

    # Emit a WARN telemetry event when recovery did something — the
    # observability pillar (R11) records the reliability event so
    # dashboards can alert on repeated crashes. Fail-open + non-fatal.
    if crashed or snapshot["recovered_runs"] > 0:
        try:
            from .telemetry.events import EventType, Severity, TelemetryEvent

            engine = ensure_telemetry_engine()
            if engine is not None:
                engine.emit(
                    TelemetryEvent(
                        type=EventType.ERROR,
                        severity=Severity.WARN,
                        name="crash_recovery",
                        payload={
                            "previous_crash": crashed,
                            "recovered_runs": snapshot["recovered_runs"],
                        },
                    )
                )
        except Exception:
            logger.debug("telemetry emit for recovery failed (non-fatal)", exc_info=True)

    logger.info(
        "crash recovery complete: previous_crash=%s recovered_runs=%d",
        crashed,
        snapshot["recovered_runs"],
    )


__all__ = [
    "ensure_repo_map_indexer",
    "get_http_app",
    "get_progress_tracker",
    "get_provider_dao",
    "get_recovery_result",
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
    "discover_plugins",
    "ensure_hook_manager",
    "ensure_plugin_registry",
    "get_hook_manager",
    "get_plugin_registry",
    "set_hook_manager",
    "set_plugin_registry",
    "ensure_telemetry_engine",
    "get_telemetry_engine",
    "set_telemetry_engine",
    "ensure_fs_bus",
    "get_fs_bus",
    "set_fs_bus",
    "ensure_breaker_registry",
    "get_breaker_registry",
    "set_breaker_registry",
]
