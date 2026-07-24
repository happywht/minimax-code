"""Tests for the SGIX v1 binary serialisation layer (R305d).

Ported from grok ``xai-codebase-graph/src/scope_graph/graph.rs`` ``save`` /
``load`` / ``write_to`` / ``read_from`` (L1364-L1628). Covers:

* magic-byte + version constants
* empty / definition-only / reference-only / alias-bearing / file-meta-bearing
  round-trips (``save`` -> ``load`` -> normalised state equal)
* ``query_version`` both legs (``Legacy`` ``None`` / stamped ``u64``)
* full-index round-trip rebuilding ``reverse_aliases`` / ``file_to_defs`` /
  ``file_to_refs`` and leaving ``graphs`` empty
* ``load`` legacy fallback (non-SGIX file -> ``None``)
* ``read_from`` hard errors on bad magic / unsupported version
* ``write_to`` defensive ``u32`` clamp on an externally-mutated oversized line

The comparison helper normalises every persisted + rebuilt field to pure-``int``
structures so two indexes that intern the same strings in the same order
produce equal snapshots (``StringId`` value equality + order-independent sets).
"""

from __future__ import annotations

import io

from minimax_code.xai_codebase_graph.scope_graph.index import U32_MAX, ScopeGraphIndex
from minimax_code.xai_codebase_graph.scope_graph.sgix import (
    SCOPE_GRAPH_INDEX_MAGIC,
    SCOPE_GRAPH_INDEX_VERSION,
    load,
    read_from,
    save,
    write_to,
)

# === helpers ==============================================================


def _roundtrip(idx: ScopeGraphIndex, tmp_path) -> ScopeGraphIndex:  # type: ignore[no-untyped-def]
    """Save ``idx`` to a temp file and load it back."""
    path = tmp_path / "idx.sgix"
    save(idx, path)
    return load(path)


def _normalized(idx: ScopeGraphIndex) -> dict:
    """Snapshot the persisted + rebuilt state as pure-``int`` comparable structures.

    ``StringId`` / ``FileMeta`` are value-equal frozen dataclasses, but
    normalising to raw ints makes the snapshots order-independent (sorted
    location tuples, ``frozenset`` reverse-index entries) and immune to any
    future dataclass ``__eq__`` surprises.
    """
    return {
        "interner": list(idx.interner.to_parts()),
        "definitions": {
            sid.as_u32(): tuple(sorted((p.as_u32(), line) for p, line in locs))
            for sid, locs in idx.definitions.items()
        },
        "references": {
            sid.as_u32(): tuple(sorted((p.as_u32(), line) for p, line in locs))
            for sid, locs in idx.references.items()
        },
        "aliases": {a.as_u32(): o.as_u32() for a, o in idx.aliases.items()},
        "reverse_aliases": {
            o.as_u32(): frozenset(a.as_u32() for a in aliases)
            for o, aliases in idx.reverse_aliases.items()
        },
        "file_meta": {
            p.as_u32(): (m.size, m.mtime_secs, m.mtime_nanos)
            for p, m in idx.file_meta.items()
        },
        "query_version": idx.query_version.version,
        "file_to_defs": {
            p.as_u32(): frozenset(s.as_u32() for s in syms)
            for p, syms in idx.file_to_defs.items()
        },
        "file_to_refs": {
            p.as_u32(): frozenset(s.as_u32() for s in syms)
            for p, syms in idx.file_to_refs.items()
        },
    }


# === constants ============================================================


def test_magic_and_version_constants_match_grok() -> None:
    """Magic bytes ``b"SGIX"`` + version ``1`` mirror grok L635-L638."""
    assert SCOPE_GRAPH_INDEX_MAGIC == b"SGIX"
    assert SCOPE_GRAPH_INDEX_VERSION == 1


# === empty round-trip =====================================================


def test_empty_index_roundtrip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """An empty index round-trips with all maps empty + Legacy query version."""
    idx = ScopeGraphIndex()
    loaded = _roundtrip(idx, tmp_path)
    assert _normalized(loaded) == _normalized(idx)
    # default query_version is Legacy (None).
    assert loaded.query_version.version is None
    # graphs left empty (not persisted).
    assert loaded.graphs == {}


# === field-by-field round-trips ===========================================


def test_definitions_roundtrip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Definitions survive save/load with line numbers intact."""
    idx = ScopeGraphIndex()
    idx.add_definition("foo", "a.py", 1)
    idx.add_definition("foo", "b.py", 2)
    idx.add_definition("bar", "a.py", 10)
    loaded = _roundtrip(idx, tmp_path)
    assert _normalized(loaded) == _normalized(idx)
    # functional parity via the public query API.
    assert sorted(loaded.find_definitions("foo")) == [("a.py", 1), ("b.py", 2)]
    assert loaded.find_definitions("bar") == [("a.py", 10)]


def test_references_roundtrip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """References survive save/load with line numbers intact."""
    idx = ScopeGraphIndex()
    idx.add_reference("foo", "a.py", 5)
    idx.add_reference("foo", "c.py", 9)
    loaded = _roundtrip(idx, tmp_path)
    assert _normalized(loaded) == _normalized(idx)
    assert sorted(loaded.find_references("foo")) == [("a.py", 5), ("c.py", 9)]


def test_aliases_roundtrip_rebuilds_reverse(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Aliases persist and ``reverse_aliases`` is rebuilt on load."""
    idx = ScopeGraphIndex()
    idx.add_alias("fn", "function")
    idx.add_alias("func", "function")
    loaded = _roundtrip(idx, tmp_path)
    assert _normalized(loaded) == _normalized(idx)
    # reverse_aliases rebuilt: function -> {fn, func}.
    function_id = loaded.get_id("function")
    assert function_id is not None
    assert {loaded.get_str(a) for a in loaded.reverse_aliases[function_id]} == {
        "fn",
        "func",
    }


def test_file_meta_roundtrip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """``FileMeta`` (size / mtime) survives save/load."""
    idx = ScopeGraphIndex()
    f = tmp_path / "x.py"
    f.write_text("hello")
    idx.update_file_meta(str(f))
    loaded = _roundtrip(idx, tmp_path)
    assert _normalized(loaded) == _normalized(idx)
    # the temp file is unchanged on disk -> not stale after reload.
    assert loaded.is_file_stale(str(f)) is False
    meta = loaded.get_file_meta(str(f))
    assert meta is not None
    assert meta.size == 5


# === query version ========================================================


def test_query_version_legacy_roundtrip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """An unstamped (Legacy) index round-trips as Legacy."""
    idx = ScopeGraphIndex()
    loaded = _roundtrip(idx, tmp_path)
    assert loaded.query_version.version is None
    assert loaded.needs_query_rebuild(5) is True  # Legacy always rebuilds


def test_query_version_stamped_roundtrip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A stamped ``u64`` query version survives save/load bit-for-bit."""
    idx = ScopeGraphIndex()
    idx.set_query_version(0x123456789ABCDEF0)
    loaded = _roundtrip(idx, tmp_path)
    assert loaded.query_version.version == 0x123456789ABCDEF0
    assert loaded.needs_query_rebuild(0x123456789ABCDEF0) is False
    assert loaded.needs_query_rebuild(0xDEADBEEF) is True


# === full index + reverse-index rebuild ===================================


def test_full_index_roundtrip_rebuilds_reverse_indexes_and_leaves_graphs_empty(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    """A full index round-trips; reverse indexes rebuild; graphs stay empty."""
    idx = ScopeGraphIndex()
    idx.add_definition("foo", "a.py", 1)
    idx.add_definition("foo", "b.py", 2)
    idx.add_definition("bar", "a.py", 10)
    idx.add_reference("foo", "a.py", 5)
    idx.add_reference("foo", "c.py", 9)
    idx.add_reference("bar", "a.py", 11)
    idx.add_alias("fn", "foo")
    f = tmp_path / "x.py"
    f.write_text("src")
    idx.update_file_meta(str(f))
    idx.set_query_version(42)

    loaded = _roundtrip(idx, tmp_path)

    # every persisted + rebuilt field matches.
    assert _normalized(loaded) == _normalized(idx)
    # graphs are NOT persisted -- the on-disk format leaves them empty.
    assert loaded.graphs == {}
    # file_to_defs rebuilt: a.py -> {foo, bar}; b.py -> {foo}.
    a_id = loaded.get_id("a.py")
    b_id = loaded.get_id("b.py")
    assert a_id is not None and b_id is not None
    assert {loaded.get_str(s) for s in loaded.file_to_defs[a_id]} == {"foo", "bar"}
    assert {loaded.get_str(s) for s in loaded.file_to_defs[b_id]} == {"foo"}
    # file_to_refs rebuilt: a.py -> {foo, bar}; c.py -> {foo}.
    c_id = loaded.get_id("c.py")
    assert c_id is not None
    assert {loaded.get_str(s) for s in loaded.file_to_refs[a_id]} == {"foo", "bar"}
    assert {loaded.get_str(s) for s in loaded.file_to_refs[c_id]} == {"foo"}


# === error handling =======================================================


def test_load_non_sgix_file_returns_none(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """``load`` returns ``None`` for a non-SGIX file (legacy fallback)."""
    path = tmp_path / "legacy.bin"
    path.write_bytes(b"\x00\x01\x02\x03legacy-bincode-payload")
    assert load(path) is None


def test_read_from_bad_magic_raises() -> None:
    """``read_from`` raises ``ValueError`` on a wrong magic."""
    buf = io.BytesIO(b"XXXX" + b"\x01\x00")  # bad magic + would-be version
    try:
        read_from(buf)
    except ValueError as exc:
        assert "magic" in str(exc)
    else:  # pragma: no cover - defensive: the raise above is the contract
        raise AssertionError("read_from should reject a bad magic")


def test_read_from_unsupported_version_raises() -> None:
    """``read_from`` raises ``ValueError`` on an unsupported version."""
    buf = io.BytesIO(SCOPE_GRAPH_INDEX_MAGIC + b"\x02\x00")  # version 2
    try:
        read_from(buf)
    except ValueError as exc:
        assert "version" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("read_from should reject an unsupported version")


def test_read_from_truncated_body_raises() -> None:
    """``read_from`` raises ``ValueError`` if the body is truncated mid-decode."""
    # valid header + a definitions count that promises data we never supply
    import struct

    buf = io.BytesIO(
        SCOPE_GRAPH_INDEX_MAGIC
        + struct.pack("<H", SCOPE_GRAPH_INDEX_VERSION)
        + struct.pack("<I", 0)  # arena_len = 0
        + struct.pack("<I", 0)  # num_offsets = 0
        + struct.pack("<I", 5)  # num_defs = 5, but no bytes follow
    )
    try:
        read_from(buf)
    except ValueError as exc:
        assert "EOF" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("read_from should reject a truncated body")


# === write defensive clamp ================================================


def test_write_clamps_oversized_line_defensively(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A line beyond ``u32`` is clamped on write (mirrors grok's ``u32`` type).

    The R305c runtime saturates lines on insertion, so well-formed indexes
    never hit this path; the clamp is a defensive guard for externally-mutated
    state and matches grok's ``u32`` type guarantee (no overflow, no raise).
    """
    idx = ScopeGraphIndex()
    foo_id = idx.intern("foo")
    path_id = idx.intern("a.py")
    # bypass the saturating public API to inject an oversized line directly.
    idx.definitions[foo_id] = [(path_id, U32_MAX + 100)]
    loaded = _roundtrip(idx, tmp_path)
    # the oversized line was clamped to U32_MAX on the way through write_to.
    assert loaded.find_definitions("foo") == [("a.py", U32_MAX)]


# === write_to / read_from over BytesIO (no file) ==========================


def test_write_to_read_from_roundtrip_over_bytesio() -> None:
    """``write_to`` + ``read_from`` work over an in-memory buffer (no file)."""
    idx = ScopeGraphIndex()
    idx.add_definition("foo", "a.py", 1)
    idx.add_reference("foo", "a.py", 2)
    idx.add_alias("fn", "foo")
    idx.set_query_version(7)

    buf = io.BytesIO()
    write_to(idx, buf)
    buf.seek(0)
    loaded = read_from(buf)

    assert _normalized(loaded) == _normalized(idx)
    # the first 4 emitted bytes are the magic header.
    buf.seek(0)
    assert buf.read(4) == SCOPE_GRAPH_INDEX_MAGIC
