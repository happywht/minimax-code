"""Tool-call permission store.

A thin wrapper over :class:`~minimax_code.storage.dao.permissions.PermissionRuleDAO`
that keeps an in-process cache of every rule and exposes a simple
``is_allowed(tool_name)`` / ``is_denied(tool_name)`` API the agent
loop can call on every tool invocation without hitting SQLite.

Persistence
-----------
On startup the store calls :meth:`load_from_db` once and rebuilds the
cache. Every write (``upsert`` / ``delete``) flows through to the DAO
*and* updates the cache atomically (under an ``asyncio.Lock``). The
cache is therefore the source of truth *between* writes; a restart
re-hydrates from disk.

Matching
--------
The :meth:`is_allowed` / :meth:`is_denied` predicates take a concrete
``tool_name`` and scan the cached rules top-down (insertion order).
Each rule's ``tool_pattern`` is interpreted as:

* ``"*"``  — matches every tool
* ``"exec_*"`` — fnmatch-style glob (case-sensitive)
* otherwise exact equality

The first rule that matches wins. If no rule matches, the store
returns ``True`` for ``is_allowed`` (i.e. the agent's defaults are
"ask the user, but allow by default at the store layer"). Use
:meth:`lookup` if you need the full rule object instead of a bool.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
from collections.abc import Iterable
from typing import Any

from ..storage.dao.permissions import PermissionRuleDAO

logger = logging.getLogger(__name__)


_VALID_ACTIONS: frozenset[str] = frozenset({"allow", "deny", "ask"})


class PermissionStore:
    """In-process cache over :class:`PermissionRuleDAO`."""

    def __init__(self, dao: PermissionRuleDAO) -> None:
        self._dao = dao
        self._lock = asyncio.Lock()
        # tool_pattern -> rule dict. Insertion-ordered (Python 3.7+).
        self._rules: dict[str, dict[str, Any]] = {}

    # -- lifecycle ---------------------------------------------------------

    async def load_from_db(self) -> int:
        """Replace the in-memory cache with whatever the DAO returns.

        Returns the number of cached rules. Safe to call repeatedly.
        """
        async with self._lock:
            rules = await self._dao.list_all()
            self._rules = {r["tool_pattern"]: r for r in rules if r.get("tool_pattern")}
            logger.info("permission store loaded %d rule(s) from db", len(self._rules))
            return len(self._rules)

    async def warm(self) -> int:
        """Idempotent alias for :meth:`load_from_db`."""
        return await self.load_from_db()

    # -- read API ----------------------------------------------------------

    def list_rules(self) -> list[dict[str, Any]]:
        """Return a snapshot of the cached rules in insertion order."""
        return list(self._rules.values())

    def get(self, tool_pattern: str) -> dict[str, Any] | None:
        """Return the cached rule for ``tool_pattern`` (or ``None``)."""
        return self._rules.get(tool_pattern)

    def lookup(self, tool_name: str) -> dict[str, Any] | None:
        """Find the first rule that matches ``tool_name``.

        Returns the rule dict, or ``None`` if no rule matches. Glob
        matching is fnmatch-style (e.g. ``exec_*``).
        """
        if not tool_name:
            return None
        for rule in self._rules.values():
            pattern = rule.get("tool_pattern") or ""
            if _matches(pattern, tool_name):
                return rule
        return None

    def is_allowed(self, tool_name: str, *, scope: str = "global") -> bool:
        """Decide whether ``tool_name`` is currently allowed.

        ``scope`` is currently a hint — the cache only stores
        ``global`` rules; the per-session / per-user scopes are a
        future addition. Returns ``True`` when no rule matches (the
        agent's default is "ask, but the store doesn't block").
        """
        if not tool_name:
            return True
        rule = self.lookup(tool_name)
        if rule is None:
            return True
        if rule.get("scope") not in (None, "", scope):
            # Different scope — defer to whatever policy owns it.
            return True
        action = rule.get("action")
        if action == "allow":
            return True
        if action == "deny":
            return False
        # "ask" — the store doesn't decide; the caller must prompt.
        return True

    def is_denied(self, tool_name: str, *, scope: str = "global") -> bool:
        """Inverse helper — ``True`` only when a rule explicitly denies."""
        if not tool_name:
            return False
        rule = self.lookup(tool_name)
        if rule is None:
            return False
        if rule.get("scope") not in (None, "", scope):
            return False
        return rule.get("action") == "deny"

    # -- write API ---------------------------------------------------------

    async def upsert(
        self,
        *,
        tool_pattern: str,
        action: str,
        scope: str = "global",
    ) -> dict[str, Any]:
        """Insert or update a rule; refresh the cache.

        Returns the persisted rule dict (as written by the DAO).
        """
        if not tool_pattern or not isinstance(tool_pattern, str):
            raise ValueError("tool_pattern must be a non-empty string")
        if action not in _VALID_ACTIONS:
            raise ValueError(
                f"action must be one of {sorted(_VALID_ACTIONS)}, got {action!r}"
            )
        if not scope or not isinstance(scope, str):
            raise ValueError("scope must be a non-empty string")
        async with self._lock:
            rule = await self._dao.upsert(
                {
                    "tool_pattern": tool_pattern,
                    "action": action,
                    "scope": scope,
                }
            )
            if rule and rule.get("tool_pattern"):
                self._rules[rule["tool_pattern"]] = rule
            return rule

    async def delete(self, tool_pattern: str) -> int:
        """Delete a rule from disk and the cache.

        Returns the number of rows actually deleted (0 if the rule
        didn't exist).
        """
        async with self._lock:
            n = await self._dao.delete(tool_pattern)
            self._rules.pop(tool_pattern, None)
            return n

    # -- debugging ---------------------------------------------------------

    def __len__(self) -> int:
        return len(self._rules)

    def __contains__(self, tool_pattern: str) -> bool:
        return tool_pattern in self._rules

    def __iter__(self) -> Iterable[str]:
        return iter(self._rules)


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------


def _matches(pattern: str, tool_name: str) -> bool:
    """Glob-style match (fnmatch) — exact when no wildcard is present."""
    if pattern == tool_name:
        return True
    if not any(c in pattern for c in "*?["):
        return False
    return fnmatch.fnmatchcase(tool_name, pattern)


__all__ = ["PermissionStore"]
