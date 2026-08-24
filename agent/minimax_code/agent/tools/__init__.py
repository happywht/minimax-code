"""Tool package — collects the built-in tools.

Importing this module has the side effect of registering every
built-in :class:`Tool` in the default :class:`ToolRegistry`. The
agent loop imports the default registry (``get_default_registry()``)
which already contains file_ops, terminal, edit, and search.
"""

from __future__ import annotations

from .artifacts import BRIEF_NAME, ReadArtifactTool
from .ask_user import ASK_USER_MARKER, ASK_USER_TIMEOUT_S, AskUserTool
from .base import Tool, ToolRegistry, ToolResult, get_default_registry, register_tool
from .codebase_find_symbol import FindSymbolCodebaseTool
from .codebase_navigate import NavigateCodebaseTool
from .codebase_search import SearchCodebaseTool
from .codebase_summarize import SummarizeCodebaseTool
from .edit import EditFileTool
from .file_ops import (
    ListDirectoryTool,
    PathSecurityError,
    ReadFileTool,
    WriteFileTool,
    safe_resolve,
)
from .glob import GlobFindTool
from .search import SearchFilesTool
from .subagents import (
    CheckSubagentTool,
    ListSubagentsTool,
    SpawnSubagentTool,
    WaitSubagentTool,
)
from .terminal import ExecCommandTool

__all__ = [
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "get_default_registry",
    "register_tool",
    "ASK_USER_MARKER",
    "ASK_USER_TIMEOUT_S",
    "AskUserTool",
    "BRIEF_NAME",
    "CheckSubagentTool",
    "EditFileTool",
    "GlobFindTool",
    "ListDirectoryTool",
    "ListSubagentsTool",
    "NavigateCodebaseTool",
    "PathSecurityError",
    "ReadArtifactTool",
    "ReadFileTool",
    "SearchCodebaseTool",
    "SearchFilesTool",
    "FindSymbolCodebaseTool",
    "SpawnSubagentTool",
    "SummarizeCodebaseTool",
    "WaitSubagentTool",
    "WriteFileTool",
    "ExecCommandTool",
    "safe_resolve",
]
