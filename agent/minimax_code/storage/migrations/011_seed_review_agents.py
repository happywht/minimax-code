"""Seed review-oriented sub-agents for v0.8.0.

Inserts 3 pre-configured agents focused on code review dimensions:
security-reviewer, performance-reviewer, style-reviewer.

``INSERT OR IGNORE`` keeps the migration idempotent — re-running it
on a DB that already has these rows is a no-op.

v0.8.0 — Enterprise Multi-Agent.
"""

from __future__ import annotations

from typing import Any

from . import run_script

VERSION = 11

DDL = r"""
-- ---------------------------------------------------------------------------
-- Seed v0.8.0 review agents
-- ---------------------------------------------------------------------------
-- Idempotent: INSERT OR IGNORE skips rows whose ``name`` already exists.
INSERT OR IGNORE INTO agents (
    name, system_prompt, tool_allowlist, model,
    description, enabled, icon, color, category, tags,
    skills, max_iterations
)
VALUES (
    'security-reviewer',
    'You are a security-focused code reviewer. Analyze code for vulnerabilities: hard-coded secrets, SQL injection, insecure deserialization, weak crypto, and other OWASP Top 10 issues. Provide severity ratings and remediation advice.',
    '["security_scan","read_file","search_files"]',
    NULL,
    'Scans for security vulnerabilities and OWASP issues',
    1,
    'ShieldAlert',
    'red',
    'review',
    '["security","vulnerability","OWASP"]',
    '["code-review"]',
    10
);

INSERT OR IGNORE INTO agents (
    name, system_prompt, tool_allowlist, model,
    description, enabled, icon, color, category, tags,
    skills, max_iterations
)
VALUES (
    'performance-reviewer',
    'You are a performance-focused code reviewer. Identify N+1 queries, unnecessary copies, sync blocking in async code, inefficient algorithms, and memory waste. Suggest concrete optimizations with expected impact.',
    '["performance_check","read_file","search_files"]',
    NULL,
    'Detects performance anti-patterns and suggests optimizations',
    1,
    'Zap',
    'yellow',
    'review',
    '["performance","optimization","N+1"]',
    '["code-review"]',
    10
);

INSERT OR IGNORE INTO agents (
    name, system_prompt, tool_allowlist, model,
    description, enabled, icon, color, category, tags,
    skills, max_iterations
)
VALUES (
    'style-reviewer',
    'You are a coding standards and style reviewer. Check naming conventions, docstring coverage, type annotation completeness, and PEP 8 compliance. Help the team maintain consistent, readable code.',
    '["check_style","check_naming","check_docstring","type_check","read_file"]',
    NULL,
    'Enforces coding standards and style conventions',
    1,
    'Paintbrush',
    'blue',
    'review',
    '["style","naming","docstring","type-safety"]',
    '["code-review","coding-standards"]',
    10
);
"""


def run(conn: Any) -> None:
    """Apply the seed-review-agents migration to ``conn``."""
    run_script(conn, DDL)


__all__ = ["DDL", "VERSION", "run"]
