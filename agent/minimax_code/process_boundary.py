"""Explicit supported L1 tool classes and textual skill admission."""
from __future__ import annotations

from .process_types import ProcessBoundaryError


def assert_text_skills(runtime, values):
    from .skill_bindings import resolve_skills
    for skill in resolve_skills(runtime, values):
        if runtime.has_tool_provider(skill.skill_id) or skill.tools:
            raise ProcessBoundaryError("L1 仅支持没有可执行 provider 的文本技能")


def assert_supported_tools(tools):
    # Exact classes, not module/name strings: a plugin cannot impersonate a
    # reviewed tool by choosing its public name or subclassing it.
    from .agent.tools.artifacts import ReadArtifactTool, ReportCompletionTool, ReportProgressTool
    from .agent.tools.ask_user import AskUserTool
    from .agent.tools.edit import EditFileTool
    from .agent.tools.file_ops import AppendFileTool, ListDirectoryTool, ReadFileTool, WriteFileTool
    from .agent.tools.glob import GlobFindTool
    from .agent.tools.search import SearchFilesTool
    from .agent.tools.terminal import ExecCommandTool

    supported = {ReadArtifactTool, ReportCompletionTool, ReportProgressTool, AskUserTool,
        EditFileTool, AppendFileTool, ListDirectoryTool, ReadFileTool, WriteFileTool,
        GlobFindTool, SearchFilesTool, ExecCommandTool}
    for tool in tools:
        if type(tool) not in supported:
            raise ProcessBoundaryError(f"L1 尚未审计工具实际类: {tool.name}")


def assert_restricted_dispatch(tool):
    from .app import get_execution_profiles
    from .process_context import current_process_target
    if current_process_target() is None:
        return
    catalog = get_execution_profiles()
    if catalog is None:
        raise ProcessBoundaryError("L1 service boundary is unavailable")
    catalog.assert_extension_boundary()
    assert_supported_tools([tool])
