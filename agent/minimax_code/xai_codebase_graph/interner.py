"""String interning for memory-efficient symbol storage (direction (2) brick 3).

Ported from grok ``xai-codebase-graph/src/interner.rs``. The grok interner
stores every interned byte string once in a contiguous ``arena: Vec<u8>``
and maps each unique byte slice to a compact ``StringId(u32)`` via an
``FxHashMap``-backed ``lookup`` table (``offsets`` records ``(start, len)``
per id). That arena + offsets + FxHash layout is a Rust memory / perf
optimisation, not part of the functional contract.

This Python port keeps the functional contract -- intern-once dedup, O(1)
id lookup, O(1) bytes-by-id lookup, round-trippable serialisation -- and
expresses it with the idiomatic ``dict[bytes, StringId]`` reverse index +
``list[bytes]`` forward store (``bytes`` is natively hashable, so no FxHash
counterpart is needed). ``to_parts`` / ``from_parts`` replace grok's
``arena`` / ``offsets`` / ``from_parts(arena, offsets)`` triple with a
single Pythonic ``list[bytes]`` envelope.

Public surface mirrors grok ``lib.rs`` L102
``pub use interner::{StringId, StringInterner};`` -- both names are
re-exported at the crate root (see ``xai_codebase_graph/__init__.py``).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StringId:
    """Opaque identifier for an interned byte string.

    Mirrors grok ``StringId(u32)``: a ``Copy + Hash + Eq`` newtype around a
    ``u32`` slot index. The frozen + slots dataclass gives Python the same
    hashable / comparable / immutable semantics. ``id`` is the slot index
    into ``StringInterner._strings`` (so two ids are equal iff their slots
    are equal).
    """

    id: int

    @classmethod
    def new(cls, id: int) -> StringId:
        """Construct a ``StringId`` from a raw slot index.

        Mirrors grok ``StringId::new(id: u32) -> StringId``.
        """
        return cls(id=id)

    def as_u32(self) -> int:
        """Return the underlying slot index.

        Mirrors grok ``StringId::as_u32(self) -> u32``. Kept as ``as_u32``
        rather than a dunder so the public method name matches grok.
        """
        return self.id


class StringInterner:
    """Deduplicating byte-string store with O(1) lookup.

    Pythonic reimplementation of grok ``StringInterner``. Backed by
    ``_strings`` (forward store, index == ``StringId.id``) and ``_index``
    (reverse lookup). The ``intern`` family dedups; the ``get`` family
    resolves ids <-> bytes / str.
    """

    __slots__ = ("_strings", "_index")

    def __init__(self) -> None:
        self._strings: list[bytes] = []
        self._index: dict[bytes, StringId] = {}

    @classmethod
    def with_capacity(cls, string_bytes: int, num_strings: int) -> StringInterner:
        """Pre-allocate hint constructor (no-op in Python).

        Mirrors grok ``StringInterner::with_capacity(string_bytes, num_strings)``.
        Python lists / dicts do not over-allocate 2x like ``Vec``, so the
        hint is accepted for API parity but ignored.
        """
        # Capacity hints accepted for API parity; Python containers grow
        # dynamically without Vec-style 2x over-allocation.
        _ = (string_bytes, num_strings)
        return cls()

    def intern_bytes(self, s: bytes) -> StringId:
        """Intern a byte string, returning its stable ``StringId``.

        If ``s`` was interned before, the existing id is returned (dedup);
        otherwise ``s`` is appended and a fresh id assigned. Mirrors grok
        ``intern_bytes(&[u8]) -> StringId``.
        """
        existing = self._index.get(s)
        if existing is not None:
            return existing
        new_id = StringId.new(len(self._strings))
        self._strings.append(s)
        self._index[s] = new_id
        return new_id

    def intern(self, s: str) -> StringId:
        """Intern a ``str`` via its UTF-8 encoding.

        Mirrors grok ``intern(&str) -> StringId`` (a UTF-8 wrapper around
        ``intern_bytes``).
        """
        return self.intern_bytes(s.encode("utf-8"))

    def get_bytes_id(self, s: bytes) -> StringId | None:
        """Return the ``StringId`` for ``s`` if interned, else ``None``.

        Mirrors grok ``get_bytes_id(&[u8]) -> Option<StringId>``.
        """
        return self._index.get(s)

    def get_id(self, s: str) -> StringId | None:
        """Return the ``StringId`` for ``s`` (UTF-8) if interned, else ``None``.

        Mirrors grok ``get_id(&str) -> Option<StringId>``.
        """
        return self.get_bytes_id(s.encode("utf-8"))

    def get_bytes(self, id: StringId) -> bytes | None:
        """Return the interned bytes for ``id``, or ``None`` if out of range.

        Mirrors grok ``get_bytes(StringId) -> Option<&[u8]>`` (O(1) slice
        via ``Vec::get`` bounds check).
        """
        idx = id.as_u32()
        if 0 <= idx < len(self._strings):
            return self._strings[idx]
        return None

    def get(self, id: StringId) -> str | None:
        """Return the interned string for ``id`` as UTF-8, or ``None``.

        Returns ``None`` if the id is out of range **or** the stored bytes
        are not valid UTF-8. Mirrors grok ``get(StringId) -> Option<&str>``
        (which ``.ok()``-flattens ``str::from_utf8``).
        """
        raw = self.get_bytes(id)
        if raw is None:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def get_lossy(self, id: StringId) -> str | None:
        """Return the interned bytes for ``id`` as a lossy UTF-8 ``str``.

        Invalid byte sequences are replaced with ``U+FFFD`` (mirrors grok
        ``get_lossy`` via ``String::from_utf8_lossy``). Returns ``None`` only
        when the id is out of range.
        """
        raw = self.get_bytes(id)
        if raw is None:
            return None
        return raw.decode("utf-8", errors="replace")

    def __len__(self) -> int:
        """Number of interned strings.

        Mirrors grok ``StringInterner::len`` as the Pythonic ``__len__``
        dunder so ``len(interner)`` works.
        """
        return len(self._strings)

    def is_empty(self) -> bool:
        """Whether no strings are interned.

        Mirrors grok ``StringInterner::is_empty``.
        """
        return len(self._strings) == 0

    def arena_bytes(self) -> int:
        """Total bytes across all interned strings.

        Mirrors grok ``arena_bytes`` (sum of the arena byte length, excluding
        per-slot bookkeeping).
        """
        return sum(len(s) for s in self._strings)

    def iter(self) -> Iterator[str]:
        """Yield interned strings that are valid UTF-8, in id order.

        Mirrors grok ``iter`` (UTF-8-only): invalid byte strings are skipped
        rather than raising.
        """
        for raw in self._strings:
            try:
                yield raw.decode("utf-8")
            except UnicodeDecodeError:
                continue

    def iter_bytes(self) -> Iterator[bytes]:
        """Yield all interned byte strings in id order.

        Mirrors grok ``iter_bytes`` (includes invalid-UTF-8 entries that
        ``iter`` skips).
        """
        return iter(self._strings)

    def clear(self) -> None:
        """Drop every interned string.

        Mirrors grok ``StringInterner::clear``.
        """
        self._strings.clear()
        self._index.clear()

    def to_parts(self) -> list[bytes]:
        """Serialise the interner to a list of interned byte strings.

        Pythonic replacement for grok's ``arena()`` + ``offsets()`` pair: the
        dedup'd byte list is the complete serialisable state (the reverse
        index is rebuilt by ``from_parts``). Order == id order.
        """
        return list(self._strings)

    @classmethod
    def from_parts(cls, strings: Iterable[bytes]) -> StringInterner:
        """Rebuild an interner from a ``to_parts`` byte-string list.

        Mirrors grok ``StringInterner::from_parts(arena, offsets)``: the
        reverse index is reconstructed from the byte list. Like grok, the
        input is trusted to be dedup'd -- duplicates would collapse to the
        last-seen id in the reverse index.
        """
        obj = cls()
        obj._strings = list(strings)
        obj._index = {raw: StringId.new(i) for i, raw in enumerate(obj._strings)}
        return obj

    def shrink_to_fit(self) -> None:
        """Shrink capacity to fit current size (no-op in Python).

        Mirrors grok ``StringInterner::shrink_to_fit`` (``pub(crate)``,
        called by ``ScopeGraphIndex::compact``). Python lists / dicts do not
        retain excess capacity, so this is a no-op kept for API parity.
        """
        # No-op: Python containers do not retain Vec-style excess capacity.
        return None
