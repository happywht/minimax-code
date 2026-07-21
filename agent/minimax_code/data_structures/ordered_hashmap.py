"""Insertion-ordered hash map (R241, vendored ``third_party/ordered_hashmap``).

Mirrors the public API surface of grok's vendored ``ordered_hashmap`` 0.0.3
crate (upstream ``r3alst/ordered-hashmap``, Apache-2.0) -- the zero-dependency
data-structure primitive that underpins the mermaid render pipeline's
``graphlib_rust`` + ``dagre_rust`` layout stack.

**First leaf of the ``data_structures`` package (R241)** -- opens the
data-structure primitives migration chain. ``ordered_hashmap`` is the strict
zero-dependency base (``[dependencies]`` is empty in the vendored
``Cargo.toml``); ``graphlib_rust`` (single path dep on this crate) and
``dagre_rust`` (path deps on both) build on top and migrate next.

The crate is vendored (rather than consumed from crates.io) because the
mermaid render path accepts untrusted input and the full audit surface of
its transitive layout dependencies must live in-tree.

Single-dict substitution
------------------------
grok models the map as two parallel fields -- ``keys: Vec<K>`` (insertion
order) + ``map: HashMap<K, V>`` (lookup) -- because Rust's ``HashMap`` is
iteration-disordered. Python's :class:`dict` has been insertion-ordered since
3.7, so the two-field structure collapses to a single ``dict`` backend:
``self._data``. Every operation maps directly:

- ``insert(k, v)``  ->  ``self._data[k] = v`` (new key appends; existing key
  overwrites value, preserves position).
- ``remove(k)``     ->  ``del self._data[k]`` (or ``.pop(k, None)``).
- ``keys()``        ->  ``iter(self._data.keys())``.
- ``values()``      ->  ``iter(self._data.values())``.
- ``iter()``        ->  ``iter(self._data.items())``.
- ``into_values()`` ->  ``list(self._data.values())``.
- ``extend(other)`` ->  ``for k, v in other._data.items(): self._data[k] = v``
  (matches grok's semantics exactly: new keys append to the order tail,
  existing keys keep their position but have their value overwritten).

unsafe elimination
------------------
The vendored crate has two ``unsafe`` blocks -- ``values_mut`` and
``iter_mut`` -- each a raw-pointer re-borrow of ``&mut self.map`` inside a
closure that also captures ``self.keys`` by shared reference (Rust's borrow
checker forbids the dual capture without ``unsafe``). Python has no borrow
checker: :meth:`OrderedHashMap.iter` already hands out live references to the
boxed values, so in-place mutation of a mutable ``V`` is simply
``for k, v in ohm.iter(): v.field = x`` -- no separate ``values_mut`` /
``iter_mut`` API needed. Both are omitted here as YAGNI.

Equality is order-sensitive
---------------------------
grok derives ``PartialEq`` over ``(keys: Vec, map: HashMap)``: the ``Vec``
comparison makes equality **insertion-order-sensitive** -- two maps with the
same key/value pairs in different insertion orders compare unequal. Python's
``dict.__eq__`` ignores insertion order (``{1: 1, 2: 2} == {2: 2, 1: 1}`` is
``True``), so :meth:`OrderedHashMap.__eq__` cannot delegate to ``dict``
equality. It compares ``list(self._data.items())`` instead, which is
order-sensitive and faithful to grok.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class OrderedHashMap(Generic[K, V]):
    """A hash map that remembers insertion order.

    Mirrors grok ``OrderedHashMap<K, V>`` (``#[derive(Clone, Debug, PartialEq)]``).
    The ``Clone`` derive is implicit in Python (no ``Copy`` semantics to worry
    about); ``Debug`` maps to :meth:`__repr__`; ``PartialEq`` is order-sensitive
    (see module docstring).
    """

    __slots__ = ("_data",)

    def __init__(self) -> None:
        #: Insertion-ordered key/value store (single-dict backend; see module
        #: docstring for the keys+map collapse rationale).
        self._data: dict[K, V] = {}

    # -- construction --------------------------------------------------------

    @classmethod
    def new(cls) -> OrderedHashMap[K, V]:
        """Create an empty ``OrderedHashMap``.

        Mirrors grok ``OrderedHashMap::new``. Provided as a classmethod to
        match the grok call site (``OrderedHashMap::new()``); Python callers
        may also use the ``OrderedHashMap()`` constructor directly.
        """
        return cls()

    # -- size / containment --------------------------------------------------

    def __len__(self) -> int:
        return len(self._data)

    def len(self) -> int:
        """Return the number of key/value pairs (mirrors grok ``len``)."""
        return len(self._data)

    def __bool__(self) -> bool:
        """Truthiness -- empty map is falsy (Python protocol; grok has none)."""
        return bool(self._data)

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def contains_key(self, key: K) -> bool:
        """Return ``True`` if ``key`` is present (mirrors grok ``contains_key``)."""
        return key in self._data

    # -- lookup --------------------------------------------------------------

    def get(self, key: K) -> V | None:
        """Return the value for ``key``, or ``None`` if absent.

        Mirrors grok ``get(&self, key) -> Option<&V>``. Python returns the
        value by reference; for a mutable reference see :meth:`get_mut`.
        """
        return self._data.get(key)

    def get_mut(self, key: K) -> V | None:
        """Return a *mutable* reference to the value for ``key``, or ``None``.

        Mirrors grok ``get_mut(&mut self, key) -> Option<&mut V>``. In Python
        every value is boxed behind a reference, so the returned object is the
        live boxed value -- mutating an attribute on it (``v.field = x``)
        mutates the entry in place, exactly as the Rust ``&mut V`` would.
        """
        return self._data.get(key)

    # -- mutation ------------------------------------------------------------

    def insert(self, key: K, value: V) -> V | None:
        """Insert ``key``/``value``, returning the previous value if any.

        Mirrors grok ``insert``: a new key appends to the insertion order; an
        existing key keeps its position and has its value overwritten, with
        the old value returned. ``dict[k] = v`` already has both behaviours
        (append-or-overwrite) and ``dict.put``-via-subscript returns ``None``,
        so the previous value is read first via :meth:`get`.
        """
        previous = self._data.get(key)
        self._data[key] = value
        return previous

    def remove(self, key: K) -> V | None:
        """Remove ``key``, returning its value or ``None`` if absent.

        Mirrors grok ``remove`` (``keys.retain`` + ``map.remove``). Python's
        ``dict.pop`` removes the key and preserves the remaining insertion
        order, matching grok's post-``retain`` order exactly.
        """
        return self._data.pop(key, None)

    def extend(self, other: OrderedHashMap[K, V]) -> None:
        """Absorb ``other`` into this map (mirrors grok ``extend``).

        Semantics (faithful to grok): for each key in ``other``'s insertion
        order, a *new* key appends to this map's order tail; an *existing*
        key keeps its position but has its value overwritten by ``other``'s
        value. ``dict[k] = v`` has precisely this append-or-overwrite-in-place
        behaviour, so a straight item loop is an exact translation of grok's
        ``keys.push`` + ``map.extend`` pair.
        """
        for k, v in other._data.items():
            self._data[k] = v

    def entry(self, key: K) -> Entry[K, V]:
        """Return an :class:`Entry` for ``key`` (mirrors grok ``entry``).

        The entry holds a live reference to this map's backing dict, so
        :meth:`Entry.or_insert` / :meth:`Entry.or_insert_with` mutate this map
        in place. Unlike grok's ``Entry`` -- which pushes the key to
        ``self.keys`` eagerly on the ``Vacant`` branch -- this Python
        :class:`Entry` defers the actual dict insertion until
        ``or_insert`` / ``or_insert_with`` is called, avoiding a transient
        keys/map inconsistency window. The observable post-condition is
        identical: after ``or_insert(default)`` the key is present with the
        default value and ordered last.
        """
        return Entry(self._data, key)

    # -- ordered iteration ---------------------------------------------------

    def __iter__(self) -> Iterator[K]:
        """Iterate keys in insertion order (Python dict convention).

        Note this differs from :meth:`iter`, which yields ``(key, value)``
        pairs (grok's ``iter``). ``__iter__`` yields keys so that
        ``for k in ohm`` matches the Python mapping protocol.
        """
        return iter(self._data)

    def keys(self) -> Iterator[K]:
        """Yield keys in insertion order (mirrors grok ``keys``)."""
        return iter(self._data)

    def values(self) -> Iterator[V]:
        """Yield values in insertion order (mirrors grok ``values``)."""
        return iter(self._data.values())

    def iter(self) -> Iterator[tuple[K, V]]:
        """Yield ``(key, value)`` pairs in insertion order (mirrors grok ``iter``).

        The yielded value is a live reference; mutating an attribute on a
        mutable ``V`` mutates the entry in place (this is why grok's
        ``iter_mut`` -- an ``unsafe`` raw-pointer re-borrow -- is not needed
        in Python).
        """
        return iter(self._data.items())

    def into_values(self) -> list[V]:
        """Consume the map's values in insertion order (mirrors grok ``into_values``).

        grok's ``into_values(self)`` is a move -- ``self`` is consumed and the
        values are drained in key-insertion order. Python has no move
        semantics, so this returns a fresh ``list`` and leaves ``self``
        intact; callers should treat the map as logically consumed (the
        name is preserved for API-surface parity with grok).
        """
        return list(self._data.values())

    # -- equality / repr -----------------------------------------------------

    def __eq__(self, other: object) -> bool:
        """Order-sensitive equality (mirrors grok ``PartialEq`` over ``Vec<K>``).

        Two maps compare equal only when their insertion-ordered item
        sequences match exactly -- ``OrderedHashMap([(1, 1), (2, 2)])`` is
        NOT equal to ``OrderedHashMap([(2, 2), (1, 1)])``. This diverges from
        plain ``dict.__eq__`` (which ignores insertion order) on purpose, to
        match grok's ``Vec<K>``-driven equality.
        """
        if not isinstance(other, OrderedHashMap):
            return NotImplemented
        return list(self._data.items()) == list(other._data.items())

    def __repr__(self) -> str:
        """Debug representation (mirrors grok ``#[derive(Debug)]``)."""
        inner = ", ".join(f"{k!r}: {v!r}" for k, v in self._data.items())
        return f"OrderedHashMap({{{inner}}})"


class Entry(Generic[K, V]):
    """A map entry returned by :meth:`OrderedHashMap.entry`.

    Mirrors grok ``Entry<'a, K, V>`` (the ``Occupied`` / ``Vacant`` enum).
    Python collapses the two variants into a single class that records whether
    the key was present at construction (``_occupied``) and holds a live
    reference to the backing dict (``_data``) plus the key (``_key``). Both
    :meth:`or_insert` and :meth:`or_insert_with` consume the entry.
    """

    __slots__ = ("_data", "_key", "_occupied")

    def __init__(self, data: dict[K, V], key: K) -> None:
        self._data = data
        self._key = key
        self._occupied = key in data

    def or_insert(self, default: V) -> V:
        """Insert ``default`` if the key was vacant, else return the existing value.

        Mirrors grok ``Entry::or_insert``. On the vacant branch the key is
        inserted at the order tail (dict append); on the occupied branch the
        existing value is returned unchanged. The returned value is a live
        reference, matching grok's ``&'a mut V`` return.
        """
        if self._occupied:
            return self._data[self._key]
        self._data[self._key] = default
        return default

    def or_insert_with(self, default: Callable[[], V]) -> V:
        """Insert ``default()`` if vacant, else return the existing value.

        Mirrors grok ``Entry::or_insert_with``. The factory is called only on
        the vacant branch (lazy), so it is never evaluated when the key is
        already present -- important when the default is expensive or has
        side effects.
        """
        if self._occupied:
            return self._data[self._key]
        value = default()
        self._data[self._key] = value
        return value
