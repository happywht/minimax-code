"""RPC response envelope (R68).

Fusion of grok's ``xai-grok-workspace-types::rpc::envelope`` — the
externally-tagged response wrapper for every ``workspace.*`` method.

Wire shape::

    {"ok": <value>}
    {"err": {"code": "<code>", "message": "<message>"}}

This is serde's *externally tagged* enum representation (the variant name
is the sole key), distinct from the adjacent-tagged ``{"type", "data"}``
shape of R67's leaf enums — hence a dedicated class rather than reuse of
:class:`~minimax_code.workspace_types._tagged.AdjacentTagged`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, TypeAdapter

from minimax_code.workspace_types._wire import sort_mappings

__all__ = ["TURN_ACTIVE", "RpcError", "RpcEnvelope"]

#: Wire code for "the target session has an active turn" rejections of
#: toolset mutations (``workspace.update_tool_config``). Retryable at the
#: turn boundary. Shared so clients can recognise the retryable class
#: without depending on the workspace crate's error enum.
TURN_ACTIVE = "turn_active"

T = TypeVar("T")


class RpcError(BaseModel):
    """A typed RPC failure — discriminant ``code`` + human ``message``.

    Implements Rust's ``std::error::Error`` contract via ``__str__``;
    Python-side it is a data model (not an ``Exception`` subclass —
    pydantic and ``Exception`` metaclasses conflict), so callers unwrap
    it from :meth:`RpcEnvelope.into_result` rather than ``except`` it.
    """

    model_config = {"populate_by_name": True}

    code: str
    message: str

    def is_turn_active(self) -> bool:
        """Whether this is a :data:`TURN_ACTIVE` rejection (retryable)."""
        return self.code == TURN_ACTIVE

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


def _dump_payload(value: Any) -> Any:
    """JSON-safe dump of an ``ok`` payload, recursively unwrapping models.

    Handles pydantic ``BaseModel`` (→ BTreeMap-sorted wire dict), lists /
    tuples of them, nested mappings, and bare primitives — so
    :meth:`RpcEnvelope.ok` accepts any shape Rust's ``T`` could take (a
    single struct, a ``Vec<Struct>``, ``()``, or a primitive).
    """
    if isinstance(value, BaseModel):
        return sort_mappings(value.model_dump(mode="json", by_alias=True))
    if isinstance(value, list):
        return [_dump_payload(v) for v in value]
    if isinstance(value, tuple):
        return [_dump_payload(v) for v in value]
    if isinstance(value, Mapping):
        return {k: _dump_payload(v) for k, v in value.items()}
    return value


class RpcEnvelope(Generic[T]):
    """Externally-tagged response envelope for ``workspace.*`` RPCs.

    Wraps either ``Ok(T)`` or ``Err(RpcError)``. Generic over the success
    payload ``T``. Construct with :meth:`ok` / :meth:`err_parts`, unwrap
    with :meth:`into_result`, (de)serialise with :meth:`to_wire` /
    :meth:`from_wire` (``from_wire`` takes the response type explicitly,
    since Python cannot recover ``T`` from a bare dict the way Rust
    recovers it from the associated ``type Response``).
    """

    def __init__(self, ok: Any = None, err: RpcError | None = None) -> None:
        # Exactly one arm is set; the constructors below enforce this.
        self._ok: Any = ok
        self._err: RpcError | None = err

    # -- constructors ------------------------------------------------------

    @classmethod
    def ok(cls, value: Any) -> RpcEnvelope[Any]:
        """Wrap a success payload (``Self::Ok``)."""
        return cls(ok=value)

    @classmethod
    def err_parts(cls, code: str, message: str) -> RpcEnvelope[Any]:
        """Build an ``Err`` envelope from raw ``code`` + ``message``."""
        return cls(err=RpcError(code=code, message=message))

    @classmethod
    def err(cls, error: RpcError) -> RpcEnvelope[Any]:
        """Wrap an existing :class:`RpcError`."""
        return cls(err=error)

    # -- accessors ---------------------------------------------------------

    def is_ok(self) -> bool:
        return self._err is None

    def is_err(self) -> bool:
        return self._err is not None

    def into_result(self) -> tuple[Any, RpcError | None]:
        """Split into ``(ok, err)`` — exactly one element is non-None.

        Mirrors Rust's ``Result<T, RpcError>`` via an explicit pair;
        :class:`RpcError` is a data model (not raisable), so callers
        pattern-match rather than ``except``.
        """
        return self._ok, self._err

    # -- wire --------------------------------------------------------------

    def to_wire(self) -> dict[str, Any]:
        """Emit the externally-tagged shape ``{"ok": ...}`` / ``{"err": ...}``."""
        if self._err is not None:
            return {"err": {"code": self._err.code, "message": self._err.message}}
        return {"ok": _dump_payload(self._ok)}

    @classmethod
    def from_wire(cls, data: Mapping[str, Any], response_type: Any) -> RpcEnvelope[Any]:
        """Parse an externally-tagged envelope, validating ``ok`` against ``response_type``.

        ``response_type`` is the Rust associated ``type Response`` (e.g.
        ``FileRewindResponse``, ``list[AgentConfigFile]``, ``type(None)``);
        ``TypeAdapter`` handles struct / ``Vec`` / unit / primitive shapes.
        """
        if "err" in data:
            payload = data["err"]
            return cls(err=RpcError(code=payload["code"], message=payload["message"]))
        if "ok" in data:
            adapter = TypeAdapter(response_type)
            return cls(ok=adapter.validate_python(data["ok"]))
        raise ValueError(f"envelope has neither 'ok' nor 'err': {data!r}")
