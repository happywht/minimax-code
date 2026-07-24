"""Tests for ``xai_codebase_graph.interner`` (direction (2) brick 3).

Mirrors the six grok ``interner.rs`` tests (basic interning, get_id, bytes
interning with invalid UTF-8, 10k strings, from_parts, clear) plus Pythonic
surface coverage (get_bytes / get_lossy / iter / iter_bytes / arena_bytes /
StringId hashability / crate-root re-export).
"""

from __future__ import annotations

import minimax_code.xai_codebase_graph as xcg
from minimax_code.xai_codebase_graph import StringId, StringInterner


def test_string_id_new_and_as_u32() -> None:
    """``StringId.new`` round-trips through ``as_u32``."""
    sid = StringId.new(7)
    assert sid.as_u32() == 7


def test_string_id_is_hashable_and_equal() -> None:
    """Frozen dataclass -> hashable + value equality (grok Copy+Hash+Eq)."""
    assert StringId.new(3) == StringId.new(3)
    assert hash(StringId.new(3)) == hash(StringId.new(3))
    assert StringId.new(3) != StringId.new(4)
    # Usable as a dict key (grok Hash+Eq contract).
    mapping = {StringId.new(0): "a", StringId.new(1): "b"}
    assert mapping[StringId.new(0)] == "a"


def test_string_id_is_immutable() -> None:
    """Frozen dataclass forbids field reassignment."""
    sid = StringId.new(1)
    try:
        sid.id = 99
    except (AttributeError, TypeError):
        pass
    else:  # pragma: no cover - frozen must reject mutation
        raise AssertionError("StringId should be frozen")


def test_basic_interning() -> None:
    """Same string -> same id; different strings -> different ids."""
    interner = StringInterner()
    a1 = interner.intern("hello")
    a2 = interner.intern("hello")
    assert a1 == a2
    b = interner.intern("world")
    assert a1 != b
    assert len(interner) == 2


def test_get_id() -> None:
    """``get_id`` recovers the id handed out by ``intern``."""
    interner = StringInterner()
    sid = interner.intern("foo")
    assert interner.get_id("foo") == sid
    assert interner.get_id("missing") is None


def test_bytes_interning_invalid_utf8() -> None:
    """``intern_bytes`` stores non-UTF-8 bytes; ``get`` returns None for them."""
    interner = StringInterner()
    raw = bytes([0x80, 0x81, 0x82])  # invalid UTF-8
    sid = interner.intern_bytes(raw)
    assert interner.get_bytes_id(raw) == sid
    assert interner.get_bytes(sid) == raw
    # grok ``get`` .ok()-flattens from_utf8 -> None on invalid bytes.
    assert interner.get(sid) is None


def test_get_bytes_out_of_range() -> None:
    """Out-of-range id yields None (grok Vec::get semantics)."""
    interner = StringInterner()
    interner.intern("x")
    assert interner.get_bytes(StringId.new(999)) is None
    assert interner.get(StringId.new(999)) is None
    assert interner.get_lossy(StringId.new(999)) is None


def test_get_lossy_invalid_utf8() -> None:
    """``get_lossy`` replaces invalid bytes with U+FFFD."""
    interner = StringInterner()
    raw = bytes([0x80, 0x81, 0x82])
    sid = interner.intern_bytes(raw)
    lossy = interner.get_lossy(sid)
    assert lossy is not None
    assert "�" in lossy


def test_intern_str_and_bytes_dedup() -> None:
    """``intern(s)`` and ``intern_bytes(s.encode())`` share a slot."""
    interner = StringInterner()
    s_id = interner.intern("café")
    b_id = interner.intern_bytes("café".encode())
    assert s_id == b_id
    assert len(interner) == 1


def test_many_strings() -> None:
    """10k distinct strings get distinct, dense 0-indexed ids."""
    interner = StringInterner()
    for i in range(10000):
        interner.intern(f"string_{i}")
    assert len(interner) == 10000
    assert interner.get_id("string_0") == StringId.new(0)
    assert interner.get_id("string_9999") == StringId.new(9999)


def test_from_parts_roundtrip() -> None:
    """``to_parts`` + ``from_parts`` round-trips the full state."""
    interner = StringInterner()
    for s in ("alpha", "beta", "gamma"):
        interner.intern(s)
    parts = interner.to_parts()
    restored = StringInterner.from_parts(parts)
    assert restored.to_parts() == parts
    assert len(restored) == len(interner)
    # ids preserved (id == index into parts).
    for i, s in enumerate(("alpha", "beta", "gamma")):
        assert restored.get_id(s) == StringId.new(i)


def test_clear() -> None:
    """``clear`` empties both the forward store and the reverse index."""
    interner = StringInterner()
    interner.intern("a")
    interner.intern("b")
    assert not interner.is_empty()
    interner.clear()
    assert interner.is_empty()
    assert len(interner) == 0
    assert interner.get_id("a") is None


def test_is_empty_and_len() -> None:
    """Fresh interner reports empty; after intern reports count."""
    interner = StringInterner()
    assert interner.is_empty()
    assert len(interner) == 0
    interner.intern("x")
    assert not interner.is_empty()
    assert len(interner) == 1


def test_arena_bytes() -> None:
    """``arena_bytes`` sums byte lengths across all interned strings."""
    interner = StringInterner()
    interner.intern("ab")  # 2 bytes
    interner.intern("cdef")  # 4 bytes
    assert interner.arena_bytes() == 6


def test_iter_skips_invalid_utf8() -> None:
    """``iter`` yields valid-UTF-8 strings only, in id order."""
    interner = StringInterner()
    interner.intern("first")
    interner.intern_bytes(bytes([0x80, 0x81]))  # invalid, skipped
    interner.intern("third")
    assert list(interner.iter()) == ["first", "third"]


def test_iter_bytes_includes_invalid() -> None:
    """``iter_bytes`` includes invalid-UTF-8 entries, in id order."""
    interner = StringInterner()
    interner.intern("first")
    raw = bytes([0x80, 0x81])
    interner.intern_bytes(raw)
    assert list(interner.iter_bytes()) == [b"first", raw]


def test_with_capacity_is_noop() -> None:
    """``with_capacity`` returns an empty interner (Python no-op hint)."""
    interner = StringInterner.with_capacity(1024, 64)
    assert interner.is_empty()


def test_shrink_to_fit_is_noop() -> None:
    """``shrink_to_fit`` is a no-op; interner stays consistent."""
    interner = StringInterner()
    interner.intern("keep")
    sid = interner.intern("me")
    interner.shrink_to_fit()
    assert len(interner) == 2
    assert interner.get_bytes(sid) == b"me"


def test_crate_root_barrel_exports_interner() -> None:
    """Crate root re-exports StringId + StringInterner (grok lib.rs L102)."""
    from minimax_code.xai_codebase_graph import StringId as RootStringId
    from minimax_code.xai_codebase_graph import StringInterner as RootStringInterner

    assert RootStringId is StringId
    assert RootStringInterner is StringInterner
    assert "StringId" in xcg.__all__
    assert "StringInterner" in xcg.__all__
