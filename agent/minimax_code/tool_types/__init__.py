"""Tool schema vocabulary — tool-plane wire types (R65).

Fusion of grok-build's ``xai-tool-types`` crate: the JSON vocabulary for
declaring *tools*, their *arguments*, and the JSON-Schema *argument schemas*
that render tools surface to the model. Pure types + pure logic, zero I/O.

This package is the **tool-plane type contract**, the symmetric counterpart
to R64's management-plane wire DTO (:mod:`minimax_code.extensions`):

* R64 — extension system wire contract (hooks / plugins / MCP / marketplace):
  *how the shell is extended*.
* R65 — tool system schema vocabulary (tool / argument / type-tag / validate):
  *what tools exist*.

Together they close the two "type contract" surfaces a platform shell needs.
Nothing here does I/O — these are the wire types future IPC handlers and the
agent's tool registry will consume and produce.

Package layout
--------------

* :mod:`.types`         — :class:`ArgumentType`, :class:`SchemaType`,
  :class:`ToolArgument`, :class:`ToolDescription`, validation errors.
* :mod:`.schema_parser` — :func:`parse_arguments_from_schema_lossy`, the
  lossy JSON Schema → :class:`ToolArgument` flattener (resolves ``$ref`` /
  ``$defs`` / ``anyOf`` / ``oneOf``).
"""

from __future__ import annotations

from .schema_parser import parse_arguments_from_schema_lossy
from .types import (
    ArgumentType,
    SchemaType,
    ToolArgument,
    ToolDescription,
    ValidationError,
    ValidationErrors,
)

__all__ = [
    "ArgumentType",
    "SchemaType",
    "ToolArgument",
    "ToolDescription",
    "ValidationError",
    "ValidationErrors",
    "parse_arguments_from_schema_lossy",
]
