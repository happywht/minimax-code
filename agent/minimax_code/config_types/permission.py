"""Permission-policy config value types (R66).

Fusion of grok-build's ``xai-grok-config-types::permission`` — the leaf
types for the ``[permission]`` section of config.toml: an ordered list of
rules, each pairing an action (allow/deny/ask) with a tool filter and a
glob/domain pattern.

Pure types, zero I/O. Part of R66's runtime config type contract
(alongside :mod:`.flags`, :mod:`.pool`, :mod:`.memory`, :mod:`.mcp`,
:mod:`.types`).

Forward-migrated from Rust (``#[serde(rename_all = "lowercase")]`` +
``#[derive(Default)]``) to :class:`~enum.StrEnum` + pydantic v2.

Wire-fidelity note
------------------

Rust serde ``rename_all = "lowercase"`` lowercases the whole variant name
**without splitting CamelCase**, so the ``WebFetch`` variant serialises as
``"webfetch"`` (not ``"web_fetch"``). The :class:`ToolFilter` member values
below reproduce that exactly; the member is named ``WEB_FETCH`` only to
satisfy PEP-8 / lint.

CWE-1188: :class:`RuleAction` defaults to :attr:`RuleAction.DENY` (not
``ALLOW``) so an accidentally-omitted ``action`` field can never silently
create a catch-all allow rule.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "PatternMode",
    "RuleAction",
    "ToolFilter",
    "PermissionRule",
    "PermissionConfig",
]


class PatternMode(StrEnum):
    """How a rule's ``pattern`` is interpreted.

    Wire form is lowercase (serde ``rename_all = "lowercase"``):
    ``"glob"`` / ``"domain"``. Defaults to :attr:`GLOB`.
    """

    GLOB = "glob"
    DOMAIN = "domain"


class RuleAction(StrEnum):
    """Action taken when a permission rule matches.

    Wire form is lowercase: ``"allow"`` / ``"deny"`` / ``"ask"``.

    Defaults to :attr:`DENY` (CWE-1188): omitting ``action`` in a TOML
    rule must never silently produce a catch-all allow.
    """

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class ToolFilter(StrEnum):
    """Which tool a permission rule applies to.

    Wire form is lowercase **without CamelCase splitting** (serde
    ``rename_all = "lowercase"``): ``Any``→``"any"``, ``WebFetch``→``"webfetch"``.
    Defaults to :attr:`ANY`.
    """

    ANY = "any"
    BASH = "bash"
    EDIT = "edit"
    READ = "read"
    GREP = "grep"
    MCP = "mcp"
    WEB_FETCH = "webfetch"


class PermissionRule(BaseModel):
    """A single permission rule.

    Wire shape (grok serde): ``action`` is **required** (no field-level
    default in Rust, so the JSON/TOML must include it); ``tool`` defaults
    to :attr:`ToolFilter.ANY`, ``pattern_mode`` to :attr:`PatternMode.GLOB`,
    ``pattern`` to ``None``.
    """

    model_config = ConfigDict(populate_by_name=True)

    action: RuleAction
    tool: ToolFilter = ToolFilter.ANY
    pattern: str | None = None
    pattern_mode: PatternMode = PatternMode.GLOB


class PermissionConfig(BaseModel):
    """The ``[permission]`` config section: a list of rules (empty by default).

    Rust has ``#[serde(default)]`` on ``rules`` so an absent section yields
    an empty list; reproduced via ``default_factory=list``.
    """

    model_config = ConfigDict(populate_by_name=True)

    rules: list[PermissionRule] = Field(default_factory=list)

    @classmethod
    def default(cls) -> PermissionConfig:
        """Empty rule list (the all-default ``[permission]`` section)."""
        return cls()
