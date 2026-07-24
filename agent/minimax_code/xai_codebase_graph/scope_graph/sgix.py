"""SGIX v1 binary serialisation for :class:`ScopeGraphIndex` (R305d).

Ported from grok ``xai-codebase-graph/src/scope_graph/graph.rs`` -- the
``save`` / ``load`` / ``write_to`` / ``read_from`` quartet at L1364-L1628. The
on-disk format is a custom little-endian binary envelope with magic-byte
detection and a version guard::

    magic            b"SGIX"                 (4 bytes)
    version          u16 LE  (= 1)           (2 bytes)
    interner arena   u32 LE len + bytes
    interner offsets u32 LE count + N x (start: u32 LE, len: u16 LE)
    definitions      u32 LE count + N x (symbol_id: u32 LE,
                      loc_count: u32 LE, [path_id: u32 LE, line: u32 LE] ...)
    references       (same layout as definitions)
    aliases          u32 LE count + N x (alias_id: u32 LE, original_id: u32 LE)
    file_meta        u32 LE count + N x (path_id: u32 LE, size: u64 LE,
                      mtime_secs: i64 LE, mtime_nanos: u32 LE)
    query_version    1-byte tag (0 = Legacy | 1 = Version(u64 LE))

Not persisted -- rebuilt on load, mirroring grok ``read_from`` L1601-L1628:

* ``graphs`` -- left empty (rebuilt on demand from source by the manager layer).
* ``reverse_aliases`` -- rebuilt inline as aliases are decoded.
* ``file_to_defs`` / ``file_to_refs`` -- rebuilt from definitions / references.

Adapter note: grok's ``StringInterner`` exposes ``arena()`` / ``offsets()`` /
``from_parts(arena, offsets)`` because its in-memory store *is* a contiguous
``Vec<u8>`` arena keyed by ``(start, len)`` offsets. The Python port (R302)
uses an idiomatic ``list[bytes]`` store exposed via ``to_parts()`` /
``from_parts()``. This module bridges the two shapes on the serialisation
seam: ``_interner_to_arena`` flattens ``list[bytes]`` into a contiguous arena
plus ``(start, len)`` offsets on write, and ``_interner_from_arena`` slices
the arena back into ``list[bytes]`` on read. The on-disk bytes therefore match
grok's layout exactly while the in-memory representation stays Pythonic -- a
functional clone, not a line-by-line clone.
"""

from __future__ import annotations

import struct
from os import PathLike
from typing import BinaryIO

from minimax_code.xai_codebase_graph.interner import StringId, StringInterner
from minimax_code.xai_codebase_graph.scope_graph.graph import QueryVersion
from minimax_code.xai_codebase_graph.scope_graph.index import U32_MAX, ScopeGraphIndex
from minimax_code.xai_codebase_graph.types import FileMeta

# Magic bytes + version for the SGIX binary format (mirrors grok L635-L638).
SCOPE_GRAPH_INDEX_MAGIC: bytes = b"SGIX"
SCOPE_GRAPH_INDEX_VERSION: int = 1

# Pre-compiled little-endian struct codecs (all grok ``to_le_bytes`` equivalents).
_U16 = struct.Struct("<H")  # version
_U32 = struct.Struct("<I")  # lengths / ids / line / mtime_nanos
_U64 = struct.Struct("<Q")  # file size / query hash
_I64 = struct.Struct("<q")  # mtime_secs (signed -- matches grok i64)
# (path_id: u32, line: u32) -- one definition / reference location.
_LOC = struct.Struct("<II")
# (start: u32, len: u16) -- one interner offset entry.
_OFFSET = struct.Struct("<IH")
# (alias_id: u32, original_id: u32) -- one alias pair.
_ALIAS = struct.Struct("<II")


# === interner <-> arena adapter ===========================================


def _interner_to_arena(interner: StringInterner) -> tuple[bytes, list[tuple[int, int]]]:
    """Flatten the interner's ``list[bytes]`` store into a contiguous arena.

    Mirrors grok ``StringInterner::arena()`` + ``offsets()``: every interned
    byte string is concatenated into one arena, and each id maps to
    ``(start, len)`` where ``start`` is the byte offset and ``len`` the byte
    length. grok stores ``len`` as ``u16``, so entries longer than 65535 bytes
    cannot round-trip -- matching grok's own ``u16`` length limitation (the
    on-disk contract is identical).
    """
    arena = bytearray()
    offsets: list[tuple[int, int]] = []
    for raw in interner.to_parts():
        start = len(arena)
        arena.extend(raw)
        offsets.append((start, len(raw)))
    return bytes(arena), offsets


def _interner_from_arena(
    arena: bytes, offsets: list[tuple[int, int]]
) -> StringInterner:
    """Rebuild an interner by slicing ``arena`` per ``(start, len)`` offset.

    Mirrors grok ``StringInterner::from_parts(arena, offsets)`` (which rebuilds
    the reverse lookup by re-hashing each arena slice). The Python interner
    rebuilds its ``dict[bytes, StringId]`` reverse index inside ``from_parts``.
    """
    strings = [arena[start : start + length] for start, length in offsets]
    return StringInterner.from_parts(strings)


# === read / write primitives ==============================================


def _read_exact(reader: BinaryIO, n: int) -> bytes:
    """Read exactly ``n`` bytes or raise (mirrors grok ``Read::read_exact``).

    grok's ``read_exact`` returns ``io::Error`` on early EOF; the Python
    counterpart raises ``ValueError`` so callers see a clear format error
    rather than silently decoding a short buffer.
    """
    buf = reader.read(n)
    if buf is None or len(buf) < n:
        raise ValueError(f"unexpected EOF reading {n} bytes")
    return buf


# === write_to / read_from =================================================


def write_to(index: ScopeGraphIndex, writer: BinaryIO) -> None:
    """Encode ``index`` to ``writer`` in SGIX v1 binary format.

    Mirrors grok ``ScopeGraphIndex::write_to`` (L1392-L1460): header (magic +
    version), interner (arena + offsets), definitions, references, aliases,
    file_meta, query_version. ``graphs`` are not serialised (rebuilt on demand
    from source). ``line`` values are clamped to the ``u32`` range to mirror
    grok's ``u32`` type guarantee -- the R305c runtime invariant already
    saturates lines on insertion, so this clamp is a no-op for well-formed
    indexes and a defensive guard against externally-mutated state.
    """
    # Header.
    writer.write(SCOPE_GRAPH_INDEX_MAGIC)
    writer.write(_U16.pack(SCOPE_GRAPH_INDEX_VERSION))

    # Interner arena + offsets.
    arena, offsets = _interner_to_arena(index.interner)
    writer.write(_U32.pack(len(arena)))
    writer.write(arena)
    writer.write(_U32.pack(len(offsets)))
    for start, length in offsets:
        writer.write(_OFFSET.pack(start, length))

    # Definitions.
    writer.write(_U32.pack(len(index.definitions)))
    for symbol_id, locations in index.definitions.items():
        writer.write(_U32.pack(symbol_id.as_u32()))
        writer.write(_U32.pack(len(locations)))
        for path_id, line in locations:
            writer.write(_LOC.pack(path_id.as_u32(), min(int(line), U32_MAX)))

    # References (same layout as definitions).
    writer.write(_U32.pack(len(index.references)))
    for symbol_id, locations in index.references.items():
        writer.write(_U32.pack(symbol_id.as_u32()))
        writer.write(_U32.pack(len(locations)))
        for path_id, line in locations:
            writer.write(_LOC.pack(path_id.as_u32(), min(int(line), U32_MAX)))

    # Aliases.
    writer.write(_U32.pack(len(index.aliases)))
    for alias_id, original_id in index.aliases.items():
        writer.write(_ALIAS.pack(alias_id.as_u32(), original_id.as_u32()))

    # File metadata.
    writer.write(_U32.pack(len(index.file_meta)))
    for path_id, meta in index.file_meta.items():
        writer.write(_U32.pack(path_id.as_u32()))
        writer.write(_U64.pack(meta.size))
        writer.write(_I64.pack(meta.mtime_secs))
        writer.write(_U32.pack(meta.mtime_nanos))

    # Query version (tagged: 0 = Legacy, 1 = Version(u64)).
    if index.query_version.version is None:
        writer.write(b"\x00")
    else:
        writer.write(b"\x01")
        writer.write(_U64.pack(index.query_version.version))

    # graphs intentionally not serialised (rebuilt on demand from source).


def read_from(reader: BinaryIO) -> ScopeGraphIndex:
    """Decode an SGIX v1 index from ``reader``.

    Mirrors grok ``ScopeGraphIndex::read_from`` (L1463-L1628): validates the
    magic + version, decodes the interner (arena + offsets), definitions,
    references, aliases (rebuilding ``reverse_aliases`` inline), file_meta, and
    the EOF-tolerant query_version tag. ``file_to_defs`` / ``file_to_refs`` are
    rebuilt from the decoded definitions / references (L1601-L1616); ``graphs``
    is left empty (L1620).

    Raises ``ValueError`` on a bad magic, an unsupported version, or a truncated
    body (mirrors grok's ``io::Error(InvalidData, ...)`` returns).
    """
    # Header.
    magic = _read_exact(reader, 4)
    if magic != SCOPE_GRAPH_INDEX_MAGIC:
        raise ValueError("invalid SGIX magic")
    version = _U16.unpack(_read_exact(reader, 2))[0]
    if version != SCOPE_GRAPH_INDEX_VERSION:
        raise ValueError(f"unsupported SGIX version: {version}")

    # Interner arena + offsets.
    arena_len = _U32.unpack(_read_exact(reader, 4))[0]
    arena = _read_exact(reader, arena_len)
    num_offsets = _U32.unpack(_read_exact(reader, 4))[0]
    offsets: list[tuple[int, int]] = []
    for _ in range(num_offsets):
        start, length = _OFFSET.unpack(_read_exact(reader, 6))
        offsets.append((start, length))
    interner = _interner_from_arena(arena, offsets)

    # Definitions.
    definitions: dict[StringId, list[tuple[StringId, int]]] = {}
    num_defs = _U32.unpack(_read_exact(reader, 4))[0]
    for _ in range(num_defs):
        symbol_id = StringId.new(_U32.unpack(_read_exact(reader, 4))[0])
        num_locations = _U32.unpack(_read_exact(reader, 4))[0]
        locations: list[tuple[StringId, int]] = []
        for _ in range(num_locations):
            path_id, line = _LOC.unpack(_read_exact(reader, 8))
            locations.append((StringId.new(path_id), line))
        definitions[symbol_id] = locations

    # References.
    references: dict[StringId, list[tuple[StringId, int]]] = {}
    num_refs = _U32.unpack(_read_exact(reader, 4))[0]
    for _ in range(num_refs):
        symbol_id = StringId.new(_U32.unpack(_read_exact(reader, 4))[0])
        num_locations = _U32.unpack(_read_exact(reader, 4))[0]
        locations = []
        for _ in range(num_locations):
            path_id, line = _LOC.unpack(_read_exact(reader, 8))
            locations.append((StringId.new(path_id), line))
        references[symbol_id] = locations

    # Aliases + reverse_aliases (rebuilt inline, mirrors grok L1551-L1561).
    aliases: dict[StringId, StringId] = {}
    reverse_aliases: dict[StringId, set[StringId]] = {}
    num_aliases = _U32.unpack(_read_exact(reader, 4))[0]
    for _ in range(num_aliases):
        alias_id_raw, original_id_raw = _ALIAS.unpack(_read_exact(reader, 8))
        alias_id = StringId.new(alias_id_raw)
        original_id = StringId.new(original_id_raw)
        aliases[alias_id] = original_id
        reverse_aliases.setdefault(original_id, set()).add(alias_id)

    # File metadata.
    file_meta: dict[StringId, FileMeta] = {}
    num_files = _U32.unpack(_read_exact(reader, 4))[0]
    for _ in range(num_files):
        path_id = _U32.unpack(_read_exact(reader, 4))[0]
        size = _U64.unpack(_read_exact(reader, 8))[0]
        mtime_secs = _I64.unpack(_read_exact(reader, 8))[0]
        mtime_nanos = _U32.unpack(_read_exact(reader, 4))[0]
        file_meta[StringId.new(path_id)] = FileMeta.new(size, mtime_secs, mtime_nanos)

    # Query version (EOF-tolerant: missing / unknown tag -> Legacy).
    tag = reader.read(1)
    if tag == b"\x01":
        # tag == 1: a u64 hash must follow; a short read raises (mirrors grok `?`).
        query_version = QueryVersion(version=_U64.unpack(_read_exact(reader, 8))[0])
    else:
        # tag == b"\x00" (Legacy), b"" (EOF), or unknown -> Legacy.
        query_version = QueryVersion()

    # Rebuild file_to_defs / file_to_refs from the decoded maps (grok L1601-L1616).
    file_to_defs: dict[StringId, set[StringId]] = {}
    for symbol_id, locations in definitions.items():
        for path_id, _line in locations:
            file_to_defs.setdefault(path_id, set()).add(symbol_id)
    file_to_refs: dict[StringId, set[StringId]] = {}
    for symbol_id, locations in references.items():
        for path_id, _line in locations:
            file_to_refs.setdefault(path_id, set()).add(symbol_id)

    index = ScopeGraphIndex()
    index.interner = interner
    index.definitions = definitions
    index.references = references
    index.aliases = aliases
    index.reverse_aliases = reverse_aliases
    index.file_meta = file_meta
    index.query_version = query_version
    index.file_to_defs = file_to_defs
    index.file_to_refs = file_to_refs
    # graphs left empty (rebuilt on demand from source -- grok L1620).
    return index


# === save / load (file convenience) =======================================


def save(index: ScopeGraphIndex, path: str | PathLike[str]) -> None:
    """Persist ``index`` to ``path`` in SGIX binary format.

    Mirrors grok ``ScopeGraphIndex::save`` (L1364-L1369): open the file for
    writing, buffer, ``write_to``, flush. The ``with`` block handles the flush
    + close on the Python side.
    """
    with open(path, "wb") as f:
        write_to(index, f)


def load(path: str | PathLike[str]) -> ScopeGraphIndex | None:
    """Load an index from ``path``; ``None`` if the format is unrecognized.

    Mirrors grok ``ScopeGraphIndex::load`` (L1372-L1389): peek the magic bytes
    and return ``None`` for non-SGIX files (legacy fallback so callers can
    gracefully fall back to a rebuild), else rewind and decode the full index.
    """
    with open(path, "rb") as f:
        magic = f.read(4)
        if magic != SCOPE_GRAPH_INDEX_MAGIC:
            return None
        f.seek(0)
        return read_from(f)


__all__ = [
    "SCOPE_GRAPH_INDEX_MAGIC",
    "SCOPE_GRAPH_INDEX_VERSION",
    "load",
    "read_from",
    "save",
    "write_to",
]
