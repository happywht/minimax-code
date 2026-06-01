"""Skill registry — in-memory catalogue + optional persistence.

Responsibilities
----------------

* Hold a single :class:`Skill` per ``skill_id`` (composite key
  ``<dir>:<name>``) with an in-memory *enabled* flag.
* Reconcile with the persistent ``skills`` table at startup so the
  user's enable/disable choices survive a restart.
* Provide the operations the IPC layer needs: ``list``,
  ``enable`` / ``disable`` / ``get`` / ``has`` / ``register`` /
  ``unregister`` / ``set_tools_available``.
* Expose a thread-/asyncio-safe mutator surface — handlers run
  concurrently on the IPC server, so we guard mutation with an
  :class:`asyncio.Lock`.

Persistence model
-----------------

The registry is the *source of truth for runtime state*; the
``skills`` table mirrors the manifest (name, version, path,
description, when_to_use, enabled) so the UI can list skills
without re-reading the filesystem. The table is *not* a content
store — the body of a skill is always re-read from disk on
demand, so edits to a ``SKILL.md`` are picked up at the next
:func:`reload` (or the next agent start).

DB integration is optional: passing ``db=None`` puts the
registry in *memory-only* mode, which is what the test suite
relies on. The production wiring (``app.py``) instantiates
the registry with an :class:`AsyncDatabase`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .loader import Skill, load_all, validate_tools

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SkillNotFoundError(KeyError):
    """Raised when a skill_id is unknown. ``str(exc)`` is the missing id."""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class SkillRegistry:
    """Async-safe in-memory catalogue of skills.

    Parameters
    ----------
    skills_root:
        Directory the loader should scan. Required; pass
        ``Path("agent/skills")`` (relative to the repo root)
        in production.
    db:
        Optional :class:`~minimax_code.storage.db.AsyncDatabase`.
        When supplied, ``load_all`` and the ``enable`` / ``disable``
        methods persist to the ``skills`` table; when ``None``,
        the registry is in-memory only and ``enable`` /
        ``disable`` simply mutate the in-memory flag.
    extra_roots:
        Additional directories to scan (in addition to
        ``skills_root``). Useful for user-supplied skill packs
        that live outside the project tree. Duplicates (same
        skill name) are resolved in declaration order — first
        wins, the rest are logged and skipped.
    auto_persist:
        If ``True`` (default), every ``register``/``unregister``
        call mirrors to the DB. Set ``False`` for tests that
        don't want I/O.
    """

    def __init__(
        self,
        *,
        skills_root: Path | str,
        db: Any | None = None,
        extra_roots: Iterable[Path | str] = (),
        auto_persist: bool = True,
    ) -> None:
        self.skills_root = Path(skills_root)
        self._extra_roots: list[Path] = [Path(p) for p in extra_roots]
        self._db = db
        self._auto_persist = auto_persist
        self._skills: dict[str, Skill] = {}
        self._lock = asyncio.Lock()
        # Cached list of available tool names, refreshed whenever
        # the global tool registry is rebuilt.
        self._available_tools: set[str] = set()
        # Tools the registry has *added* to the global tool
        # registry on behalf of skills. Used to unregister cleanly
        # on :meth:`reload`.
        self._registered_skill_tools: dict[str, set[str]] = {}

    # -- lifecycle ----------------------------------------------------------

    async def load_all(self) -> list[Skill]:
        """Scan ``skills_root`` (+ extras) and load every skill.

        Reconciliation rule for the ``enabled`` flag:

        * If the skill already exists in the DB, keep the DB value.
        * If the skill is brand-new, default to ``enabled=True``.

        Returns the list of loaded skills (also stored on
        ``self._skills``).
        """
        all_skills: list[Skill] = []
        roots = [self.skills_root, *self._extra_roots]
        for root in roots:
            all_skills.extend(load_all(root))

        # Deduplicate by name — first occurrence wins.
        dedup: dict[str, Skill] = {}
        for s in all_skills:
            if s.name in dedup:
                logger.warning(
                    "duplicate skill %r — keeping first (%s), ignoring %s",
                    s.name,
                    dedup[s.name].path,
                    s.path,
                )
                continue
            dedup[s.name] = s

        async with self._lock:
            for skill in dedup.values():
                persisted = await self._maybe_lookup_persisted(skill.name)
                if persisted is not None:
                    skill = skill.with_enabled(bool(persisted.get("enabled", True)))
                self._skills[skill.skill_id] = skill
                await self._maybe_upsert(skill)
            loaded = list(self._skills.values())
        logger.info("loaded %d skill(s) from %s", len(loaded), self.skills_root)
        return loaded

    async def reload(self) -> list[Skill]:
        """Drop everything and re-scan. Picks up new / edited SKILL.md files."""
        async with self._lock:
            self._skills.clear()
        return await self.load_all()

    # -- introspection ------------------------------------------------------

    def has(self, skill_id: str) -> bool:
        return skill_id in self._skills

    def get(self, skill_id: str) -> Skill:
        try:
            return self._skills[skill_id]
        except KeyError as exc:
            raise SkillNotFoundError(skill_id) from exc

    def get_by_name(self, name: str) -> Skill | None:
        for s in self._skills.values():
            if s.name == name:
                return s
        return None

    def list(
        self,
        *,
        enabled: bool | None = None,
        search: str | None = None,
    ) -> list[Skill]:
        """Return skills, optionally filtered.

        ``search`` matches case-insensitively against ``name`` and
        ``description`` (substring).
        """
        out = list(self._skills.values())
        if enabled is not None:
            out = [s for s in out if s.enabled == enabled]
        if search:
            needle = search.lower()
            out = [
                s
                for s in out
                if needle in s.name.lower() or needle in s.description.lower()
            ]
        out.sort(key=lambda s: s.name.lower())
        return out

    def manifest(self) -> list[dict[str, Any]]:
        """JSON-friendly listing for the IPC layer."""
        return [s.manifest() for s in self.list()]

    # -- mutation -----------------------------------------------------------

    async def register(self, skill: Skill, *, enabled: bool | None = None) -> Skill:
        """Insert or replace a skill. Optionally override the ``enabled`` flag."""
        if enabled is not None:
            skill = skill.with_enabled(bool(enabled))
        async with self._lock:
            self._skills[skill.skill_id] = skill
            await self._maybe_upsert(skill)
        return skill

    async def unregister(self, skill_id: str) -> Skill | None:
        async with self._lock:
            removed = self._skills.pop(skill_id, None)
            if removed is not None and self._db is not None:
                try:
                    await self._db.fetchone(
                        "SELECT id FROM skills WHERE name = ?", (removed.name,)
                    )
                    # Mirror the storage-layer DAO behaviour: delete by name.
                    async with self._db.transaction() as conn:
                        await conn.execute(
                            "DELETE FROM skills WHERE name = ?", (removed.name,)
                        )
                except Exception:  # pragma: no cover — defensive
                    logger.exception("failed to remove skill %s from DB", skill_id)
        return removed

    async def enable(self, skill_id: str) -> Skill:
        return await self._set_enabled(skill_id, True)

    async def disable(self, skill_id: str) -> Skill:
        return await self._set_enabled(skill_id, False)

    async def _set_enabled(self, skill_id: str, enabled: bool) -> Skill:
        async with self._lock:
            skill = self.get(skill_id)
            new_skill = skill.with_enabled(enabled)
            self._skills[skill_id] = new_skill
            await self._maybe_upsert(new_skill)
        return new_skill

    # -- tool-registry bridging --------------------------------------------

    def set_tools_available(self, tool_names: Iterable[str]) -> None:
        """Cache the set of tool names the global registry currently has.

        The registry uses this to compute :meth:`Skill.missing_tools`
        without needing a circular import on the tools package.
        """
        self._available_tools = set(tool_names)

    def tools_added_by_skill(self, skill_id: str) -> set[str]:
        return set(self._registered_skill_tools.get(skill_id, ()))

    def register_skill_tools(self, skill: Skill, tool_names: set[str]) -> None:
        """Track which tools the runtime has added for ``skill``."""
        self._registered_skill_tools[skill.skill_id] = set(tool_names)

    def unregister_skill_tools(self, skill_id: str) -> set[str]:
        return self._registered_skill_tools.pop(skill_id, set())

    def validate(self) -> dict[str, list[str]]:
        """Return ``skill_id -> missing_tool_names`` for all skills."""
        return validate_tools(self._skills.values(), self._available_tools)

    # -- persistence helpers (private) --------------------------------------

    async def _maybe_upsert(self, skill: Skill) -> None:
        if not self._auto_persist or self._db is None:
            return
        try:
            from ...storage.dao.skills import SkillsDAO  # local import to avoid cycles

            dao = SkillsDAO(self._db)
            await dao.upsert(
                id=skill.skill_id,
                name=skill.name,
                path=str(skill.path),
                version=skill.version,
                description=skill.description,
                when_to_use=skill.when_to_use,
                enabled=skill.enabled,
            )
        except Exception:  # pragma: no cover — defensive
            logger.exception("failed to persist skill %s", skill.skill_id)

    async def _maybe_lookup_persisted(self, name: str) -> dict[str, Any] | None:
        if self._db is None:
            return None
        try:
            from ...storage.dao.skills import SkillsDAO

            dao = SkillsDAO(self._db)
            return await dao.get_by_name(name)
        except Exception:  # pragma: no cover — defensive
            logger.exception("failed to read skill %s from DB", name)
            return None


# ---------------------------------------------------------------------------
# Module-level singleton (matches the pattern used by the tool registry)
# ---------------------------------------------------------------------------


_DEFAULT: SkillRegistry | None = None
_DEFAULT_LOCK = asyncio.Lock()


async def get_default_registry() -> SkillRegistry:
    """Return the process-wide skill registry, building it on first call.

    The registry is constructed with ``db=None`` (in-memory only) so
    callers in test or CLI contexts can use it without spinning up
    SQLite. The application bootstrap (``app.py``) replaces the
    default with one wired to the real database via
    :func:`set_default_registry`.
    """
    global _DEFAULT
    async with _DEFAULT_LOCK:
        if _DEFAULT is None:
            reg = SkillRegistry(
                skills_root=_default_skills_root(),
                db=None,
                auto_persist=False,
            )
            await reg.load_all()
            _DEFAULT = reg
        return _DEFAULT


def set_default_registry(registry: SkillRegistry | None) -> None:
    """Replace the default registry (used by the application bootstrap)."""
    global _DEFAULT
    _DEFAULT = registry


def _default_skills_root() -> Path:
    """Resolve the built-in skills directory.

    Falls back to ``<package>/skills/../../skills`` (the
    ``agent/skills/`` sibling of the ``minimax_code/`` source
    tree) when the environment variable
    ``MINIMAX_CODE_SKILLS_DIR`` is unset.
    """
    import os

    env = os.environ.get("MINIMAX_CODE_SKILLS_DIR")
    if env:
        return Path(env)
    # agent/minimax_code/agent/skills/_builtin.py  ->  agent/skills/
    return Path(__file__).resolve().parents[3] / "skills"


__all__ = [
    "SkillNotFoundError",
    "SkillRegistry",
    "get_default_registry",
    "set_default_registry",
]
