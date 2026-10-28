"""The supervised L1 tool surface is the exact built-in exec implementation."""
from __future__ import annotations

from .process_types import ProcessBoundaryError


def assert_text_skills(runtime, values):
    from .skill_bindings import skill_ids
    if skill_ids(values):
        raise ProcessBoundaryError("L1 does not support skills, including text-only skills")


def assert_supported_tools(tools):
    # Exact classes, not module/name strings: a plugin cannot impersonate a
    # reviewed tool by choosing its public name or subclassing it.
    from .agent.tools.terminal import ExecCommandTool
    from .skill_bindings import tool_identity

    if len(tools) != 1:
        raise ProcessBoundaryError("L1 requires exactly one exec_command tool")
    for tool in tools:
        if type(tool) is not ExecCommandTool:
            raise ProcessBoundaryError(f"L1 尚未审计工具实际类: {tool.name}")
    if tool_identity(tools) != tool_identity([ExecCommandTool()]):
        raise ProcessBoundaryError("L1 requires the unchanged exec_command implementation")


def assert_restricted_agent_snapshot(data):
    """Pure public-podman authority check, shared by restore and owner loading.

    Old broader snapshots are rejected rather than silently narrowed. Internal
    process_execution_owner snapshots have their own authority and are not agents.
    """
    from .agent.tools.terminal import ExecCommandTool
    from .skill_bindings import check_bindings, tool_identity

    target = data.get("execution_target")
    if not isinstance(target, dict) or target.get("mode") != "podman":
        return
    bindings = data.get("skill_bindings")
    try:
        check_bindings(bindings)
        assert_text_skills(None, data.get("skills"))
    except ValueError as exc:
        raise ProcessBoundaryError("Saved L1 skill bindings are incompatible") from exc
    if bindings["skills"]:
        raise ProcessBoundaryError("Saved L1 agent cannot contain skills")
    if (data.get("allowlist") != [ExecCommandTool.name]
            or data.get("tool_identity") != tool_identity([ExecCommandTool()])):
        raise ProcessBoundaryError("Saved L1 agent requires the exact exec_command implementation only")
    if not isinstance(data.get("intent"), dict) or data["intent"].get("method") != "agent.invoke":
        raise ProcessBoundaryError("L1 public roots require agent.invoke")
    if (data.get("hooks_policy") != "disabled" or type(data.get("permission_policy_version")) is not int
            or data["permission_policy_version"] != 1):
        raise ProcessBoundaryError("Saved L1 permission or Hooks policy is incompatible")


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
