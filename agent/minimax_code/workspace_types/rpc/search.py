"""Content-search + fuzzy file-search RPCs (R70).

Fusion of grok's ``xai-grok-workspace-types::rpc::search`` — the
``workspace.ripgrep`` content-search request plus the four ``workspace.fuzzy_*``
file-search requests. This file is a **mixed-namespace** migration and lands
four serde patterns new to the RPC layer:

* **camelCase wire keys** — ``#[serde(rename_all = "camelCase")]`` becomes
  ``ConfigDict(alias_generator=to_camel, populate_by_name=True)`` (the content-
  search structs + :class:`ClientId`). The fuzzy half keeps snake_case (no
  ``rename_all`` in the source), so its field names are emitted verbatim while
  the nested :class:`ClientId` value inside :class:`TargetClientId` is still
  camelCase.
* **untagged enum** — ``#[serde(untagged)] enum TargetClientId`` (``null`` →
  ``None`` variant, ``{instanceId, connId}`` → ``ClientId`` variant) becomes a
  :class:`pydantic.RootModel` over ``ClientId | None`` with an ``is_none()``
  method; the default is the ``None`` variant (``#[default]`` in the source).
* **custom default fn** — ``#[serde(default = "default_respect_gitignore")]``
  (``respect_gitignore`` defaults to ``True`` while its sibling bools default to
  ``False``) becomes a plain ``= True`` field default.
* **primitive / arbitrary-JSON responses** — ``type Response = String / bool /
  serde_json::Value`` become ``Response: ClassVar = str / bool / Any``.

A fifth, subtler pattern: :class:`ContentMatch`'s ``match_start`` / ``match_end``
carry ``#[serde(default, skip_serializing_if = "Option::is_none")]`` and are
serialised **nested** inside ``ContentSearchData.files[].matches[]``. R67's
``to_wire`` override only covered top-level serialisation, so :class:`ContentMatch`
uses a plain :func:`pydantic.model_serializer` instead — the skip then applies at
every nesting depth.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, ClassVar

from pydantic import ConfigDict, Field, RootModel, model_serializer
from pydantic.alias_generators import to_camel

from minimax_code.workspace_types._wire import WireModel, sort_mappings

__all__ = [
    "ClientId",
    "TargetClientId",
    "ContentMatch",
    "ContentMatchFile",
    "ContentSearchData",
    "ContentSearchRequest",
    "FuzzyOpenReq",
    "FuzzyChangeReq",
    "FuzzyCloseReq",
    "FuzzyStatusReq",
]

#: camelCase wire config shared by the content-search structs and ``ClientId``.
#: ``to_camel`` mirrors ``#[serde(rename_all = "camelCase")]``; combined with
#: ``WireModel.to_wire``'s ``by_alias=True`` dump, field names serialise as
#: camelCase while still validating under their snake_case names.
_CAMEL = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class ClientId(WireModel):
    """Client-routing identity (relay instance + connection). camelCase on wire.

    Duplicated here for Phase-1 independence from the shell-extension crate.
    """

    model_config = _CAMEL

    instance_id: str
    conn_id: str


class TargetClientId(RootModel[ClientId | None]):
    """Untagged enum mirroring ``#[serde(untagged)] enum TargetClientId``.

    Wire forms: ``null`` → the ``None`` variant, ``{instanceId, connId}`` → the
    ``ClientId`` variant. The default is the ``None`` variant (``#[default]`` in
    the source). Implemented as a :class:`RootModel` over ``ClientId | None``
    because Python has no first-class untagged enum; the :meth:`is_none` method
    preserves the source's ``TargetClientId::is_none`` API.
    """

    root: ClientId | None = None

    @classmethod
    def none(cls) -> TargetClientId:
        """The ``None`` variant — wire ``null`` (the ``#[default]``)."""
        return cls(None)

    def is_none(self) -> bool:
        """Whether this is the ``None`` variant (mirrors ``TargetClientId::is_none``)."""
        return self.root is None

    def to_wire(self) -> Any:
        """``null`` for the ``None`` variant, else the camelCase ``ClientId`` dict."""
        # ``RootModel.model_dump`` returns the root value directly (None or the
        # ClientId dict); sort_mappings passes None through untouched.
        return sort_mappings(self.model_dump(mode="json", by_alias=True))


class ContentMatch(WireModel):
    """One matched line in a content-search hit. camelCase on wire.

    ``match_start`` / ``match_end`` carry ``#[serde(default,
    skip_serializing_if = "Option::is_none")]`` — omitted when ``None`` at any
    nesting depth. A plain :func:`model_serializer` hand-builds the wire dict so
    the skip applies whether the match is serialised at the top level or nested
    inside ``ContentMatchFile.matches`` (R67's ``to_wire`` override only covered
    the top-level path).
    """

    model_config = _CAMEL

    line: int
    content: str
    match_start: int | None = None
    match_end: int | None = None

    @model_serializer
    def _to_wire_dict(self) -> dict[str, Any]:
        # Hand-build camelCase keys, dropping the two optional span fields when
        # None — reproducing #[serde(skip_serializing_if = "Option::is_none")]
        # at every nesting depth (sort_mappings re-sorts keys in to_wire).
        out: dict[str, Any] = {"line": self.line, "content": self.content}
        if self.match_start is not None:
            out["matchStart"] = self.match_start
        if self.match_end is not None:
            out["matchEnd"] = self.match_end
        return out


class ContentMatchFile(WireModel):
    """A file with its content-search matches. camelCase on wire."""

    model_config = _CAMEL

    name: str
    path: str
    matches: list[ContentMatch]

    @classmethod
    def new(cls, path: str) -> ContentMatchFile:
        """Construct from a path, deriving ``name`` via ``Path::file_name()``.

        Mirrors ``ContentMatchFile::new``: ``name`` is the final path component
        (``PurePosixPath(path).name``), falling back to the whole path when the
        path has no file-name component (``.`` / ``..`` / trailing-slash /
        empty) — matching ``Path::file_name`` returning ``None`` for those.
        """
        name = PurePosixPath(path).name
        if name in ("", ".", ".."):
            name = path
        return cls(name=name, path=path, matches=[])


class ContentSearchData(WireModel):
    """Response for ``workspace.ripgrep``. camelCase on wire; derives Default."""

    model_config = _CAMEL

    files: list[ContentMatchFile]
    total_matches: int
    total_files: int
    truncated: bool


class ContentSearchRequest(WireModel):
    """``workspace.ripgrep`` — ripgrep-powered content search.

    camelCase on wire. ``respect_gitignore`` defaults to ``True``
    (``#[serde(default = "default_respect_gitignore")]``); every other bool /
    ``Option`` defaults to ``False`` / ``None`` (plain ``#[serde(default)]``).
    ``Response = ContentSearchData``.
    """

    model_config = _CAMEL

    METHOD: ClassVar[str] = "workspace.ripgrep"
    Response: ClassVar[type] = ContentSearchData

    pattern: str
    case_insensitive: bool = False
    whole_word: bool = False
    is_regex: bool = False
    include_globs: list[str] = Field(default_factory=list)
    exclude_globs: list[str] = Field(default_factory=list)
    max_files: int | None = None
    max_matches: int | None = None
    respect_gitignore: bool = True
    cwd: str | None = None
    context_id: str | None = None

    @classmethod
    def default(cls) -> ContentSearchRequest:
        # `#[derive(Default)]`: the required `pattern: String` defaults to "".
        return cls(pattern="")


class FuzzyOpenReq(WireModel):
    """``workspace.fuzzy_open`` — open a fuzzy file-search session.

    snake_case on wire (no ``rename_all`` in the source). ``root`` is
    ``Option<PathBuf>``. ``target_client_id`` is ``#[serde(default)]``
    ``TargetClientId`` → defaults to the ``None`` variant. ``Response = String``
    (the search id).
    """

    METHOD: ClassVar[str] = "workspace.fuzzy_open"
    Response: ClassVar[type] = str

    root: str | None = None
    request_id: str | None = None
    hidden: bool = False
    session_id: str | None = None
    target_client_id: TargetClientId = Field(default_factory=TargetClientId.none)


class FuzzyChangeReq(WireModel):
    """``workspace.fuzzy_change`` — update a fuzzy search's query.

    snake_case. ``Response = bool`` (whether the search existed, so the shell
    can return "not found").
    """

    METHOD: ClassVar[str] = "workspace.fuzzy_change"
    Response: ClassVar[type] = bool

    search_id: str
    query: str
    dirs_only: bool = False
    limit: int | None = None


class FuzzyCloseReq(WireModel):
    """``workspace.fuzzy_close`` — close a fuzzy search session. ``Response = bool``."""

    METHOD: ClassVar[str] = "workspace.fuzzy_close"
    Response: ClassVar[type] = bool

    search_id: str


class FuzzyStatusReq(WireModel):
    """``workspace.fuzzy_search`` — poll a fuzzy search's results.

    ``Response = serde_json::Value`` (arbitrary JSON; ``null`` when the search
    no longer exists) → ``Response: ClassVar = Any``. Derives ``Default`` in the
    source, so :meth:`default` succeeds (the required ``search_id`` → ``""``).
    """

    METHOD: ClassVar[str] = "workspace.fuzzy_search"
    Response: ClassVar = Any  # serde_json::Value — arbitrary JSON

    search_id: str

    @classmethod
    def default(cls) -> FuzzyStatusReq:
        # `#[derive(Default)]`: the required `search_id: String` defaults to "".
        return cls(search_id="")
