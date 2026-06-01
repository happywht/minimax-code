"""Agent core — conversation loop, LLM client, prompts.

Public surface
--------------

* :class:`~.core.AgentCore` — drives a single conversation turn.
* :class:`~.core.AgentConfig` — runtime tunables.
* :class:`~.llm.MiniMaxClient` — async LLM client.
* :class:`~.llm.StreamChunk`, :class:`~.llm.LLMResponse` — wire types.
* :class:`~.tools.ToolRegistry`, :class:`~.tools.Tool`,
  :class:`~.tools.ToolResult` — tool abstraction.
* :func:`~.prompts.build_system_prompt` — system prompt assembly.

Importing this package alone is a no-op — the built-in tools
self-register as a side effect of importing
:mod:`minimax_code.agent.tools`. The agent loop
(:class:`AgentCore`) takes care of triggering that import.
"""

from __future__ import annotations

# Re-export public surface.
from . import tools  # noqa: F401  — side-effect import: registers built-in tools
from .core import (
    AgentConfig,
    AgentCore,
    AgentRunResult,
    ChunkCallback,
    StatusCallback,
    ToolCallCallback,
    ToolResultCallback,
    UsageCallback,
)
from .llm import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    LLMConfigError,
    LLMError,
    LLMResponse,
    MiniMaxClient,
    StreamChunk,
)
from .prompts import DEFAULT_SYSTEM_PROMPT, build_system_prompt
from .tools import (
    EditFileTool,
    ExecCommandTool,
    ListDirectoryTool,
    ReadFileTool,
    SearchFilesTool,
    Tool,
    ToolRegistry,
    ToolResult,
    WriteFileTool,
    get_default_registry,
)

__all__ = [
    # core
    "AgentConfig",
    "AgentCore",
    "AgentRunResult",
    "ChunkCallback",
    "StatusCallback",
    "ToolCallCallback",
    "ToolResultCallback",
    "UsageCallback",
    # llm
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "LLMConfigError",
    "LLMError",
    "LLMResponse",
    "MiniMaxClient",
    "StreamChunk",
    # prompts
    "DEFAULT_SYSTEM_PROMPT",
    "build_system_prompt",
    # tools
    "EditFileTool",
    "ExecCommandTool",
    "ListDirectoryTool",
    "ReadFileTool",
    "SearchFilesTool",
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "WriteFileTool",
    "get_default_registry",
    "tools",
]
