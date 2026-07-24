"""Tests for v0.6.0 skills — frontmatter fix + 2 new skills.

Validates that:
1. smart-debug and security-audit now load with valid frontmatter
2. workspace-understanding loads correctly
3. dependency-analyzer loads correctly
"""
from __future__ import annotations

import pytest

from minimax_code.agent.skills.loader import load_skill_dir, parse_frontmatter

# ---------------------------------------------------------------------------
# Fixtures: skill directories
# ---------------------------------------------------------------------------

@pytest.fixture
def skills_root(tmp_path):
    """Create a temporary skills root with all 4 skill directories."""
    from pathlib import Path

    # Point to the real skills directory
    real_skills = Path(__file__).resolve().parent.parent / "skills"
    return real_skills


# ---------------------------------------------------------------------------
# smart-debug — frontmatter fix
# ---------------------------------------------------------------------------

class TestSmartDebug:
    """Verify smart-debug loads correctly after frontmatter addition."""

    def test_loads_successfully(self, skills_root):
        skill = load_skill_dir(skills_root / "smart-debug")
        assert skill is not None

    def test_has_name(self, skills_root):
        skill = load_skill_dir(skills_root / "smart-debug")
        assert skill.name == "smart-debug"

    def test_has_version(self, skills_root):
        skill = load_skill_dir(skills_root / "smart-debug")
        assert skill.version == "1.0.0"

    def test_has_description(self, skills_root):
        skill = load_skill_dir(skills_root / "smart-debug")
        assert "debug" in (skill.description or "").lower()

    def test_has_tools(self, skills_root):
        skill = load_skill_dir(skills_root / "smart-debug")
        assert "exec_command" in skill.tools
        assert "read_file" in skill.tools
        assert "edit_file" in skill.tools

    def test_has_body(self, skills_root):
        skill = load_skill_dir(skills_root / "smart-debug")
        assert "Workflow" in skill.body
        assert "Reproduce" in skill.body


# ---------------------------------------------------------------------------
# security-audit — frontmatter fix
# ---------------------------------------------------------------------------

class TestSecurityAudit:
    """Verify security-audit loads correctly after frontmatter addition."""

    def test_loads_successfully(self, skills_root):
        skill = load_skill_dir(skills_root / "security-audit")
        assert skill is not None

    def test_has_name(self, skills_root):
        skill = load_skill_dir(skills_root / "security-audit")
        assert skill.name == "security-audit"

    def test_has_tools(self, skills_root):
        skill = load_skill_dir(skills_root / "security-audit")
        assert "exec_command" in skill.tools
        assert "search_files" in skill.tools

    def test_has_body(self, skills_root):
        skill = load_skill_dir(skills_root / "security-audit")
        assert "Automated Scan" in skill.body or "Manual Review" in skill.body


# ---------------------------------------------------------------------------
# workspace-understanding — new skill
# ---------------------------------------------------------------------------

class TestWorkspaceUnderstanding:
    """Verify the new workspace-understanding skill loads correctly."""

    def test_loads_successfully(self, skills_root):
        skill = load_skill_dir(skills_root / "workspace-understanding")
        assert skill is not None

    def test_has_name(self, skills_root):
        skill = load_skill_dir(skills_root / "workspace-understanding")
        assert skill.name == "workspace-understanding"

    def test_has_tools(self, skills_root):
        skill = load_skill_dir(skills_root / "workspace-understanding")
        assert "glob_find" in skill.tools
        assert "read_file" in skill.tools
        assert "search_files" in skill.tools

    def test_has_workflow(self, skills_root):
        skill = load_skill_dir(skills_root / "workspace-understanding")
        assert "Scan" in skill.body
        assert "Map" in skill.body


# ---------------------------------------------------------------------------
# dependency-analyzer — new skill
# ---------------------------------------------------------------------------

class TestDependencyAnalyzer:
    """Verify the new dependency-analyzer skill loads correctly."""

    def test_loads_successfully(self, skills_root):
        skill = load_skill_dir(skills_root / "dependency-analyzer")
        assert skill is not None

    def test_has_name(self, skills_root):
        skill = load_skill_dir(skills_root / "dependency-analyzer")
        assert skill.name == "dependency-analyzer"

    def test_has_tools(self, skills_root):
        skill = load_skill_dir(skills_root / "dependency-analyzer")
        assert "exec_command" in skill.tools
        assert "read_file" in skill.tools

    def test_has_audit_workflow(self, skills_root):
        skill = load_skill_dir(skills_root / "dependency-analyzer")
        assert "Audit" in skill.body
        assert "Detect" in skill.body


# ---------------------------------------------------------------------------
# Frontmatter parsing — regression
# ---------------------------------------------------------------------------

class TestFrontmatterParsing:
    """Verify parse_frontmatter works on all 4 SKILL.md files."""

    @pytest.mark.parametrize("skill_name", [
        "smart-debug",
        "security-audit",
        "workspace-understanding",
        "dependency-analyzer",
    ])
    def test_parse_frontmatter(self, skills_root, skill_name):

        skill_file = skills_root / skill_name / "SKILL.md"
        assert skill_file.exists(), f"SKILL.md missing for {skill_name}"

        text = skill_file.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)

        assert meta["name"] == skill_name
        assert len(body) > 50  # non-trivial body
        assert "tools" in meta
        assert isinstance(meta["tools"], list)
        assert len(meta["tools"]) >= 2
