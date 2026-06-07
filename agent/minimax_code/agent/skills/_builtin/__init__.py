"""Built-in skill tool providers.

This module wires the 6 built-in skills shipped in
``agent/skills/`` to the runtime:

* ``commit-helper``      — :mod:`.commit_helper`
* ``code-review``        — :mod:`.code_review`
* ``test-generator``     — :mod:`.test_generator`
* ``refactor-assistant``  — :mod:`.refactor`
* ``coding-standards``   — :mod:`.coding_standards`
* ``doc-generator``      — :mod:`.doc_generator`

Each sub-module exposes a :class:`SkillToolProvider` subclass and
an ``install(runtime)`` helper. The application bootstrap calls
:func:`install_builtin_providers` to register all of them.
"""

from __future__ import annotations

import logging
from typing import Any

from ..loader import Skill
from ..runtime import SkillRuntime, SkillToolProvider

logger = logging.getLogger(__name__)


def install_builtin_providers(runtime: SkillRuntime) -> int:
    """Install every built-in skill's tool provider on ``runtime``.

    Returns the number of providers actually installed (a skill
    whose ``SKILL.md`` is missing on disk is silently skipped,
    with a warning). Idempotent: a second call replaces the
    previous provider for any given skill.
    """
    from . import (
        code_review,
        coding_standards,
        commit_helper,
        doc_generator,
        refactor,
        test_generator,
    )

    count = 0
    for module, name in (
        (commit_helper, "commit-helper"),
        (code_review, "code-review"),
        (test_generator, "test-generator"),
        (refactor, "refactor-assistant"),
        (coding_standards, "coding-standards"),
        (doc_generator, "doc-generator"),
    ):
        skill = runtime.registry.get_by_name(name)
        if skill is None:
            logger.info("built-in skill %r not on disk; skipping provider install", name)
            continue
        provider = module.Provider()
        runtime.register_tool_provider(skill, provider)
        count += 1
    logger.info("installed %d built-in skill tool provider(s)", count)
    return count


__all__ = ["install_builtin_providers"]
