"""The unified Tool contract + streaming primitives (R109).

Fusion of grok-build's ``xai-tool-runtime::tool`` — both the contract
layer tool authors code against (the ``Tool`` / ``ToolDyn`` /
``ToolFamily`` protocols) and the runtime primitives the dispatcher drives
(``ToolStream`` / ``ToolStreamItem`` / ``ToolProgress`` / ``ContentBlock``
/ ``terminal_only`` / ``with_progress`` / ``TypedToolOutput``).

Why tool lands third
--------------------

R107 landed the error leaf (:class:`~minimax_code.tool_runtime.error.ToolError`);
R108 landed the context leaf
(:class:`~minimax_code.tool_runtime.context.ToolCallContext` /
:class:`~minimax_code.tool_runtime.context.ListToolsContext`). ``tool`` is
the third leaf and depends on both: ``Tool::execute`` threads a
:class:`ToolCallContext` and yields ``Result<T, ToolError>`` terminal
items; ``should_list`` takes a :class:`ListToolsContext`. It also depends
on the upstream protocol types :class:`ToolId` / :class:`ToolCapabilities`
(:mod:`minimax_code.tool_protocol`, R82 / R86) and
:class:`~minimax_code.tool_types.ToolDescription` (:mod:`tool_types`, R65).

The render seam (a two-round split)
------------------------------------

``tool.rs`` and ``render.rs`` form a mutually-referencing pair in the
crate: ``tool.rs`` imports ``ToolChatCompletionResponse`` / ``ToolOutput``
from ``render.rs``, and ``render.rs`` imports ``ContentBlock`` from
``tool.rs``. Rust permits intra-crate cycles; Python does not permit
circular top-level module imports. The resolution splits the pair across
two rounds:

* **R109 (this round)** — ``tool.py`` does NOT import ``render`` at
  runtime. Every render reference is an annotation string under PEP 563
  (``from __future__ import annotations`` defers all annotations), except
  the single runtime touchpoint: :meth:`TypedToolOutput.from_value` calls
  ``extract_content_blocks`` via a **function-local lazy import**, so it
  resolves only when ``from_value`` runs (and only after ``render.py``
  lands in R110).
* **R110 (next round)** — ``render.py`` imports :class:`ContentBlock`
  from ``tool.py`` at module level. The runtime dependency edge is then
  one-way (``render -> tool``); no cycle.

Protocol shape
--------------

The crate's three trait objects — ``Tool``, ``ToolDyn``, ``ToolFamily`` —
land as :class:`typing.Protocol` classes. Rust's associated types
(``type Args``, ``type Output``) and trait default bodies have no direct
Python equivalent: a :class:`Protocol` carries no state and no default
implementations, so the associated types are documented concepts and the
default-method behaviour (``capabilities`` -> ``ToolCapabilities::default()``;
``has_dynamic_description`` -> ``False``; ``should_list`` -> ``True``;
``execute`` delegating to ``run``) is documented per-method. The
:func:`default_capabilities` helper gives tool authors the Rust default
without repeating the field list. The blanket ``impl<T: Tool> ToolDyn for
T`` (parse args from ``Value``, drive the typed execute, realise a
:class:`TypedToolOutput`) is partially migrated as the output->
:class:`TypedToolOutput` step realised in
:meth:`TypedToolOutput.from_value`; the full async stream-mapping (drive
``Tool::execute`` and re-tag each item) lands with
:mod:`minimax_code.tool_runtime.dispatch` (YAGNI until a caller drives it).

Serde shapes
------------

:class:`ToolProgress` and :class:`ContentBlock` are Rust
``#[serde(tag = "kind")]`` / ``#[serde(tag = "type")]`` internally-tagged
enums. They land as single dataclasses with a discriminator field plus
``Text`` / ``Content`` / ``Custom`` (resp. ``Text`` / ``Image`` /
``Resource``) constructor class methods and ``to_dict`` / ``from_dict``
round-trippers matching the snake_case tags. :meth:`ContentBlock.Image`
accepts both ``mime_type`` and the ``mimeType`` alias on decode (Rust
``#[serde(alias = "mimeType")]``). :class:`ToolStreamItem` is
externally-tagged ``Progress`` / ``Terminal``; it lands the same
discriminator + constructor way.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeAlias, TypeVar

from minimax_code.tool_protocol.capabilities import ToolCapabilities
from minimax_code.tool_protocol.ids import ToolId
from minimax_code.tool_runtime.context import ListToolsContext, ToolCallContext
from minimax_code.tool_runtime.error import ToolError
from minimax_code.tool_types import ToolDescription

if TYPE_CHECKING:
    # render.rs lands in R110. tool.py never imports render at runtime:
    # every reference below is an annotation string (deferred by PEP 563),
    # and the one runtime touchpoint (TypedToolOutput.from_value ->
    # extract_content_blocks) is a function-local lazy import. Kept under
    # TYPE_CHECKING so static checkers resolve the name without creating a
    # top-level import cycle. ``ToolOutput`` itself is not named here: the
    # TypedToolOutput fields ARE the trait impl, so no annotation names it
    # (its role is documented in TypedToolOutput's docstring instead).
    from minimax_code.tool_runtime.render import ToolChatCompletionResponse

__all__ = [
    "ArcTool",
    "ArcToolFamily",
    "ContentBlock",
    "Tool",
    "ToolDyn",
    "ToolFamily",
    "ToolProgress",
    "ToolStream",
    "ToolStreamItem",
    "ToolVariant",
    "TypedToolOutput",
    "terminal_only",
    "with_progress",
]

#: Element type of a typed tool's output stream (Rust ``type Args`` /
#: ``type Output`` associated types — documented concept only).
T = TypeVar("T")


# -----------------------------------------------------------------------
# ContentBlock — a single model-facing content piece (text/image/resource).
# -----------------------------------------------------------------------


@dataclass
class ContentBlock:
    """A single content piece a tool emits for the model (Rust ``ContentBlock``).

    Rust ``#[serde(tag = "type", rename_all = "snake_case")]`` internally-
    tagged enum with three variants — ``Text``, ``Image``, ``Resource``.
    Lands as one dataclass with a ``type`` discriminator and constructor
    class methods; :meth:`to_dict` / :meth:`from_dict` reproduce the
    snake_case tags and the ``mimeType`` alias on ``Image`` / ``Resource``.
    """

    #: Discriminator — ``"text"`` / ``"image"`` / ``"resource"``
    #: (Rust ``#[serde(tag = "type")]``).
    type: str
    #: ``Text`` variant payload.
    text: str | None = None
    #: ``Image`` / ``Resource`` MIME type (Rust alias ``mimeType`` on decode).
    mime_type: str | None = None
    #: ``Image`` variant base64 payload.
    data: str | None = None
    #: ``Image`` optional media id (pre-uploaded asset reference).
    media_id: str | None = None
    #: ``Image`` optional filename.
    filename: str | None = None
    #: ``Image`` optional filesystem path.
    path: str | None = None
    #: ``Image`` optional string-to-string metadata.
    metadata: dict[str, str] = field(default_factory=dict)
    #: ``Resource`` variant uri.
    uri: str | None = None

    @classmethod
    def Text(cls, text: str) -> ContentBlock:
        """``Text { text }`` variant constructor."""
        return cls(type="text", text=text)

    @classmethod
    def Image(
        cls,
        *,
        mime_type: str,
        data: str,
        media_id: str | None = None,
        filename: str | None = None,
        path: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> ContentBlock:
        """``Image { mime_type, data, media_id?, filename?, path?, metadata }`` variant."""
        return cls(
            type="image",
            mime_type=mime_type,
            data=data,
            media_id=media_id,
            filename=filename,
            path=path,
            metadata=dict(metadata) if metadata else {},
        )

    @classmethod
    def Resource(
        cls,
        *,
        uri: str,
        mime_type: str | None = None,
        text: str | None = None,
    ) -> ContentBlock:
        """``Resource { uri, mime_type?, text? }`` variant constructor."""
        return cls(type="resource", uri=uri, mime_type=mime_type, text=text)

    def to_dict(self) -> dict[str, Any]:
        """Serialise with the crate's internally-tagged snake_case shape.

        Reproduces Rust ``#[serde(tag = "type", rename_all = "snake_case")]``:
        each variant emits its discriminator plus its fields. ``Image``
        omits the optional fields when ``None`` and ``metadata`` when empty;
        ``Resource`` omits ``mime_type`` / ``text`` when ``None``.
        """
        if self.type == "text":
            return {"type": "text", "text": self.text}
        if self.type == "image":
            out: dict[str, Any] = {
                "type": "image",
                "mime_type": self.mime_type,
                "data": self.data,
            }
            if self.media_id is not None:
                out["media_id"] = self.media_id
            if self.filename is not None:
                out["filename"] = self.filename
            if self.path is not None:
                out["path"] = self.path
            if self.metadata:
                out["metadata"] = dict(self.metadata)
            return out
        if self.type == "resource":
            out = {"type": "resource", "uri": self.uri}
            if self.mime_type is not None:
                out["mime_type"] = self.mime_type
            if self.text is not None:
                out["text"] = self.text
            return out
        raise ValueError(f"unknown ContentBlock type: {self.type!r}")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContentBlock:
        """Deserialise, accepting the ``mimeType`` alias on ``Image``/``Resource``.

        Mirrors Rust ``#[serde(alias = "mimeType")]``: the MIME-type field
        reads ``mime_type`` first, then the camelCase ``mimeType`` alias.
        """
        type_ = data.get("type")
        if type_ == "text":
            return cls.Text(str(data.get("text", "")))
        if type_ == "image":
            metadata_raw = data.get("metadata")
            return cls.Image(
                mime_type=str(
                    data.get("mime_type") or data.get("mimeType") or ""
                ),
                data=str(data.get("data", "")),
                media_id=data.get("media_id"),
                filename=data.get("filename"),
                path=data.get("path"),
                metadata=(
                    dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
                ),
            )
        if type_ == "resource":
            return cls.Resource(
                uri=str(data.get("uri", "")),
                mime_type=data.get("mime_type") or data.get("mimeType"),
                text=data.get("text"),
            )
        raise ValueError(f"unknown ContentBlock type: {type_!r}")


# -----------------------------------------------------------------------
# ToolProgress — a non-terminal progress item emitted mid-stream.
# -----------------------------------------------------------------------


@dataclass
class ToolProgress:
    """A non-terminal progress item a tool streams before its terminal item.

    Rust ``#[serde(tag = "kind", rename_all = "snake_case")]`` internally-
    tagged enum — ``Text { text }``, ``Content { blocks }``,
    ``Custom { subkind, payload }``. Lands as one dataclass with a ``kind``
    discriminator and constructor class methods. ``Custom.subkind`` is the
    snake_case discriminator the runtime dispatches on (e.g.
    ``"bash_output_chunk"``); ``payload`` is opaque
    (:class:`serde_json::Value` -> ``Any``).
    """

    #: Discriminator — ``"text"`` / ``"content"`` / ``"custom"``
    #: (Rust ``#[serde(tag = "kind")]``).
    kind: str
    #: ``Text`` variant payload.
    text: str | None = None
    #: ``Content`` variant payload (one or more :class:`ContentBlock`).
    blocks: list[ContentBlock] | None = None
    #: ``Custom`` variant snake_case discriminator.
    subkind: str | None = None
    #: ``Custom`` variant opaque payload (``serde_json::Value``).
    payload: Any = None

    @classmethod
    def Text(cls, text: str) -> ToolProgress:
        """``Text { text }`` variant constructor."""
        return cls(kind="text", text=text)

    @classmethod
    def Content(cls, blocks: list[ContentBlock]) -> ToolProgress:
        """``Content { blocks }`` variant constructor."""
        return cls(kind="content", blocks=list(blocks))

    @classmethod
    def Custom(cls, subkind: str, payload: Any) -> ToolProgress:
        """``Custom { subkind, payload }`` variant constructor."""
        return cls(kind="custom", subkind=subkind, payload=payload)

    def to_dict(self) -> dict[str, Any]:
        """Serialise with the crate's internally-tagged snake_case shape."""
        if self.kind == "text":
            return {"kind": "text", "text": self.text}
        if self.kind == "content":
            return {
                "kind": "content",
                "blocks": [b.to_dict() for b in self.blocks or []],
            }
        if self.kind == "custom":
            return {
                "kind": "custom",
                "subkind": self.subkind,
                "payload": self.payload,
            }
        raise ValueError(f"unknown ToolProgress kind: {self.kind!r}")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolProgress:
        """Deserialise (mirrors the ``#[serde(tag = "kind")]`` shape)."""
        kind = data.get("kind")
        if kind == "text":
            return cls.Text(str(data.get("text", "")))
        if kind == "content":
            blocks_raw = data.get("blocks") or []
            return cls.Content(
                [ContentBlock.from_dict(b) for b in blocks_raw]
            )
        if kind == "custom":
            return cls.Custom(
                str(data.get("subkind", "")),
                data.get("payload"),
            )
        raise ValueError(f"unknown ToolProgress kind: {kind!r}")


# -----------------------------------------------------------------------
# ToolStreamItem — one item of a ToolStream (Progress | Terminal).
# -----------------------------------------------------------------------


@dataclass
class ToolStreamItem(Generic[T]):
    """One item yielded by a :type:`ToolStream` (Rust ``ToolStreamItem<T>``).

    Rust ``enum ToolStreamItem<T> { Progress(ToolProgress), Terminal(Result<T,
    ToolError>) }``. Lands as one dataclass with a ``kind`` discriminator
    plus :meth:`Progress` / :meth:`Terminal` constructor class methods. A
    ``Terminal`` item carries ``T | ToolError`` in :attr:`terminal`;
    :meth:`is_error` distinguishes an ``Err`` (a :class:`ToolError`) from
    an ``Ok`` (the typed output ``T``).
    """

    #: Discriminator — ``"progress"`` / ``"terminal"``.
    kind: str
    #: ``Progress`` variant payload (``None`` for ``Terminal``).
    progress: ToolProgress | None = None
    #: ``Terminal`` variant payload — ``T`` on ``Ok``, :class:`ToolError` on
    #: ``Err`` (``None`` for ``Progress``).
    terminal: Any = None

    @classmethod
    def Progress(cls, progress: ToolProgress) -> ToolStreamItem[Any]:
        """``Progress(ToolProgress)`` variant constructor."""
        return cls(kind="progress", progress=progress)

    @classmethod
    def Terminal(cls, result: Any) -> ToolStreamItem[Any]:
        """``Terminal(Result<T, ToolError>)`` variant constructor.

        ``result`` is ``T`` on success or :class:`ToolError` on failure;
        the caller picks which to pass.
        """
        return cls(kind="terminal", terminal=result)

    def is_terminal(self) -> bool:
        """Whether this is the ``Terminal`` variant (Rust ``is_terminal``)."""
        return self.kind == "terminal"

    def is_error(self) -> bool:
        """Whether this is a ``Terminal`` carrying an ``Err`` (:class:`ToolError`)."""
        return self.kind == "terminal" and isinstance(self.terminal, ToolError)


#: A tool's output stream — an async iterator of :class:`ToolStreamItem`.
#:
#: Rust ``type ToolStream<T> = Pin<Box<dyn Stream<Item = ToolStreamItem<T>>
#: + Send>>``; Python's natural equivalent is an async iterator. The
#: ``T`` parameter tracks the terminal item's success type.
ToolStream: TypeAlias = AsyncIterator[ToolStreamItem[T]]


# -----------------------------------------------------------------------
# terminal_only / with_progress — ToolStream constructors.
# -----------------------------------------------------------------------


async def terminal_only(result: Any) -> AsyncIterator[ToolStreamItem[Any]]:
    """Yield a single ``Terminal`` item, then end (Rust ``terminal_only``).

    Rust signature: ``pub fn terminal_only<T: Send + 'static>(result:
    Result<T, ToolError>) -> ToolStream<T>``. ``result`` is ``T |
    ToolError`` in Python — the success value or the error.

    Implemented as an async generator so ``async for item in
    terminal_only(value)`` yields exactly one ``Terminal`` item.
    """
    yield ToolStreamItem.Terminal(result)


async def with_progress(
    progress: ToolProgress,
    terminal: Awaitable[Any],
) -> AsyncIterator[ToolStreamItem[Any]]:
    """Yield one ``Progress``, await ``terminal``, then yield ``Terminal``.

    Rust signature: ``pub fn with_progress<T, P, F>(progress: P, terminal:
    F) -> ToolStream<T>`` where ``P: Into<ToolProgress> + Send`` and
    ``F: Future<Output = Result<T, ToolError>> + Send``. The Python
    equivalent takes the progress item and an awaitable that resolves to
    ``T | ToolError``.
    """
    yield ToolStreamItem.Progress(progress)
    result = await terminal
    yield ToolStreamItem.Terminal(result)


# -----------------------------------------------------------------------
# default_capabilities — the Rust Tool::capabilities() default.
# -----------------------------------------------------------------------


def default_capabilities() -> ToolCapabilities:
    """The all-off :class:`ToolCapabilities` every tool gets by default.

    Rust ``Tool::capabilities`` has a default body returning
    ``ToolCapabilities::default()`` (``#[derive(Default)]``). Because a
    :class:`Protocol` method has no default body, tool authors call this
    helper from their ``capabilities`` override to reproduce the Rust
    default without repeating the field list. ``ToolCapabilities`` is a
    dataclass whose every field defaults, so ``ToolCapabilities()`` IS
    ``ToolCapabilities::default()``.
    """
    return ToolCapabilities()


# -----------------------------------------------------------------------
# TypedToolOutput — the type-erased bundle the dispatcher hands downstream.
# -----------------------------------------------------------------------


@dataclass
class TypedToolOutput:
    """Type-erased bundle the dispatcher hands the render/telemetry layers.

    Fusion of ``TypedToolOutput``. A concrete ``Tool::Output`` never
    reaches the dispatcher directly: the blanket ``impl<T: Tool> ToolDyn
    for T`` serialises it to a :class:`serde_json::Value`, derives
    :attr:`model_output` via :func:`extract_content_blocks`, optionally
    attaches a chat-completion card, and tags it with the tool's id. The
    result is one concrete type the downstream layers treat uniformly.

    The two fields :attr:`model_output` / :attr:`chat_completion_output`
    ARE the crate's ``ToolOutput`` trait implementations for this type
    (the trait methods ``model_output()`` / ``chat_completion_output()``
    return these fields' values). Python fields subsume the trait-method
    bodies — no separate accessor methods are needed (a field IS a
    readable attribute).
    """

    #: The tool that produced this output.
    tool_id: ToolId
    #: The serialised typed output (``serde_json::Value``).
    value: Any
    #: Content blocks extracted from :attr:`value` for the model. Filled by
    #: :meth:`from_value` via :func:`extract_content_blocks`; empty signals
    #: "use auto-extraction" downstream. This field IS the ``ToolOutput``
    #: trait's ``model_output()`` impl for ``TypedToolOutput``.
    model_output: list[ContentBlock] = field(default_factory=list)
    #: Optional chat-completion card. ``None`` = no card. This field IS the
    #: ``ToolOutput`` trait's ``chat_completion_output()`` impl.
    chat_completion_output: ToolChatCompletionResponse | None = None

    @classmethod
    def from_value(cls, tool_id: ToolId, value: Any) -> TypedToolOutput:
        """Build from a serialised output value (Rust ``TypedToolOutput::from_value``).

        Derives :attr:`model_output` by calling :func:`extract_content_blocks`
        on ``value``. The import is function-local and lazy: ``render.py``
        lands in R110, and this is the single runtime touchpoint across the
        ``tool``/``render`` seam. Until R110, callers that exercise this
        method must supply ``extract_content_blocks`` (the test suite injects
        a fake ``render`` module).
        """
        from minimax_code.tool_runtime.render import extract_content_blocks

        return cls(
            tool_id=tool_id,
            value=value,
            model_output=extract_content_blocks(value),
        )

    def with_chat_completion_output(
        self,
        cco: ToolChatCompletionResponse | None,
    ) -> TypedToolOutput:
        """Attach a chat-completion card (builder; returns self)."""
        self.chat_completion_output = cco
        return self


# -----------------------------------------------------------------------
# ToolVariant — Default or named variant selector.
# -----------------------------------------------------------------------


@dataclass
class ToolVariant:
    """Default or named variant selector (Rust ``enum ToolVariant``).

    Rust ``enum ToolVariant { Default, Variant(String) }``. Lands as one
    dataclass with a ``kind`` discriminator plus :meth:`Default` /
    :meth:`Variant` constructor class methods.
    """

    #: Discriminator — ``"default"`` / ``"variant"``.
    kind: str
    #: ``Variant`` payload (the variant name); ``None`` for ``Default``.
    name: str | None = None

    @classmethod
    def Default(cls) -> ToolVariant:
        """``Default`` variant constructor."""
        return cls(kind="default")

    @classmethod
    def Variant(cls, name: str) -> ToolVariant:
        """``Variant(String)`` variant constructor."""
        return cls(kind="variant", name=name)

    def is_default(self) -> bool:
        """Whether this is the ``Default`` variant."""
        return self.kind == "default"


# -----------------------------------------------------------------------
# Tool — the typed contract a tool author codes against.
# -----------------------------------------------------------------------


class Tool(Protocol):
    """The typed contract a tool author codes against (Rust ``trait Tool``).

    Rust's ``trait Tool: Send + Sync`` has two associated types
    (``type Args: Deserialize + JsonSchema + Send`` and ``type Output:
    Serialize + ToolOutput + Send``) and seven methods, four of which
    carry default bodies. A Python :class:`Protocol` has no associated
    types and no default bodies, so:

    * **Associated types** are documented concepts only. ``Args`` is the
      typed argument struct the tool deserialises from the inbound
      ``Value``; ``Output`` is the typed return value it serialises. Both
      surface as ``Any`` here — the dispatcher (R110+ blanket impl / R111+
      :mod:`dispatch`) is what knows the concrete types.
    * **Default bodies** are reproduced by the helper
      :func:`default_capabilities` (for ``capabilities``) and by the
      documented expectation that ``execute`` delegates to ``run`` when a
      tool does not stream. Tool authors implement exactly the methods
      they override.

    The :class:`AsyncIterator` return of :meth:`execute` is the Python
    equivalent of ``ToolStream<Self::Output>`` (a ``Pin<Box<dyn Stream<Item
    = ToolStreamItem<Self::Output>> + Send>>``).
    """

    def id(self) -> ToolId:
        """Stable tool identifier (Rust ``fn id(&self) -> ToolId``)."""
        ...

    def description(self, ctx: ListToolsContext) -> ToolDescription:
        """Declarative description: name, namespace, kind, schema.

        Rust ``fn description(&self, ctx: &ListToolsContext) ->
        ToolDescription``. May read the per-turn context (e.g. to filter
        arguments by capability).
        """
        ...

    def capabilities(self) -> ToolCapabilities:
        """Per-tool capabilities (Rust default: ``ToolCapabilities::default()``).

        Reproduce the Rust default by returning
        :func:`default_capabilities` unless the tool overrides.
        """
        ...

    def has_dynamic_description(self) -> bool:
        """Whether :meth:`description` changes per-turn (Rust default: ``False``)."""
        ...

    def should_list(self, ctx: ListToolsContext) -> bool:
        """Whether the tool appears in the listing (Rust default: ``True``)."""
        ...

    def execute(
        self, ctx: ToolCallContext, args: Any
    ) -> AsyncIterator[ToolStreamItem[Any]]:
        """Drive the tool, yielding a :type:`ToolStream` of items.

        Rust default body delegates to ``run`` via ``with_progress`` /
        ``terminal_only``. The Python contract is the same: a tool that
        does not stream implements :meth:`run` and lets ``execute`` wrap
        it; a streaming tool overrides :meth:`execute` directly.

        ``args`` is the typed ``Self::Args`` (``Any`` here — the dispatcher
        deserialises the inbound ``Value`` into the concrete type before
        calling). Returns an async iterator of
        ``ToolStreamItem<Self::Output>``.
        """
        ...

    async def run(self, ctx: ToolCallContext, args: Any) -> Any:
        """Non-streaming entry point (Rust default: ``not_implemented``).

        Rust ``async fn run(&self, ctx: ToolCallContext, args: Self::Args)
        -> Result<Self::Output, ToolError>`` with a default body returning
        ``Err(ToolError::not_implemented(...))``. A tool implements exactly
        one of :meth:`run` (non-streaming) or :meth:`execute` (streaming).
        """
        ...


# -----------------------------------------------------------------------
# ToolDyn — the type-erased contract the dispatcher holds.
# -----------------------------------------------------------------------


class ToolDyn(Protocol):
    """Object-safe tool contract the dispatcher drives (Rust ``trait ToolDyn``).

    Where :class:`Tool` is the typed contract a tool author codes against,
    :class:`ToolDyn` is the type-erased contract the dispatcher holds a
    heterogeneous collection of: args arrive as opaque ``serde_json::Value``
    and the output stream yields :class:`TypedToolOutput`. The blanket
    ``impl<T: Tool> ToolDyn for T`` bridges the two — it parses args,
    drives the typed :meth:`Tool.execute`, and realises each terminal item
    as a :class:`TypedToolOutput` via :meth:`TypedToolOutput.from_value`.
    The async stream-mapping itself lands with
    :mod:`minimax_code.tool_runtime.dispatch`.
    """

    def id(self) -> ToolId:
        """Stable tool identifier (delegates to :meth:`Tool.id`)."""
        ...

    def description(self, ctx: ListToolsContext) -> ToolDescription:
        """Declarative description (delegates to :meth:`Tool.description`)."""
        ...

    def execute(
        self, ctx: ToolCallContext, args: Any
    ) -> AsyncIterator[ToolStreamItem[TypedToolOutput]]:
        """Drive the tool with type-erased args, yielding :class:`TypedToolOutput`.

        Rust ``async fn execute(&self, ctx: ToolCallContext, args: Value)
        -> ToolStream<TypedToolOutput>``. ``args`` is the raw inbound JSON
        value; the blanket impl deserialises it into the concrete
        ``Self::Args`` before delegating to :meth:`Tool.execute`.
        """
        ...


# -----------------------------------------------------------------------
# ToolFamily — a family of tool variants under one id.
# -----------------------------------------------------------------------


class ToolFamily(Protocol):
    """A family of tool variants under one id (Rust ``trait ToolFamily``).

    Lets one tool id expose multiple behavioural variants (e.g. a file
    edit tool with ``apply`` / ``preview`` variants). The dispatcher
    resolves the variant name off the inbound call and fetches the
    matching :class:`Tool`.
    """

    def id(self) -> ToolId:
        """Stable family identifier."""
        ...

    def get_tool(self, variant: ToolVariant) -> Tool | None:
        """Fetch the tool for ``variant`` (``None`` if the variant is unknown)."""
        ...

    def variants(self) -> list[ToolVariant]:
        """All variants the family exposes."""
        ...

    def default_variant_name(self) -> str | None:
        """The default variant name (``None`` if the family has a single variant).

        Rust ``fn default_variant_name(&self) -> Option<&'static str>``.
        """
        ...


# -----------------------------------------------------------------------
# ArcTool / ArcToolFamily — reference-shared handles (Python aliases).
# -----------------------------------------------------------------------


#: Rust ``type ArcTool = Arc<dyn ToolDyn>``. Python objects are already
#: reference-shared (no ``Arc``), and :class:`ToolDyn` is a structural
#: :class:`Protocol`, so the alias IS the protocol type.
ArcTool: TypeAlias = ToolDyn

#: Rust ``type ArcToolFamily = Arc<dyn ToolFamily>``. Same reasoning as
#: :type:`ArcTool` — the alias is the protocol type itself.
ArcToolFamily: TypeAlias = ToolFamily
