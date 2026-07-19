"""Backend-agnostic tool search interface (R112).

Fusion of grok-build's ``xai-tool-runtime/src/search.rs``. A tool search
index lets the harness find tools by query (BM25, OpenSearch, in-memory
linear, ...) without depending on any concrete backend. The index lives
behind :class:`ToolIndex` (a thin shared wrapper) so a single snapshot can
be queried from multiple call sites.

This module is a leaf in the tool_runtime barrel: it depends only on the
standard library and touches no other tool_runtime module. Rust's
``Send + Sync`` bounds on :class:`ToolSearchIndex` (which let it live
behind an ``Arc<dyn>`` shared across tasks) have no Python equivalent
under the GIL — :class:`ToolSearchIndex` is a plain
:class:`typing.Protocol` decorated :func:`runtime_checkable` so an
arbitrary backend object can be cheaply type-checked at the seam.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "SearchSnapshot",
    "ServerSummary",
    "ToolIndex",
    "ToolSearchIndex",
    "ToolSearchResult",
]


@dataclass
class ToolSearchResult:
    """A single tool search hit.

    Mirrors ``xai_tool_runtime::ToolSearchResult``. ``score`` is
    backend-defined relevance — comparable within a single snapshot but
    NOT across snapshots (two snapshots may rank identical tools with
    incomparable numbers).
    """

    #: Qualified tool name (e.g. ``"linear__save_issue"``).
    tool_name: str
    #: Origin server name (e.g. ``"linear"``).
    server_name: str
    #: Tool description.
    description: str
    #: Backend-defined relevance score.
    score: float
    #: Parameter names from the tool's input schema, in declaration order.
    parameters: list[str]
    #: Full JSON Schema for the tool's input. Included so callers can
    #: construct dispatched tool calls without a separate schema fetch.
    input_schema: Any


@dataclass
class SearchSnapshot:
    """Snapshot of a search query — results plus index metadata.

    All fields are captured from the same point-in-time view of the index,
    so the caller can render an accurate "N results out of M" line without
    a second call.
    """

    #: Ranked search hits.
    results: list[ToolSearchResult]
    #: Number of indexed tools that did not appear in ``results``.
    total_hidden_tools: int
    #: ``True`` when the index reflects all available tools; ``False``
    #: while the index source is still warming up.
    is_ready: bool


@dataclass
class ServerSummary:
    """Summary of an MCP server (or other tool source) available for search."""

    #: Server name (e.g. ``"linear"``, ``"slack"``).
    name: str
    #: Optional short description of the server's surface area.
    description: str | None
    #: Unqualified tool names, sorted alphabetically. Use
    #: :meth:`tool_count` for a count without indirection.
    tool_names: list[str]

    def tool_count(self) -> int:
        """Number of tools the server exposes."""
        return len(self.tool_names)


@runtime_checkable
class ToolSearchIndex(Protocol):
    """Backend-agnostic search interface.

    Implementations correspond to Rust ``impl ToolSearchIndex: Send + Sync``
    (BM25, OpenSearch, in-memory linear, ...). The ``Send + Sync`` bounds
    (which let the trait live behind an ``Arc<dyn>`` shared across
    concurrent tasks) have no Python equivalent under the GIL.

    :func:`runtime_checkable` lets an arbitrary backend object be
    ``isinstance``-checked against this Protocol at the seam — a structural
    duck-type match, not subclass registration.
    """

    def search_snapshot(self, query: str, limit: int) -> SearchSnapshot:
        """Run a query against a single consistent index snapshot.

        Returning the metadata alongside the results lets the caller
        render an accurate "N results out of M" line without a second call.
        """
        ...

    def list_server_summaries(self) -> list[ServerSummary]:
        """Enumerate the unique servers in the index.

        Used to render the system-reminder listing connected integrations.
        """
        ...


@dataclass(eq=False)
class ToolIndex:
    """Shared wrapper around a :class:`ToolSearchIndex`.

    Mirrors ``xai_tool_runtime::ToolIndex(pub Arc<dyn ToolSearchIndex>)``.
    Rust wraps the dyn trait in an ``Arc`` for shared ownership across
    tasks; Python objects are already reference-shared, so this is a thin
    holder that lets a search index live in a resource map under a stable
    type.

    The Rust type derives only ``Clone`` and adds a custom ``Debug`` impl
    that deliberately does NOT expand the dyn trait (it has no ``Debug``
    bound) — :meth:`__repr__` matches that opaqueness, and ``eq=False``
    mirrors the absence of ``PartialEq`` (Rust ``ToolIndex`` cannot be
    ``==``-compared). Identity comparison (``is``) remains available via
    the default ``object`` semantics.
    """

    #: The wrapped search index.
    index: ToolSearchIndex

    def __repr__(self) -> str:
        # Match Rust's `f.debug_struct("ToolIndex").finish()` — opaque.
        return "ToolIndex(...)"
