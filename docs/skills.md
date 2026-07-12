# Skills

> **Status:** Local `SKILL.md` import is supported. Marketplace installation,
> per-skill config, and filesystem hot-reload remain future work.

The **skills system** packages reusable agent behaviours as
self-contained folders. A skill is a `SKILL.md` (instructions the LLM
reads when the skill is active) plus optional Python tools the LLM can
call while the skill is invoked.

This document covers:

1. [SKILL.md format](#skillmd-format) — frontmatter spec + body
2. [Tools binding rule](#tools-binding-rule) — how a skill references Python code
3. [Lifecycle](#lifecycle) — load / enable / invoke / tear-down
4. [Authoring a custom skill](#authoring-a-custom-skill) — minimal hello-world
5. [Built-in skills](#built-in-skills) — what ships in the box

## SKILL.md format

A `SKILL.md` is a UTF-8 Markdown file. The first non-empty line must
be `---`; the YAML-ish frontmatter follows, terminated by another
`---`. Everything after the closing `---` is the skill body (injected
into the agent's system prompt verbatim when the skill is active).

```markdown
---
name: hello-world          # required, no whitespace, used as natural key
version: 1.0.0            # optional, default 0.0.0
description: |             # optional, multi-line
  One- or two-line human summary
  shown in the UI.
when_to_use: |             # optional, LLM guidance
  Use this skill when the user wants a friendly greeting.
tools:                     # optional, list of tool names (see below)
  - get_git_diff
---

# Hello World

The body is Markdown. It is appended to the system prompt under the
heading `# Skill: <name> (v<version>)` when the skill is invoked.

Code samples, tables, and bullet lists all work.
```

### Frontmatter rules

* `name` is **required**, must be a non-empty string with no whitespace.
  Two `SKILL.md` files with the same `name` in the same scan root are
  de-duplicated (first wins, the rest are logged and skipped).
* `version` is optional; default `0.0.0`. Shown in the system prompt.
* `description` and `when_to_use` are optional. They support
  YAML block scalars (`|`) for multi-line content. `when_to_use` is
  injected into the system prompt under `## When to use` so the LLM
  has guidance even when the user types an unrelated prompt.
* `tools` is an optional list of tool names. Each name **must**
  exist in the global `ToolRegistry` at invoke time; missing names
  are reported as `missing_tools` in the `skill.get` response but do
  not block loading.
* Unknown frontmatter keys are preserved in `Skill.extra` so a future
  re-serialisation round-trip is lossless.

### Skill ID

Every loaded skill has a stable composite identifier:

```
<dir_name>:<name>
```

For example, a skill at `agent/skills/commit-helper/SKILL.md` with
`name: commit-helper` becomes `commit-helper:commit-helper`. The UI
refers to skills by this id; renaming a folder changes the id.

## Tools binding rule

A skill's `tools:` frontmatter is a **list of names** that must be
present in the agent's global `ToolRegistry` at invoke time. The
runtime *temporarily* adds the skill's Python tool classes to the
registry on `invoke()` and removes them on tear-down. This keeps the
LLM's function-calling payload minimal — skill tools only appear
when the skill is actually active.

The actual Python implementation lives next to the rest of the
agent (in `minimax_code/agent/skills/_builtin/` for built-ins), and a
`SkillToolProvider` is what the runtime calls to install / remove the
tools. The pattern is:

```python
from minimax_code.agent.tools import Tool, ToolResult
from minimax_code.agent.skills.runtime import SkillToolProvider

class MyTool(Tool):
    name = "my_tool"
    description = "What this tool does."
    parameters = {"type": "object", "properties": {...}, "required": [...]}

    async def run(self, **kwargs) -> ToolResult:
        return ToolResult.ok(output={"hello": "world"})

class Provider(SkillToolProvider):
    def install(self, tool_registry):
        if not tool_registry.has(MyTool.name):
            tool_registry.register(MyTool())
        return {MyTool.name}

    def uninstall(self, tool_registry):
        tool_registry.unregister(MyTool.name)
```

Wire it up in `_builtin/__init__.py`:

```python
def install_builtin_providers(runtime):
    skill = runtime.registry.get_by_name("hello-world")
    if skill is None:
        return
    runtime.register_tool_provider(skill, Provider())
```

A skill that *doesn't* declare any `tools:` is still useful — it can
just inject instructions into the system prompt without adding new
tool calls.

## Lifecycle

```
                     ┌────────────────────────┐
                     │  agent/skills/<name>/  │   on-disk artefacts
                     │  SKILL.md               │
                     └──────────┬─────────────┘
                                │ loader.load_all
                                ▼
                     ┌────────────────────────┐
                     │  SkillRegistry         │   in-memory + DB
                     │  - skill_id → Skill    │
                     │  - enabled flag        │
                     └──────────┬─────────────┘
                                │ runtime.invoke
                                ▼
   ┌───────────────────────────────────────────────┐
   │ 1. provider.install(tool_registry)             │
   │ 2. AgentCore with skill.instructions injected │
   │ 3. Stream assistant chunks / tool calls       │
   │ 4. provider.uninstall(tool_registry)          │
   └───────────────────────────────────────────────┘
```

1. **Load.** `SkillRegistry.load_all()` walks the configured skills
   root, parses each `SKILL.md`, and inserts a `Skill` object. A
   fresh skill defaults to `enabled=True`; if the row already
   exists in the SQLite `skills` table, the persisted `enabled` flag
   is restored.
2. **Enable / disable.** `enable(skill_id)` and `disable(skill_id)`
   flip the in-memory flag and mirror the change to the DB.
3. **Invoke.** `runtime.invoke(skill_id, request, …)`:
   1. Calls `provider.install(tool_registry)` — the LLM now sees
      the skill's tools in its function-calling payload.
   2. Constructs a fresh `AgentCore` with `skill.instructions`
      appended to the system prompt.
   3. Streams assistant chunks, tool calls, and tool results via
      the supplied callbacks.
   4. On completion (or error), `provider.uninstall(tool_registry)`
      removes the skill's tools and the registry is back to its
      baseline.
4. **Reload.** `registry.reload()` drops the in-memory state and
   re-scans disk. Phase 2 will replace this with a filesystem
   watcher for hot-reload.

## Authoring a custom skill

1. Create a `SKILL.md` with at least a `name` frontmatter entry.
2. Import it from the Skills page. The agent stores it under the personal data
   directory, separate from the built-in `agent/skills/` tree.
3. If the skill needs custom tools, add Python modules under
   `minimax_code/agent/skills/_builtin/<your-skill>/` and wire a
   `Provider` in `_builtin/__init__.py`. (Phase 2 will let you ship
   the Python in the skill folder itself via dynamic import.)
4. Restart the agent, or call `runtime.registry.reload()`.

### Minimal hello-world skill

Folder layout:

```
agent/skills/hello-world/
└── SKILL.md
```

`SKILL.md`:

```markdown
---
name: hello-world
version: 1.0.0
description: |
  Greets the user with a personalised hello.
when_to_use: |
  Use this skill when the user types "hello", "hi", or
  asks the agent to introduce itself.
tools: []
---

# Hello World

When this skill is active, the system prompt instructs the
agent to:

1. Read the user's name from the most recent message (or ask if
   unknown).
2. Reply with a friendly greeting that includes the current
   date and a one-line weather-style description of the
   workspace.
3. Keep the response to a single sentence.
```

The skill has no `tools:`, so it works without any Python code —
the body alone is enough to bias the agent's behaviour. After a
restart, the skill appears in the registry and can be invoked
through the `skill.invoke` IPC method.

## Built-in skills

| Skill              | Tools                                              | Purpose |
|--------------------|----------------------------------------------------|---------|
| `commit-helper`    | `get_git_diff`                                     | Generate Conventional Commits messages from a `git diff`. |
| `code-review`      | `run_linter`, `find_complex_functions`             | Static review of a Python file / directory. |
| `test-generator`   | `extract_functions`, `analyze_function_dependencies` | Scaffold pytest test files for a Python function. |

All three are documented in their own `SKILL.md` files under
`agent/skills/<name>/SKILL.md`.

## Where to look in the code

| Concern                | Module                                    |
|------------------------|-------------------------------------------|
| Frontmatter parser     | `minimax_code/agent/skills/loader.py`     |
| In-memory + DB state   | `minimax_code/agent/skills/registry.py`   |
| Invoke + tool install  | `minimax_code/agent/skills/runtime.py`    |
| Public entry point     | `minimax_code/agent/skills/__init__.py`   |
| Built-in tools         | `minimax_code/agent/skills/_builtin/`     |
| IPC handlers           | `minimax_code/ipc/handlers_skills.py`     |
| App bootstrap          | `minimax_code/app.py`                     |
| Unit tests             | `agent/tests/test_skills.py`              |
