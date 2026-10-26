"""Frozen skill dependencies for a single Native agent, without another model loop."""

from __future__ import annotations

import copy
from dataclasses import dataclass

from .skill_execution import _digest, _provider_tools


def skill_ids(values):
    if values is None:
        return []
    if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
        raise ValueError("skills must be a list of non-empty strings")
    if len(set(values)) != len(values):
        raise ValueError("Duplicate skill dependency")
    return list(values)


def resolve_skills(runtime, values):
    resolved = []
    for value in skill_ids(values):
        if runtime is None:
            raise ValueError("Skill registry is unavailable")
        if runtime.registry.has(value):
            skill = runtime.registry.get(value)
        else:
            matches = [s for s in runtime.registry.list() if s.name == value]
            if len(matches) != 1:
                raise ValueError(
                    f"Skill dependency {value!r} is unknown or ambiguous; use its exact ID"
                )
            skill = matches[0]
        if not skill.enabled:
            raise ValueError(f"Skill {skill.skill_id!r} is disabled")
        if skill.skill_id in [s.skill_id for s in resolved]:
            raise ValueError("Duplicate canonical skill dependency")
        resolved.append(skill)
    return resolved


def tool_identity(tools):
    return _digest(
        [
            {
                "name": t.name,
                "class": f"{type(t).__module__}.{type(t).__qualname__}",
                "parameters": t.parameters,
                "description": t.description,
            }
            for t in sorted(tools, key=lambda t: t.name)
        ]
    )


def check_bindings(data):
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or not isinstance(data.get("skills"), list)
        or data.get("digest") != _digest({k: v for k, v in data.items() if k != "digest"})
    ):
        raise ValueError("Saved skill bindings are incompatible; start a new task")
    seen = set()
    for item in data["skills"]:
        if (
            not isinstance(item, dict)
            or not {
                "id",
                "version",
                "instructions",
                "manifest_tools",
                "provider_tools",
                "provider_digest",
                "tool_identity",
            }.issubset(item)
            or not isinstance(item["id"], str)
            or not item["id"]
            or item["id"] in seen
            or not isinstance(item["version"], str)
            or not isinstance(item["instructions"], str)
            or not isinstance(item["manifest_tools"], list)
            or not isinstance(item["provider_tools"], list)
            or any(not isinstance(name, str) or not name for name in item["manifest_tools"])
            or any(not isinstance(name, str) or not name for name in item["provider_tools"])
            or not isinstance(item["provider_digest"], str)
            or not isinstance(item["tool_identity"], str)
        ):
            raise ValueError("Saved skill bindings are damaged; start a new task")
        seen.add(item["id"])


@dataclass(frozen=True)
class SkillBindings:
    data: dict
    tools: tuple = ()

    def __post_init__(self):
        check_bindings(self.data)

    @property
    def instructions(self):
        return "".join(f"\n\n## Skill: {s['id']}\n{s['instructions']}" for s in self.data["skills"])

    @classmethod
    def capture(cls, runtime, values):
        items, tools = [], []
        for skill in resolve_skills(runtime, values):
            _, exports, digest = _provider_tools(runtime, skill)
            items.append(
                dict(
                    id=skill.skill_id,
                    version=skill.version,
                    instructions=skill.instructions,
                    manifest_tools=list(skill.tools),
                    provider_tools=[t.name for t in exports],
                    provider_digest=digest,
                    tool_identity=tool_identity(exports),
                )
            )
            tools.extend(exports)
        data = dict(version=1, skills=items)
        data["digest"] = _digest(data)
        return cls(data, tuple(tools))

    def validate(self, runtime, values):
        check_bindings(self.data)
        current = self.capture(runtime, values)

        # Body edits at the same declared version never replace accepted text.
        def identity(item):
            return {k: v for k, v in item.items() if k != "instructions"}

        if [identity(s) for s in current.data["skills"]] != [
            identity(s) for s in self.data["skills"]
        ]:
            raise ValueError("Saved skill dependencies or providers changed; start a new task")
        return type(self)(copy.deepcopy(self.data), current.tools)

    def candidates(self, baseline, allowlist, *, parent_cap=None, inherited_provider_names=()):
        candidates = {t.name: t for t in baseline.list()}
        added = set()
        for tool in self.tools:
            if tool.name in {"report_completion", "report_progress"}:
                raise ValueError(f"Skill provider uses reserved receipt channel: {tool.name}")
            if tool.name in added or (
                tool.name in candidates
                and (
                    tool.name not in inherited_provider_names
                    or tool_identity([tool]) != tool_identity([candidates[tool.name]])
                )
            ):
                raise ValueError(f"Skill provider tool name collision: {tool.name}")
            added.add(tool.name)
            candidates[tool.name] = tool
        names = list(candidates) if allowlist is None else list(allowlist)
        if len(names) != len(set(names)) or set(names) - candidates.keys():
            raise ValueError("Agent allowlist contains duplicate or unavailable tools")
        required = {name for s in self.data["skills"] for name in s["manifest_tools"]}
        if required - set(names):
            raise ValueError(
                f"Agent allowlist excludes required skill tools: {sorted(required - set(names))}"
            )
        if parent_cap is not None:
            # None means inherit all available parent capabilities. Explicit
            # lists and required skills may never silently lose capabilities.
            if allowlist is None:
                names = [name for name in names if name in parent_cap]
            if (set(names) | required) - set(parent_cap):
                raise ValueError("Child skill/tools exceed the accepted parent capability")
        # Only selected baseline tools need cloning. Clone them together so
        # shared run-local tool state stays shared within this private registry.
        private_baseline = copy.deepcopy(
            {name: candidates[name] for name in names if name not in added}
        )
        return tuple(
            candidates[name] if name in added else private_baseline[name] for name in names
        )


def tool_dependencies(bindings, names):
    """Retain provenance only for provider tools the child actually inherits."""
    items = {}
    for data in bindings:
        check_bindings(data)
        for item in data["skills"]:
            if not set(item["provider_tools"]) & set(names):
                continue
            old = items.get(item["id"])
            if old is not None and (
                {k: v for k, v in old.items() if k != "instructions"}
                != {k: v for k, v in item.items() if k != "instructions"}
            ):
                raise ValueError("Inherited skill provider identities conflict")
            items[item["id"]] = copy.deepcopy(item)
    data = dict(version=1, skills=list(items.values()))
    data["digest"] = _digest(data)
    return data
