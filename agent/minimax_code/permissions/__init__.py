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

The first rule that matches wins. If no user rule matches, the
factory defaults below apply. Use :meth:`lookup` if you need the
full rule object instead of a bool.

Factory defaults (R18)
----------------------
High-risk tools ship with an ``ask`` default so a fresh install
gates shell execution behind the consent modal instead of running
it silently. The defaults live in code (``DEFAULT_RULES``), *not* in
the database:

* nothing is written to the user's ``permission_rules`` table;
* a user rule for the same pattern always wins (DB rules are
  scanned first);
* deleting the user rule falls back to the factory default again —
  "delete" means "back to factory", never "allow silently".

Tools without a matching rule (user or default) remain default-allow
at the store layer — ``is_allowed`` returns ``True`` and the caller
decides whether to prompt. The agent loop
(:func:`minimax_code.agent.core`) turns ``ask`` into a real
``permission.request`` prompt via
:class:`~minimax_code.perm_consent.PermissionGater`.
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

# Factory-default rules (R18) — see the module docstring. Evaluated
# only when no user rule matches, so an explicit user decision
# (allow / deny / a custom ask) always wins. Rules carry the same
# shape as DAO rows; ``origin: "default"`` lets the UI badge them
# and ``created_at: ""`` marks them as non-persisted.
DEFAULT_RULES: tuple[dict[str, Any], ...] = (
    {
        "id": "pr_default_exec",
        "tool_pattern": "exec_*",
        "action": "ask",
        "scope": "global",
        "created_at": "",
        "origin": "default",
    },
)


def _default_matching(tool_name: str) -> dict[str, Any] | None:
    """Find the first factory default whose pattern matches ``tool_name``."""
    for rule in DEFAULT_RULES:
        if _matches(rule.get("tool_pattern") or "", tool_name):
            return dict(rule)  # copy — callers must not mutate the constant
    return None


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
        """Return user rules plus uncovered factory defaults.

        The cached (user) rules come first in insertion order; any
        factory default whose ``tool_pattern`` is not present in the
        cache is appended after them. A default whose pattern *is*
        covered by a user rule is omitted — the user rule shadows it.
        """
        rules = list(self._rules.values())
        covered = set(self._rules.keys())
        for default in DEFAULT_RULES:
            if default.get("tool_pattern") not in covered:
                rules.append(dict(default))
        return rules

    def get(self, tool_pattern: str) -> dict[str, Any] | None:
        """Return the cached rule for ``tool_pattern`` (or the factory default)."""
        rule = self._rules.get(tool_pattern)
        if rule is not None:
            return rule
        for default in DEFAULT_RULES:
            if default.get("tool_pattern") == tool_pattern:
                return dict(default)
        return None

    def lookup(self, tool_name: str) -> dict[str, Any] | None:
        """Find the first rule that matches ``tool_name``.

        User rules are scanned first (insertion order); when none
        matches, the factory defaults (:data:`DEFAULT_RULES`) get a
        chance. Returns the rule dict, or ``None`` if neither layer
        matches. Glob matching is fnmatch-style (e.g. ``exec_*``).
        """
        if not tool_name:
            return None
        for rule in self._rules.values():
            pattern = rule.get("tool_pattern") or ""
            if _matches(pattern, tool_name):
                return rule
        return _default_matching(tool_name)

    def is_allowed(self, tool_name: str, *, scope: str = "global") -> bool:
        """Decide whether ``tool_name`` is currently allowed.

        ``scope`` is currently a hint — the cache only stores
        ``global`` rules; the per-session / per-user scopes are a
        future addition. Returns ``True`` when no rule matches (the
        agent's default is "ask, but the store doesn't block").
        Factory defaults (:data:`DEFAULT_RULES`) participate via
        :meth:`lookup` — an ``ask`` default still returns ``True``
        here; the agent loop's gater is what turns it into a prompt.
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


__all__ = ["DEFAULT_RULES", "PermissionStore"]
