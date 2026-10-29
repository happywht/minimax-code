"""Opt-in recursive resource ownership for admitted execution profiles."""

from contextvars import ContextVar

owned_runtime: ContextVar[dict | None] = ContextVar("owned_runtime", default=None)


def capture_provider_identities(runtime, bindings):
    """Remember the live provider objects associated with accepted skills."""
    if runtime is None:
        return {}
    return {item["id"]: runtime._tool_providers.get(item["id"])
            for item in bindings.get("skills", []) if item.get("provider_tools")}


def require_live_owned_tool(name: str) -> None:
    """Reject a frozen provider tool after its skill or provider is revoked."""
    owner = owned_runtime.get()
    if not owner:
        return
    bindings = owner.get("tool_bindings")
    if not isinstance(bindings, dict) or not isinstance(bindings.get("skills"), list):
        raise PermissionError("Owned tool provenance is unavailable")
    relevant = [item for item in bindings["skills"]
                if isinstance(item, dict) and name in item.get("provider_tools", [])]
    if not relevant:
        return
    runtime = owner.get("skill_runtime")
    identities = owner.get("provider_identities")
    if runtime is None or not isinstance(identities, dict):
        raise PermissionError("Skill provider authorization is unavailable")
    for item in relevant:
        skill_id = item["id"]
        try:
            skill = runtime.registry.get(skill_id)
        except KeyError:
            raise PermissionError(f"Skill {skill_id!r} was removed") from None
        provider = runtime._tool_providers.get(skill_id)
        if (not skill.enabled or skill.version != item["version"]
                or list(skill.tools) != item["manifest_tools"] or provider is None
                or identities.get(skill_id) is not provider):
            raise PermissionError(f"Skill provider {skill_id!r} is disabled or changed")
