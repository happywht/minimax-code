"""Tool package — collects the built-in tools.

Importing this module has the side effect of registering every
built-in :class:`Tool` in the default :class:`ToolRegistry`. The
agent loop imports the default registry (``get_default_registry()``)
which already contains file_ops, terminal, edit, and search.
"""

from __future__ import annotations

from .base import Tool, ToolRegistry, ToolResult, get_default_registry, register_tool
from .edit import EditFileTool
from .file_ops import (
    ListDirectoryTool,
    PathSecurityError,
    ReadFileTool,
    WriteFileTool,
    safe_resolve,
)
from .search import SearchFilesTool
from .terminal import ExecCommandTool

__all__ = [
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "get_default_registry",
    "register_tool",
    "EditFileTool",
    "ListDirectoryTool",
    "PathSecurityError",
    "ReadFileTool",
    "WriteFileTool",
    "SearchFilesTool",
    "ExecCommandTool",
    "safe_resolve",
]
