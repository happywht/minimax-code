"""Request metadata header constants + sorted map (R67).

Fusion of grok-build's ``xai-grok-workspace-types::metadata`` — the
gRPC/HTTP metadata header keys the workspace transport reads, plus the
``Metadata`` newtype over ``BTreeMap<String, String>``.

``Metadata`` is ``#[serde(transparent)]`` over a sorted map: it
serialises as a bare JSON object whose keys are emitted in deterministic
lexicographic order (BTreeMap's defining property). A plain Python
``dict`` preserves insertion order (3.7+), so :meth:`Metadata.to_wire`
sorts on the way out to mirror BTreeMap — this matters for byte-stable
wire snapshots and snapshot tests (a hash of the serialised bytes is
only meaningful when the key order is fixed).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

__all__ = [
    "META_SESSION_ID",
    "META_TRACEPARENT",
    "META_TRACESTATE",
    "META_CLIENT_ID",
    "META_PROMPT_INDEX",
    "META_GRPC_TIMEOUT",
    "STANDARD_META_KEYS",
    "Metadata",
]

META_SESSION_ID = "x-workspace-session-id"
META_TRACEPARENT = "traceparent"
META_TRACESTATE = "tracestate"
META_CLIENT_ID = "x-workspace-client-id"
META_PROMPT_INDEX = "x-workspace-prompt-index"
META_GRPC_TIMEOUT = "grpc-timeout"

#: Standard metadata keys in the source's declaration order.
STANDARD_META_KEYS = (
    META_SESSION_ID,
    META_TRACEPARENT,
    META_TRACESTATE,
    META_CLIENT_ID,
    META_PROMPT_INDEX,
    META_GRPC_TIMEOUT,
)


class Metadata(dict):
    """Sorted ``{str: str}`` metadata map — serialises as a bare object.

    The underlying storage is a plain ``dict``; all *read* surface
    (``to_wire`` / ``iter_sorted`` / ``keys_sorted`` / ``values_sorted``)
    returns keys in lexicographic order to mirror ``BTreeMap``. Mutation
    (``insert`` / ``remove``) preserves the BTreeMap contract by sorting
    on the way out, so the wire bytes stay stable regardless of
    insertion order.

    Values must be strings — the source is ``BTreeMap<String, String>``.
    """

    def __init__(self, items: Mapping[str, str] | None = None) -> None:
        super().__init__(items if items is not None else ())

    # -- mutation (keep BTreeMap semantics) --------------------------------

    def insert(self, key: str, value: str) -> None:
        """Insert ``key=value`` (mirrors ``BTreeMap::insert``)."""
        self[key] = value

    def remove(self, key: str) -> str | None:
        """Remove ``key``, returning its value or ``None`` (``BTreeMap::remove``)."""
        return dict.pop(self, key, None)

    # -- reads (sorted, mirror BTreeMap iteration order) -------------------

    def get(self, key: str, default: str | None = None) -> str | None:
        return dict.get(self, key, default)

    def contains_key(self, key: str) -> bool:
        return key in self

    def keys_sorted(self) -> list[str]:
        return sorted(self.keys())

    def values_sorted(self) -> list[str]:
        return [self[k] for k in sorted(self.keys())]

    def iter_sorted(self) -> Iterator[tuple[str, str]]:
        for k in sorted(self.keys()):
            yield k, self[k]

    # -- wire (transparent over a sorted object) ---------------------------

    def to_wire(self) -> dict[str, str]:
        """Emit a bare object whose keys are in sorted (BTreeMap) order."""
        return {k: self[k] for k in sorted(self.keys())}

    @classmethod
    def from_wire(cls, data: Mapping[str, str]) -> Metadata:
        return cls(data)

    def __repr__(self) -> str:
        return f"Metadata({dict(self)!r})"
