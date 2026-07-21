"""Tests for data_structures.ordered_hashmap (R241, vendored ``third_party/ordered_hashmap`` 0.0.3).

Covers the migrated insertion-ordered hash map: :class:`OrderedHashMap` (15
public methods mirroring grok's API surface) + :class:`Entry`
(``or_insert`` / ``or_insert_with``). Mirrors grok's ``ordered_hashmap``
doc-tests (the contract source) plus Python-specific contract checks:
single-dict backend order preservation, the **order-sensitive** ``__eq__``
(which diverges from plain ``dict.__eq__`` -- the critical grok-vs-Python
difference), unsafe ``values_mut`` / ``iter_mut`` elimination via Python
reference semantics, and ``Entry`` lazy insertion.
"""

from __future__ import annotations

from minimax_code.data_structures import Entry, OrderedHashMap

# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------


def _filled(*pairs: tuple[str, int]) -> OrderedHashMap[str, int]:
    """Build an OrderedHashMap from insertion-ordered (key, value) pairs."""
    ohm: OrderedHashMap[str, int] = OrderedHashMap()
    for k, v in pairs:
        ohm.insert(k, v)
    return ohm


# ---------------------------------------------------------------------------
# new / __init__ / len / bool (mirrors grok test_len_*).
# ---------------------------------------------------------------------------


def test_new_returns_empty_map() -> None:
    """grok: ``let ordered_map = OrderedHashMap::new()`` -- empty."""
    ohm: OrderedHashMap[str, int] = OrderedHashMap.new()
    assert len(ohm) == 0
    assert ohm.len() == 0
    assert not ohm


def test_init_returns_empty_map() -> None:
    """Python constructor ``OrderedHashMap()`` is equivalent to ``new()``."""
    ohm: OrderedHashMap[str, int] = OrderedHashMap()
    assert len(ohm) == 0


def test_len_grows_with_inserts() -> None:
    """grok: assert_eq!(map.len(), 0); insert; assert_eq!(map.len(), 1)."""
    ohm: OrderedHashMap[int, str] = OrderedHashMap()
    assert ohm.len() == 0
    ohm.insert(1, "one")
    assert ohm.len() == 1
    ohm.insert(2, "two")
    assert ohm.len() == 2


def test_bool_empty_is_falsy() -> None:
    """Python protocol (grok has no bool); empty map is falsy."""
    assert not OrderedHashMap()


def test_bool_nonempty_is_truthy() -> None:
    assert _filled(("a", 1))


# ---------------------------------------------------------------------------
# insert (mirrors grok doc-test insert contract).
# ---------------------------------------------------------------------------


def test_insert_new_key_returns_none() -> None:
    """grok: assert_eq!(ordered_map.insert("key1", 42), None)."""
    ohm: OrderedHashMap[str, int] = OrderedHashMap()
    assert ohm.insert("key1", 42) is None
    assert ohm.get("key1") == 42


def test_insert_existing_key_returns_old_value() -> None:
    """grok: assert_eq!(ordered_map.insert("key1", 99), Some(42))."""
    ohm = _filled(("key1", 42))
    assert ohm.insert("key1", 99) == 42
    assert ohm.get("key1") == 99
    assert ohm.len() == 1  # no duplicate key


def test_insert_preserves_existing_key_position() -> None:
    """Re-inserting an existing key must NOT move it to the end.

    grok: ``if !self.map.contains_key(&key) { self.keys.push(...) }`` -- only
    new keys append to the order. Python ``dict[k] = v`` preserves position on
    overwrite, matching grok exactly.
    """
    ohm = _filled(("a", 1), ("b", 2), ("c", 3))
    ohm.insert("a", 99)
    assert list(ohm.keys()) == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# remove (mirrors grok doc-test remove contract).
# ---------------------------------------------------------------------------


def test_remove_existing_returns_value() -> None:
    """grok: assert_eq!(ordered_map.remove(&"key1"), Some(42))."""
    ohm = _filled(("key1", 42))
    assert ohm.remove("key1") == 42
    assert ohm.len() == 0
    assert not ohm.contains_key("key1")


def test_remove_absent_returns_none() -> None:
    ohm = _filled(("a", 1))
    assert ohm.remove("missing") is None
    assert ohm.len() == 1


def test_remove_preserves_remaining_order() -> None:
    """grok keys.retain drops the key; remaining keys keep their relative order.

    Python dict.pop likewise preserves the surviving insertion order.
    """
    ohm = _filled(("a", 1), ("b", 2), ("c", 3))
    ohm.remove("b")
    assert list(ohm.keys()) == ["a", "c"]


# ---------------------------------------------------------------------------
# get / get_mut / contains_key (mirrors grok doc-tests).
# ---------------------------------------------------------------------------


def test_get_existing_returns_value() -> None:
    """grok: assert_eq!(ordered_map.get(&"key1"), Some(&42))."""
    ohm = _filled(("key1", 42))
    assert ohm.get("key1") == 42


def test_get_absent_returns_none() -> None:
    ohm = _filled(("a", 1))
    assert ohm.get("missing") is None


def test_contains_key_true() -> None:
    """grok: assert!(ordered_map.contains_key(&"key1"))."""
    ohm = _filled(("key1", 42))
    assert ohm.contains_key("key1")
    assert "key1" in ohm  # __contains__ protocol


def test_contains_key_false() -> None:
    ohm = _filled(("a", 1))
    assert not ohm.contains_key("missing")
    assert "missing" not in ohm


def test_get_mut_allows_in_place_mutation() -> None:
    """get_mut returns a live reference; mutating a mutable value mutates the entry.

    Mirrors grok ``*ordered_map.get_mut(&"key1").unwrap() += 1``. Python ints
    are immutable, so a list value proves the reference aliasing (the
    returned object IS the stored object).
    """
    ohm: OrderedHashMap[str, list[int]] = OrderedHashMap()
    ohm.insert("k", [1, 2])
    ref = ohm.get_mut("k")
    assert ref is not None
    ref.append(3)
    assert ohm.get("k") == [1, 2, 3]


def test_get_mut_absent_returns_none() -> None:
    ohm = _filled(("a", 1))
    assert ohm.get_mut("missing") is None


# ---------------------------------------------------------------------------
# keys / values / iter (mirrors grok doc-tests on insertion order).
# ---------------------------------------------------------------------------


def test_keys_preserves_insertion_order() -> None:
    """grok: keys == vec![&"key1", &"key2"]."""
    ohm = _filled(("key1", 42), ("key2", 24))
    assert list(ohm.keys()) == ["key1", "key2"]


def test_values_preserves_insertion_order() -> None:
    """grok: values == vec![&42, &24]."""
    ohm = _filled(("key1", 42), ("key2", 24))
    assert list(ohm.values()) == [42, 24]


def test_iter_yields_pairs_in_order() -> None:
    """grok: iter == vec![(&"key1", &42), (&"key2", &24)]."""
    ohm = _filled(("key1", 42), ("key2", 24))
    assert list(ohm.iter()) == [("key1", 42), ("key2", 24)]


def test_dunder_iter_yields_keys() -> None:
    """``__iter__`` yields keys (Python mapping protocol; distinct from ``iter()``)."""
    ohm = _filled(("a", 1), ("b", 2))
    assert list(iter(ohm)) == ["a", "b"]
    assert list(ohm) == ["a", "b"]


def test_iter_value_is_live_reference() -> None:
    """iter() yields live value refs -- mutation propagates (iter_mut eliminated).

    Replaces grok's ``iter_mut`` (an ``unsafe`` raw-pointer re-borrow): the
    plain Python ``iter()`` already hands out live references, so in-place
    mutation of a mutable ``V`` needs no separate API.
    """
    ohm: OrderedHashMap[str, list[int]] = OrderedHashMap()
    ohm.insert("k", [1])
    for k, v in ohm.iter():
        if k == "k":
            v.append(2)
    assert ohm.get("k") == [1, 2]


# ---------------------------------------------------------------------------
# into_values (mirrors grok doc-test into_values).
# ---------------------------------------------------------------------------


def test_into_values_preserves_order() -> None:
    """grok: into_values == vec!["one", "two", "three"]."""
    ohm = _filled((1, "one"), (2, "two"), (3, "three"))
    assert ohm.into_values() == ["one", "two", "three"]


def test_into_values_empty() -> None:
    assert OrderedHashMap().into_values() == []


def test_into_values_returns_list() -> None:
    """Return type is ``list`` (mirrors grok ``Vec<V>``)."""
    ohm = _filled((1, "one"))
    assert isinstance(ohm.into_values(), list)


# ---------------------------------------------------------------------------
# extend (mirrors grok doc-test extend).
# ---------------------------------------------------------------------------


def test_extend_appends_new_keys() -> None:
    """grok: map1.extend(map2) -> map1 has both keys, in insertion order."""
    map1 = _filled((1, "one"))
    map2 = _filled((2, "two"))
    map1.extend(map2)
    assert map1.get(1) == "one"
    assert map1.get(2) == "two"
    assert list(map1.keys()) == [1, 2]


def test_extend_overwrites_existing_value_preserving_position() -> None:
    """grok extend: an existing key keeps its position, value overwritten.

    map1 = {1:one, 2:two}; extend {2:TWO, 3:three} -> {1:one, 2:TWO, 3:three}
    (key 2 stays second, not moved to tail). ``dict[k] = v`` does exactly this.
    """
    map1 = _filled((1, "one"), (2, "two"))
    map2 = _filled((2, "TWO"), (3, "three"))
    map1.extend(map2)
    assert list(map1.keys()) == [1, 2, 3]
    assert map1.get(2) == "TWO"
    assert map1.get(1) == "one"
    assert map1.get(3) == "three"


def test_extend_empty_other_is_noop() -> None:
    map1 = _filled((1, "one"))
    map1.extend(OrderedHashMap())
    assert list(map1.keys()) == [1]


# ---------------------------------------------------------------------------
# entry (mirrors grok doc-test entry + or_insert).
# ---------------------------------------------------------------------------


def test_entry_or_insert_occupied_returns_existing() -> None:
    """grok: entry("key1").or_insert(99) on occupied key -> keeps 42."""
    ohm = _filled(("key1", 42))
    result = ohm.entry("key1").or_insert(99)
    assert result == 42
    assert ohm.get("key1") == 42  # NOT overwritten


def test_entry_or_insert_vacant_inserts_default() -> None:
    """grok Vacant branch: key appended at order tail with the default value."""
    ohm = _filled(("a", 1))
    result = ohm.entry("b").or_insert(2)
    assert result == 2
    assert ohm.get("b") == 2
    assert list(ohm.keys()) == ["a", "b"]  # appended at tail


def test_entry_or_insert_with_occupied_skips_factory() -> None:
    """Factory must NOT be called when the key is present (lazy default)."""
    ohm = _filled(("k", 1))
    called: list[bool] = []

    def factory() -> int:
        called.append(True)
        return 99

    result = ohm.entry("k").or_insert_with(factory)
    assert result == 1
    assert called == []  # factory never invoked
    assert ohm.get("k") == 1


def test_entry_or_insert_with_vacant_calls_factory() -> None:
    ohm: OrderedHashMap[str, int] = OrderedHashMap()
    result = ohm.entry("k").or_insert_with(lambda: 42)
    assert result == 42
    assert ohm.get("k") == 42


def test_entry_is_returned_type() -> None:
    """``entry`` returns an :class:`Entry` instance (mirrors grok ``Entry`` enum)."""
    ohm = _filled(("a", 1))
    assert isinstance(ohm.entry("a"), Entry)
    assert isinstance(ohm.entry("missing"), Entry)


# ---------------------------------------------------------------------------
# __eq__ (order-sensitive -- the critical grok-vs-Python divergence).
# ---------------------------------------------------------------------------


def test_eq_same_order_equal() -> None:
    a = _filled(("a", 1), ("b", 2))
    b = _filled(("a", 1), ("b", 2))
    assert a == b


def test_eq_different_values_not_equal() -> None:
    a = _filled(("a", 1))
    b = _filled(("a", 2))
    assert a != b


def test_eq_different_insertion_order_not_equal() -> None:
    """grok ``PartialEq`` over ``Vec<K>`` is order-sensitive; ``dict.__eq__`` is NOT.

    This is the critical divergence: ``OrderedHashMap([(1,1),(2,2)])`` !=
    ``OrderedHashMap([(2,2),(1,1)])`` even though the underlying dicts compare
    equal under ``dict.__eq__``. ``__eq__`` compares ``list(items())`` to
    preserve grok semantics.
    """
    a = _filled((1, 1), (2, 2))
    b = _filled((2, 2), (1, 1))
    # sanity: plain dict equality would say True (order-insensitive)
    assert a._data == b._data
    # but OrderedHashMap equality is order-sensitive (mirrors grok Vec compare)
    assert a != b


def test_eq_against_non_ordered_hashmap_returns_false() -> None:
    """Non-OrderedHashMap operands compare unequal (``__eq__`` returns NotImplemented)."""
    a = _filled(("a", 1))
    assert (a == 42) is False
    assert (a == "string") is False
    assert (a == {"a": 1}) is False


def test_eq_different_lengths_not_equal() -> None:
    a = _filled(("a", 1))
    b = _filled(("a", 1), ("b", 2))
    assert a != b


# ---------------------------------------------------------------------------
# __repr__ (mirrors grok #[derive(Debug)]).
# ---------------------------------------------------------------------------


def test_repr_empty() -> None:
    assert repr(OrderedHashMap()) == "OrderedHashMap({})"


def test_repr_nonempty() -> None:
    ohm = _filled(("a", 1), ("b", 2))
    assert repr(ohm) == "OrderedHashMap({'a': 1, 'b': 2})"


# ---------------------------------------------------------------------------
# Mutation-during-iteration safety (replaces grok unsafe iter_mut).
# ---------------------------------------------------------------------------


def test_iteration_supports_mutation_via_loop() -> None:
    """Python iteration is safe to mutate through during a loop (no borrow checker).

    Replaces grok's ``iter_mut`` ``unsafe`` re-borrow with a plain Python loop
    over a snapshot of keys.
    """
    ohm: OrderedHashMap[str, int] = OrderedHashMap()
    ohm.insert("a", 1)
    ohm.insert("b", 2)
    for k in list(ohm.keys()):
        old = ohm.get(k)
        assert old is not None
        ohm.insert(k, old * 10)
    assert ohm.get("a") == 10
    assert ohm.get("b") == 20


def test_intoiterator_protocol_consumes_items() -> None:
    """grok ``IntoIterator for OrderedHashMap`` -> ``Vec<(K, V)>``; Python ``list(ohm.iter())``.

    grok's IntoIterator is a move that yields ``(K, V)`` owned pairs in key
    order. Python's :meth:`iter` yields the same ``(K, V)`` pairs (by
    reference) in insertion order.
    """
    ohm = _filled((1, "one"), (2, "two"))
    pairs = list(ohm.iter())
    assert pairs == [(1, "one"), (2, "two")]
