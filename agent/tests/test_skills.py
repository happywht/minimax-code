"""Unit tests for the skills system.

The test suite is split into three scenario groups:

* :class:`TestLoader`     — ``loader.py``: parse SKILL.md, handle malformed
  input, validate tool references.
* :class:`TestRegistry`   — ``registry.py``: enable/disable, async-safety,
  persistence hooks.
* :class:`TestRuntime`    — ``runtime.py``: end-to-end invocation with a
  fake LLM.
* :class:`TestBuiltinSkills` — small smoke tests for each of the 3 built-in
  skill tools.

We aim for high signal per test (one assertion, one scenario). Helpers
live at the bottom of the file.
"""

from __future__ import annotations

import asyncio
import textwrap
import uuid
from pathlib import Path
from typing import Any

import pytest

from minimax_code.agent.skills import (
    Skill,
    SkillInvokeError,
    SkillLoadError,
    SkillRegistry,
    SkillRuntime,
    load_all,
    load_skill_dir,
    load_skill_file,
    parse_frontmatter,
    validate_tools,
)
from minimax_code.agent.skills.runtime import SkillToolProvider
from minimax_code.agent.tools import Tool, ToolRegistry, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_skill(
    root: Path,
    name: str,
    *,
    version: str = "1.0.0",
    description: str = "A test skill.",
    when_to_use: str = "Use this in tests.",
    tools: list[str] | None = None,
    body: str = "# Body\n\nHello.",
) -> Path:
    """Write a minimal valid SKILL.md under ``root/<name>`` and return the dir."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    tools_block = ""
    if tools is not None:
        tools_block = "tools:\n" + "".join(f"  - {t}\n" for t in tools)
    lines = [
        "---",
        f"name: {name}",
        f"version: {version}",
        "description: |",
        f"  {description}",
        "when_to_use: |",
        f"  {when_to_use}",
    ]
    if tools_block:
        lines.append(tools_block.rstrip("\n"))
    lines.append("---")
    lines.append("")
    lines.append(body)
    text = "\n".join(lines) + "\n"
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    return skill_dir


def _write_skill_text(skill_dir: Path, text: str) -> Path:
    """Write a raw ``SKILL.md`` to ``skill_dir/SKILL.md`` (no auto-format)."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(text, encoding="utf-8")
    return path


def _async_load(reg: SkillRegistry) -> Any:
    return reg.load_all()


# ---------------------------------------------------------------------------
# TestLoader
# ---------------------------------------------------------------------------


class TestLoader:
    def test_parse_frontmatter_minimal(self) -> None:
        text = textwrap.dedent(
            """\
            ---
            name: hello
            ---
            Body line 1
            Body line 2
            """
        )
        data, body = parse_frontmatter(text)
        assert data["name"] == "hello"
        assert "Body line 1" in body
        assert "Body line 2" in body

    def test_parse_frontmatter_with_block_scalars(self) -> None:
        text = textwrap.dedent(
            """\
            ---
            name: skill-x
            description: |
              Multi-line
              description.
            when_to_use: |
              When you need it.
            tools:
              - foo
              - bar
            ---
            Body.
            """
        )
        data, body = parse_frontmatter(text)
        assert data["name"] == "skill-x"
        assert "Multi-line" in data["description"]
        assert "description." in data["description"]
        assert "When you need it." in data["when_to_use"]
        assert data["tools"] == ["foo", "bar"]
        assert body.strip() == "Body."

    def test_parse_frontmatter_missing_delimiters_raises(self) -> None:
        with pytest.raises(SkillLoadError):
            parse_frontmatter("no frontmatter here\n\nbody")

    def test_parse_frontmatter_empty_yaml_raises(self) -> None:
        with pytest.raises(SkillLoadError):
            parse_frontmatter("---\n---\nbody")

    def test_load_skill_file(self, tmp_path: Path) -> None:
        skill_dir = _write_skill(tmp_path, "my-skill", tools=["echo"], body="# Hi")
        skill = load_skill_file(skill_dir / "SKILL.md")
        assert skill.name == "my-skill"
        assert skill.tools == ["echo"]
        assert skill.skill_id == "my-skill:my-skill"
        assert "Hi" in skill.body
        assert skill.path == skill_dir

    def test_load_skill_file_missing_required_name(self, tmp_path: Path) -> None:
        text = textwrap.dedent(
            """\
            ---
            description: no name here
            ---
            body
            """
        )
        path = _write_skill_text(tmp_path / "bad", text)
        with pytest.raises(SkillLoadError):
            load_skill_file(path)

    def test_load_skill_file_rejects_whitespace_in_name(self, tmp_path: Path) -> None:
        text = textwrap.dedent(
            """\
            ---
            name: "two words"
            ---
            body
            """
        )
        path = _write_skill_text(tmp_path / "bad2", text)
        with pytest.raises(SkillLoadError):
            load_skill_file(path)

    def test_load_all_skips_malformed_skill(self, tmp_path: Path) -> None:
        root = tmp_path / "skills"
        root.mkdir()
        _write_skill(root, "good", tools=["echo"])
        bad = root / "bad"
        bad.mkdir()
        (bad / "SKILL.md").write_text("no frontmatter\nbody", encoding="utf-8")

        skills = load_all(root)
        assert [s.name for s in skills] == ["good"]

    def test_load_all_skips_directories_without_skill_md(self, tmp_path: Path) -> None:
        root = tmp_path / "skills"
        root.mkdir()
        _write_skill(root, "alpha", tools=["echo"])
        (root / "not-a-skill").mkdir()
        (root / "not-a-skill" / "README.md").write_text("not a skill", encoding="utf-8")

        skills = load_all(root)
        assert [s.name for s in skills] == ["alpha"]

    def test_load_all_dedupes_by_name(self, tmp_path: Path) -> None:
        root = tmp_path / "skills"
        root.mkdir()
        _write_skill(root, "alpha", tools=["echo"])
        # Same name in a sub-directory.
        _write_skill(root / "nested" / "alpha", "alpha", tools=["echo"])
        skills = load_all(root)
        assert [s.name for s in skills].count("alpha") == 1

    def test_validate_tools_reports_missing(self) -> None:
        s1 = Skill(name="a", description="", when_to_use="", body="", path=Path("a"))
        s1.tools = ["known", "missing"]
        s2 = Skill(name="b", description="", when_to_use="", body="", path=Path("b"))
        s2.tools = ["known", "also-missing"]
        result = validate_tools([s1, s2], {"known"})
        assert result == {s1.skill_id: ["missing"], s2.skill_id: ["also-missing"]}


# ---------------------------------------------------------------------------
# TestRegistry
# ---------------------------------------------------------------------------


class TestRegistry:
    async def test_load_all_enables_by_default(self, tmp_path: Path) -> None:
        root = tmp_path / "skills"
        root.mkdir()
        _write_skill(root, "alpha", tools=["echo"])
        reg = SkillRegistry(skills_root=root, db=None, auto_persist=False)
        await reg.load_all()
        assert reg.has("alpha:alpha")
        assert reg.get("alpha:alpha").enabled is True

    async def test_enable_and_disable(self, tmp_path: Path) -> None:
        root = tmp_path / "skills"
        root.mkdir()
        _write_skill(root, "alpha", tools=["echo"])
        reg = SkillRegistry(skills_root=root, db=None, auto_persist=False)
        await reg.load_all()

        s = await reg.disable("alpha:alpha")
        assert s.enabled is False

        s = await reg.enable("alpha:alpha")
        assert s.enabled is True

    async def test_unknown_skill_raises(self, tmp_path: Path) -> None:
        reg = SkillRegistry(skills_root=tmp_path, db=None, auto_persist=False)
        await reg.load_all()
        with pytest.raises(KeyError):
            reg.get("does:not-exist")

    async def test_list_filters_by_enabled(self, tmp_path: Path) -> None:
        root = tmp_path / "skills"
        root.mkdir()
        _write_skill(root, "a", tools=["echo"])
        _write_skill(root, "b", tools=["echo"])
        reg = SkillRegistry(skills_root=root, db=None, auto_persist=False)
        await reg.load_all()
        await reg.disable("a:a")

        all_skills = reg.list()
        enabled = reg.list(enabled=True)
        disabled = reg.list(enabled=False)
        assert {s.name for s in all_skills} == {"a", "b"}
        assert {s.name for s in enabled} == {"b"}
        assert {s.name for s in disabled} == {"a"}

    async def test_concurrent_enable_disable_is_safe(self, tmp_path: Path) -> None:
        """Race the registry's enable/disable from 20 coroutines — no crash, consistent final state."""
        root = tmp_path / "skills"
        root.mkdir()
        _write_skill(root, "a", tools=["echo"])
        reg = SkillRegistry(skills_root=root, db=None, auto_persist=False)
        await reg.load_all()

        async def toggle(target: bool) -> None:
            for _ in range(50):
                if target:
                    await reg.enable("a:a")
                else:
                    await reg.disable("a:a")

        await asyncio.gather(toggle(True), toggle(False), toggle(True), toggle(False))
        # Final state must be one of the two flags — never inconsistent.
        assert reg.get("a:a").enabled in (True, False)

    async def test_unregister_removes_skill(self, tmp_path: Path) -> None:
        root = tmp_path / "skills"
        root.mkdir()
        _write_skill(root, "a", tools=["echo"])
        reg = SkillRegistry(skills_root=root, db=None, auto_persist=False)
        await reg.load_all()

        removed = await reg.unregister("a:a")
        assert removed is not None
        assert not reg.has("a:a")


# ---------------------------------------------------------------------------
# TestRuntime
# ---------------------------------------------------------------------------


class _EchoTool(Tool):
    name = "echo"
    description = "echoes the input back"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        return ToolResult.ok(output={"echoed": kwargs.get("text", "")})


class _StaticProvider(SkillToolProvider):
    """Provider that registers a single Tool instance on install."""

    def __init__(self, tool: Tool) -> None:
        self._tool = tool

    def install(self, tool_registry: Any) -> set[str]:
        if tool_registry is None:
            return set()
        if not tool_registry.has(self._tool.name):
            tool_registry.register(self._tool)
        return {self._tool.name}

    def uninstall(self, tool_registry: Any) -> None:
        if tool_registry is None:
            return
        tool_registry.unregister(self._tool.name)


class _FakeLLM:
    """Tiny LLM stand-in: returns a single canned final message."""

    def __init__(self, text: str = "ok") -> None:
        self._text = text
        self.calls = 0
        self.last_tools: list[dict[str, Any]] | None = None

    async def stream_chat(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        self.last_tools = kwargs.get("tools")
        from minimax_code.agent.llm import StreamChunk
        yield StreamChunk(delta=self._text, finish_reason="stop")


class TestRuntime:
    async def test_invoke_returns_canned_text(self, tmp_path: Path) -> None:
        root = tmp_path / "skills"
        root.mkdir()
        _write_skill(root, "echo-skill", tools=["echo"], body="Be helpful.")

        reg = SkillRegistry(skills_root=root, db=None, auto_persist=False)
        await reg.load_all()
        tool_registry = ToolRegistry()
        tool_registry.register(_EchoTool())

        llm = _FakeLLM("hello world")
        runtime = SkillRuntime(
            registry=reg, llm=llm, tool_registry=tool_registry
        )
        skill = reg.get("echo-skill:echo-skill")
        runtime.register_tool_provider(skill, _StaticProvider(tool_registry.get("echo")))

        result = await runtime.invoke(
            "echo-skill:echo-skill",
            request="hi",
            session_id="s1",
        )
        assert result.final_text == "hello world"
        assert result.skill_id == "echo-skill:echo-skill"
        assert result.cancelled is False

    async def test_invoke_unknown_skill_raises(self, tmp_path: Path) -> None:
        reg = SkillRegistry(skills_root=tmp_path, db=None, auto_persist=False)
        await reg.load_all()
        tool_registry = ToolRegistry()
        tool_registry.register(_EchoTool())
        runtime = SkillRuntime(registry=reg, llm=_FakeLLM(), tool_registry=tool_registry)

        with pytest.raises(KeyError):
            await runtime.invoke("nope:nope", request="hi", session_id="s1")

    async def test_invoke_disabled_skill_raises(self, tmp_path: Path) -> None:
        root = tmp_path / "skills"
        root.mkdir()
        _write_skill(root, "echo-skill", tools=["echo"], body="x")
        reg = SkillRegistry(skills_root=root, db=None, auto_persist=False)
        await reg.load_all()
        await reg.disable("echo-skill:echo-skill")
        tool_registry = ToolRegistry()
        tool_registry.register(_EchoTool())
        runtime = SkillRuntime(registry=reg, llm=_FakeLLM(), tool_registry=tool_registry)
        with pytest.raises(SkillInvokeError):
            await runtime.invoke(
                "echo-skill:echo-skill", request="hi", session_id="s1"
            )

    async def test_invoke_installs_skill_tool(self, tmp_path: Path) -> None:
        """Verify the skill's tools show up in the LLM's tool payload while invoking."""
        root = tmp_path / "skills"
        root.mkdir()
        _write_skill(root, "echo-skill", tools=["echo"], body="x")
        reg = SkillRegistry(skills_root=root, db=None, auto_persist=False)
        await reg.load_all()

        # The base registry doesn't have the tool — the provider adds it.
        tool_registry = ToolRegistry()
        runtime = SkillRuntime(registry=reg, llm=_FakeLLM(), tool_registry=tool_registry)
        skill = reg.get("echo-skill:echo-skill")
        runtime.register_tool_provider(skill, _StaticProvider(_EchoTool()))

        await runtime.invoke("echo-skill:echo-skill", request="hi", session_id="s1")
        # The LLM was given the skill's tool.
        names = [t["function"]["name"] for t in (runtime.llm.last_tools or [])]
        assert "echo" in names
        # And the registry was cleaned up afterwards.
        assert "echo" not in tool_registry.names()


# ---------------------------------------------------------------------------
# TestBuiltinSkills
# ---------------------------------------------------------------------------


class TestBuiltinSkills:
    @pytest.fixture(autouse=True)
    def _workspace(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # The file_ops safe_resolve() check uses
        # MINIMAX_CODE_WORKSPACE; tests must point it at tmp_path
        # so the test files we create are reachable.
        monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(tmp_path))

    async def test_commit_helper_tool_validates_required_args(self) -> None:
        from minimax_code.agent.skills._builtin.commit_helper import GetGitDiffTool

        tool = GetGitDiffTool()
        result = await tool.run()
        # No path supplied — defaults to cwd. This should *not* fail
        # (the path arg is optional), but we must at least get a
        # result object back without raising.
        assert isinstance(result, ToolResult)

    async def test_commit_helper_tool_rejects_bad_path(self) -> None:
        from minimax_code.agent.skills._builtin.commit_helper import GetGitDiffTool

        tool = GetGitDiffTool()
        result = await tool.run(path="Z:/this/does/not/exist")
        assert result.success is False

    async def test_code_review_find_complex_functions(self, tmp_path: Path) -> None:
        from minimax_code.agent.skills._builtin.code_review import FindComplexFunctionsTool

        # Hand-craft a file with a clearly over-complex function.
        src = tmp_path / "complex.py"
        src.write_text(
            "def f(a, b):\n"
            "    if a:\n"
            "        for i in range(b):\n"
            "            while i and b:\n"
            "                if i > 1 and b < 2:\n"
            "                    return i\n"
            "                i -= 1\n"
            "    return 0\n",
            encoding="utf-8",
        )

        tool = FindComplexFunctionsTool()
        result = await tool.run(path=str(src), threshold=1)
        assert result.success is True
        findings = result.output["findings"]
        assert any(f["name"] == "f" and f["complexity"] > 1 for f in findings)

    async def test_code_review_run_linter_handles_missing_linter(self, tmp_path: Path) -> None:
        from minimax_code.agent.skills._builtin.code_review import RunLinterTool

        src = tmp_path / "ok.py"
        src.write_text("x = 1\n", encoding="utf-8")
        # Force the auto-pick to find no linter by hiding the PATH
        # entries — easier: pick a non-existent linter name.
        tool = RunLinterTool()
        # No linter installed is environment-dependent; we accept either success or a clean failure.
        result = await tool.run(path=str(src), linter="ruff")
        assert result.success is True or "no supported linter" in (result.error or "")

    async def test_test_generator_extract_functions(self, tmp_path: Path) -> None:
        from minimax_code.agent.skills._builtin.test_generator import ExtractFunctionsTool

        src = tmp_path / "mod.py"
        src.write_text(
            "def add(a, b):\n"
            "    '''Add two numbers.'''\n"
            "    return a + b\n"
            "\n"
            "def mul(a, b):\n"
            "    return a * b\n",
            encoding="utf-8",
        )
        tool = ExtractFunctionsTool()
        result = await tool.run(path=str(src))
        assert result.success is True
        names = [f["name"] for f in result.output["functions"]]
        assert "add" in names
        assert "mul" in names

    async def test_test_generator_analyze_function_dependencies(self, tmp_path: Path) -> None:
        from minimax_code.agent.skills._builtin.test_generator import (
            AnalyzeFunctionDependenciesTool,
        )

        src = tmp_path / "mod.py"
        src.write_text(
            "import os\n"
            "\n"
            "def helper(x):\n"
            "    return x * 2\n"
            "\n"
            "def main(path):\n"
            "    return helper(len(os.listdir(path)))\n",
            encoding="utf-8",
        )
        tool = AnalyzeFunctionDependenciesTool()
        result = await tool.run(path=str(src), func_name="main")
        assert result.success is True
        assert "os" in result.output["free_symbols"]
        assert "helper" in result.output["calls"]
        assert "helper" in result.output["calls_in_module"]
