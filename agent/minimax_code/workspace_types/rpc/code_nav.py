"""Codebase index / code navigation RPCs (R69, Ok side of the envelope).

Fusion of grok's ``xai-grok-workspace-types::rpc::code_nav`` — the five
``workspace.code_*`` request structs plus the two response shapes they
return. These are the first envelope consumers on the **Ok side**:
``Response = CodeNavResponse`` (a ``Vec`` of locations) for the four
navigation methods, ``Response = CodeIndexStatusResponse`` for the index
status probe.

Response types are declared before the request structs so each request's
``Response`` ClassVar can name its response type directly (same ordering
contract as :mod:`minimax_code.workspace_types.rpc.session`).
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import ConfigDict

from minimax_code.workspace_types._wire import WireModel, sort_mappings

__all__ = [
    "CodeNavLocation",
    "CodeNavResponse",
    "CodeIndexStats",
    "CodeIndexStatusResponse",
    "CodeGotoDefinitionReq",
    "CodeGotoReferencesReq",
    "CodeFindDefinitionsReq",
    "CodeFindReferencesReq",
    "CodeIndexStatusReq",
]


class CodeNavLocation(WireModel):
    """One hit in a code-navigation response.

    ``symbol`` carries ``#[serde(skip_serializing_if = "Option::is_none")]``
    in the source — the key is omitted entirely when ``None`` (R67-established
    override pattern; see ``UserQuestionOption.preview``).
    """

    model_config = ConfigDict(populate_by_name=True)

    path: str
    line: int
    symbol: str | None = None

    def to_wire(self) -> dict[str, Any]:
        # skip_serializing_if = "Option::is_none" on `symbol`: omit the key
        # when None rather than emitting ``"symbol": null``.
        return sort_mappings(self.model_dump(mode="json", by_alias=True, exclude_none=True))


class CodeNavResponse(WireModel):
    """Response for the four navigation methods (goto / find definitions & refs)."""

    locations: list[CodeNavLocation]


class CodeIndexStats(WireModel):
    """Aggregate counts for an indexed codebase."""

    files: int
    definitions: int
    references: int


class CodeIndexStatusResponse(WireModel):
    """Response for ``workspace.code_index_status``.

    ``active`` is required; ``file_count`` and ``stats`` are ``Option`` →
    ``int | None`` / ``CodeIndexStats | None`` defaulting to ``None``.
    """

    active: bool
    file_count: int | None = None
    stats: CodeIndexStats | None = None


class CodeGotoDefinitionReq(WireModel):
    """``workspace.code_goto_definition`` — LSP-style goto-definition.

    ``Response = CodeNavResponse``. ``root`` is ``Option<PathBuf>`` with
    ``#[serde(default)]`` → ``str | None`` defaulting to ``None`` (the wire
    form of a path is its OS string, so a plain ``str`` carries it).
    """

    METHOD: ClassVar[str] = "workspace.code_goto_definition"
    Response: ClassVar[type] = CodeNavResponse

    root: str | None = None
    file: str
    line: int
    col: int


class CodeGotoReferencesReq(WireModel):
    """``workspace.code_goto_references`` — find references at a position.

    ``Response = CodeNavResponse``. ``include_definition`` is
    ``#[serde(default)]`` ``bool`` → defaults to ``False``.
    """

    METHOD: ClassVar[str] = "workspace.code_goto_references"
    Response: ClassVar[type] = CodeNavResponse

    root: str | None = None
    file: str
    line: int
    col: int
    include_definition: bool = False


class CodeFindDefinitionsReq(WireModel):
    """``workspace.code_find_definitions`` — symbol-name definition search.

    ``Response = CodeNavResponse``. ``context_file`` is ``Option<String>``.
    """

    METHOD: ClassVar[str] = "workspace.code_find_definitions"
    Response: ClassVar[type] = CodeNavResponse

    root: str | None = None
    symbol: str
    context_file: str | None = None


class CodeFindReferencesReq(WireModel):
    """``workspace.code_find_references`` — symbol-name reference search.

    ``Response = CodeNavResponse``.
    """

    METHOD: ClassVar[str] = "workspace.code_find_references"
    Response: ClassVar[type] = CodeNavResponse

    root: str | None = None
    symbol: str
    context_file: str | None = None


class CodeIndexStatusReq(WireModel):
    """``workspace.code_index_status`` — probe indexer progress.

    ``Response = CodeIndexStatusResponse``. Empty body apart from the optional
    ``root``; derives ``Default`` in the source, so :meth:`default` succeeds.
    """

    METHOD: ClassVar[str] = "workspace.code_index_status"
    Response: ClassVar[type] = CodeIndexStatusResponse

    root: str | None = None
