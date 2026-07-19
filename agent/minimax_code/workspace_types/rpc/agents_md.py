"""Discovery RPC (R68).

Fusion of grok's ``xai-grok-workspace-types::rpc::agents_md`` — the
``workspace.discover_agents_md`` request and the ``AgentConfigFile`` shape
it returns.

``AgentConfigFile`` mirrors ``xai-grok-agent``'s struct; pydantic ignores
unknown fields by default (``extra="ignore"``), matching serde's
forward-compatibility for config files (a future field does not break
parsing — verified by the source ``agent_config_file_ignores_unknown_fields``
test).
"""

from __future__ import annotations

from typing import ClassVar

from minimax_code.workspace_types._wire import WireModel

__all__ = ["AgentConfigFile", "DiscoverAgentsMdReq"]


class AgentConfigFile(WireModel):
    """One discovered project-instruction file (AGENTS.md / Claude.md / …)."""

    file_name: str
    file_path: str
    content: str


class DiscoverAgentsMdReq(WireModel):
    """``workspace.discover_agents_md`` — discover project-instruction files.

    The empty-request body mirrors Rust's ``DiscoverAgentsMdReq {}`` (which
    derives ``Default`` — all fields absent, so :meth:`default` succeeds).
    ``Response = Vec<AgentConfigFile>``.
    """

    METHOD: ClassVar[str] = "workspace.discover_agents_md"
    Response: ClassVar[type] = list[AgentConfigFile]
