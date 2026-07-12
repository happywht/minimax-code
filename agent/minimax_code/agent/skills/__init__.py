"""Skill system — loader, registry, runtime, IPC handlers.

Public surface
--------------

* :class:`~.loader.Skill`, :func:`~.loader.load_all`,
  :func:`~.loader.parse_frontmatter`, :class:`~.loader.SkillLoadError`
* :class:`~.registry.SkillRegistry`, :class:`~.registry.SkillNotFoundError`,
  :func:`~.registry.get_default_registry`
* :class:`~.runtime.SkillRuntime`, :class:`~.runtime.SkillToolProvider`,
  :class:`~.runtime.SkillInvokeResult`, :class:`~.runtime.SkillInvokeError`
* :func:`build_runtime` — one-call helper that wires the default
  registry, default tool registry, and a fresh runtime together.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .loader import (
    Skill,
    SkillLoadError,
    load_all,
    load_skill_dir,
    load_skill_file,
    make_skill_id,
    parse_frontmatter,
    validate_tools,
)
from .registry import (
    SkillNotFoundError,
    SkillRegistry,
    get_default_registry,
    set_default_registry,
)
from .runtime import (
    ChunkCallback,
    SkillInvokeError,
    SkillInvokeResult,
    SkillRuntime,
    SkillToolProvider,
    StatusCallback,
    ToolCallCallback,
    ToolResultCallback,
)

logger = logging.getLogger(__name__)


def build_runtime(
    *,
    skills_root: Path | str | None = None,
    db: Any | None = None,
    llm: Any | None = None,
    tool_registry: Any | None = None,
    extra_roots: Iterable[Path | str] = (),
) -> SkillRuntime:
    """Build a :class:`SkillRuntime` wired to the default registries.

    If ``tool_registry`` is ``None``, the agent's default tool
    registry is used (which contains the built-in tools). Tests
    can pass a custom :class:`ToolRegistry` to scope state.

    The runtime is returned *uninitialised* — call
    :meth:`SkillRuntime.registry.load_all` (or use
    :func:`bootstrap`) before exposing it to the IPC layer.
    """
    from .registry import _default_skills_root  # local — config wiring

    registry = SkillRegistry(
        skills_root=Path(skills_root) if skills_root else _default_skills_root(),
        db=db,
        extra_roots=extra_roots,
        auto_persist=db is not None,
    )
    if tool_registry is None:
        # Import the tools package as a side effect so its
        # built-in tools self-register, then return the
        # process-wide registry.
        from .. import tools as _tools  # noqa: F401
        from ..tools.base import get_default_registry

        tool_registry = get_default_registry()
    return SkillRuntime(
        registry=registry,
        llm=llm,
        tool_registry=tool_registry,
    )


async def bootstrap(
    *,
    skills_root: Path | str | None = None,
    db: Any | None = None,
    llm: Any | None = None,
    tool_registry: Any | None = None,
    extra_roots: Iterable[Path | str] = (),
) -> SkillRuntime:
    """Async one-stop: build the runtime, load skills, and install tool providers.

    Returns a fully-initialised :class:`SkillRuntime` ready to
    serve :meth:`SkillRuntime.invoke` calls. Used by the
    application bootstrap (``app.py``).
    """
    from ._builtin import install_builtin_providers  # local: avoid heavy import at module load

    runtime = build_runtime(
        skills_root=skills_root,
        db=db,
        llm=llm,
        tool_registry=tool_registry,
        extra_roots=extra_roots,
    )
    await runtime.registry.load_all()

    # Cache available tool names so the registry can flag missing tools.
    if runtime._default_tool_registry is not None:
        runtime.registry.set_tools_available(runtime._default_tool_registry.names())

    # Wire built-in skill tools into the runtime.
    install_builtin_providers(runtime)

    return runtime


__all__ = [
    # loader
    "Skill",
    "SkillLoadError",
    "load_all",
    "load_skill_dir",
    "load_skill_file",
    "make_skill_id",
    "parse_frontmatter",
    "validate_tools",
    # registry
    "SkillNotFoundError",
    "SkillRegistry",
    "get_default_registry",
    "set_default_registry",
    # runtime
    "ChunkCallback",
    "SkillInvokeError",
    "SkillInvokeResult",
    "SkillRuntime",
    "SkillToolProvider",
    "StatusCallback",
    "ToolCallCallback",
    "ToolResultCallback",
    # helpers
    "bootstrap",
    "build_runtime",
]
