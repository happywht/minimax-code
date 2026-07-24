"""Black-box tests for the ``manager`` cache wrapper (R305f).

Ported from grok ``xai-codebase-graph/src/manager/cache.rs`` (106 lines, no
inline ``mod tests`` -- this suite is the sole coverage). Exercises the three
concerns the module owns:

* the ``CACHE_FILE_NAME`` constant
* the :class:`CacheError` hierarchy (base + 4 subclasses mirroring grok's
  ``enum CacheError`` variants ``IoError`` / ``SerializeError`` /
  ``DeserializeError`` / ``LegacyFormat``)
* the six free functions (``get_cache_path`` / ``cache_exists`` /
  ``cache_size`` / ``load_index`` / ``save_index`` / ``save_index_async``)
  including the tri-state ``load_index`` error mapping
  (``Ok(Some)`` -> value / ``Ok(None)`` -> :class:`LegacyCacheFormat` /
  ``Err(io::Error)`` -> :class:`CacheIOError`)
* the barrel contract (``manager`` subpackage exports 12 symbols; the
  crate root re-exports the grok ``lib.rs`` L89-L93 cache subset of 8, with
  the 4 subclasses staying subpackage-only).

The ``save_index_async`` fire-and-forget semantics (grok ``std::thread::spawn``
-> Python daemon :class:`threading.Thread`) are covered via a background-write
poll plus a direct ``_save_index_worker`` error-swallow assertion (the worker
is grok's ``move ||`` thread body, so testing it synchronously is equivalent to
testing grok's error-swallow contract without a flaky thread race).
"""

from __future__ import annotations

import logging
import struct
import threading
import time

import pytest

import minimax_code.xai_codebase_graph as xcg_root
from minimax_code.xai_codebase_graph.manager import (
    CACHE_FILE_NAME,
    CacheDeserializeError,
    CacheError,
    CacheIOError,
    CacheSerializeError,
    LegacyCacheFormat,
    cache_exists,
    cache_size,
    get_cache_path,
    load_index,
    save_index,
    save_index_async,
)
from minimax_code.xai_codebase_graph.scope_graph import ScopeGraphIndex
from minimax_code.xai_codebase_graph.scope_graph.sgix import (
    SCOPE_GRAPH_INDEX_MAGIC,
    SCOPE_GRAPH_INDEX_VERSION,
)

# === helpers ===============================================================


def _make_index() -> ScopeGraphIndex:
    """Build a small but non-empty index for round-trip exercises."""
    idx = ScopeGraphIndex()
    idx.add_definition("foo", "a.py", 1)
    idx.add_reference("foo", "a.py", 5)
    idx.add_alias("fn", "foo")
    idx.set_query_version(7)
    return idx


def _join_cache_save_threads(timeout: float = 2.0) -> None:
    """Block until no live ``xcg-cache-save`` daemon threads remain."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        alive = any(
            t.name == "xcg-cache-save" and t.is_alive()
            for t in threading.enumerate()
        )
        if not alive:
            return
        time.sleep(0.005)


# === CACHE_FILE_NAME =======================================================


def test_cache_file_name_matches_grok() -> None:
    """grok ``CACHE_FILE_NAME`` -> ``".goto_index.bin"``."""
    assert CACHE_FILE_NAME == ".goto_index.bin"


# === error hierarchy =======================================================
# grok ``enum CacheError`` -> base + 4 subclasses (functional clone).


@pytest.mark.parametrize(
    "cls",
    [CacheIOError, CacheSerializeError, CacheDeserializeError, LegacyCacheFormat],
)
def test_subclass_is_cache_error(cls: type[Exception]) -> None:
    """grok single enum -> ``issubclass(variant, CacheError)`` for all 4."""
    assert issubclass(cls, CacheError)
    assert issubclass(cls, Exception)


def test_except_cache_error_catches_every_variant() -> None:
    """grok ``Result<T, CacheError>`` -> ``except CacheError`` catches all."""
    variants: list[CacheError] = [
        CacheIOError(FileNotFoundError("x")),
        CacheSerializeError("s"),
        CacheDeserializeError("d"),
        LegacyCacheFormat("l"),
    ]
    for variant in variants:
        with pytest.raises(CacheError):
            raise variant


def test_except_legacy_format_does_not_catch_io_error() -> None:
    """``except LegacyCacheFormat`` targets only the rebuild trigger (grok match arm)."""
    with pytest.raises(CacheIOError):
        try:
            raise CacheIOError(FileNotFoundError("io"))
        except LegacyCacheFormat:  # noqa: PERF203 -- the point: must NOT catch
            pytest.fail("CacheIOError must not be caught as LegacyCacheFormat")


def test_cache_io_error_carries_cause() -> None:
    """grok ``IoError(io::Error)`` -> ``.cause`` holds the wrapped OSError."""
    err = CacheIOError(FileNotFoundError("boom"))
    assert isinstance(err.cause, FileNotFoundError)
    assert "boom" in str(err)


def test_serialize_deserialize_errors_carry_message() -> None:
    """grok ``SerializeError(String)`` / ``DeserializeError(String)`` -> ``.message``."""
    assert CacheSerializeError("ser-failed").message == "ser-failed"
    assert CacheDeserializeError("de-failed").message == "de-failed"
    assert str(CacheSerializeError("ser-failed")) == "ser-failed"


# === get_cache_path ========================================================


def test_get_cache_path_joins_root_with_cache_name(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """grok ``root_path.join(CACHE_FILE_NAME)`` -> ``Path(root) / CACHE_FILE_NAME``."""
    p = get_cache_path(tmp_path)
    assert p.name == CACHE_FILE_NAME
    assert p.parent == tmp_path


def test_get_cache_path_accepts_str_root() -> None:
    """A bare ``str`` root is coerced to ``Path`` (grok ``&str`` / ``PathBuf``)."""
    p = get_cache_path("/tmp/whatever")
    assert p.name == CACHE_FILE_NAME
    assert p.as_posix().endswith("/tmp/whatever/.goto_index.bin")


# === cache_exists / cache_size =============================================


def test_cache_exists_true_after_save(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """grok ``cache_path.exists()`` -> ``True`` once a file lands."""
    p = get_cache_path(tmp_path)
    assert cache_exists(p) is False
    save_index(p, _make_index())
    assert cache_exists(p) is True


def test_cache_size_returns_int_or_none(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """grok ``Option<u64>`` from ``metadata().len()`` -> ``int`` or ``None``."""
    p = get_cache_path(tmp_path)
    assert cache_size(p) is None  # absent
    save_index(p, _make_index())
    size = cache_size(p)
    assert isinstance(size, int)
    assert size > 0


# === load_index ============================================================
# grok ``load_index`` tri-state: Ok(Some) -> value / Ok(None) -> Legacy / Err -> Io.


def test_load_index_missing_file_raises_io_error(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """grok NotFound probe -> :class:`CacheIOError` wrapping FileNotFoundError."""
    p = get_cache_path(tmp_path)
    with pytest.raises(CacheIOError) as ei:
        load_index(p)
    assert isinstance(ei.value.cause, FileNotFoundError)


def test_load_index_legacy_magic_raises_legacy_format(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Non-SGIX file -> sgix.load returns ``None`` -> :class:`LegacyCacheFormat`."""
    p = tmp_path / "legacy.bin"
    p.write_bytes(b"\x00\x01\x02\x03legacy-bincode-payload")
    with pytest.raises(LegacyCacheFormat):
        load_index(p)


def test_load_index_truncated_body_raises_io_error(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Valid SGIX header + truncated body -> read_from ``ValueError`` -> :class:`CacheIOError`.

    Mirrors grok funneling every decode error through ``?`` into ``io::Error``
    (``InvalidData``) -> ``CacheError::IoError``. The Python port catches the
    ``ValueError`` raised by :func:`scope_graph.sgix.read_from` so no raw
    non-:class:`CacheError` leaks from :func:`load_index`.
    """
    p = tmp_path / "trunc.bin"
    p.write_bytes(
        SCOPE_GRAPH_INDEX_MAGIC
        + struct.pack("<H", SCOPE_GRAPH_INDEX_VERSION)
        + struct.pack("<I", 0)  # arena_len = 0
        + struct.pack("<I", 0)  # num_offsets = 0
        + struct.pack("<I", 5)  # num_defs = 5 promised, but no body follows
    )
    with pytest.raises(CacheIOError):
        load_index(p)


def test_load_index_roundtrip_returns_value_equal_index(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """``save_index`` then ``load_index`` returns a query-equivalent index."""
    p = get_cache_path(tmp_path)
    save_index(p, _make_index())
    loaded = load_index(p)
    assert sorted(loaded.find_definitions("foo")) == [("a.py", 1)]
    assert sorted(loaded.find_references("foo")) == [("a.py", 5)]
    assert loaded.query_version.version == 7


# === save_index ============================================================


def test_save_index_writes_sgix_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """``save_index`` writes a file whose first 4 bytes are the SGIX magic."""
    p = get_cache_path(tmp_path)
    save_index(p, _make_index())
    assert p.exists()
    assert p.read_bytes()[:4] == SCOPE_GRAPH_INDEX_MAGIC


def test_save_index_wraps_os_error_as_io_error(
    tmp_path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """grok ``.map_err(CacheError::IoError)`` -> OSError from sgix.save wrapped."""
    from minimax_code.xai_codebase_graph.manager import cache as cache_mod

    def boom(*args: object, **kwargs: object) -> None:
        raise PermissionError("nope")

    monkeypatch.setattr(cache_mod, "_sgix_save", boom)
    with pytest.raises(CacheIOError) as ei:
        save_index(get_cache_path(tmp_path), _make_index())
    assert isinstance(ei.value.cause, PermissionError)


# === save_index_async ======================================================
# grok ``std::thread::spawn`` fire-and-forget -> Python daemon thread.


def test_save_index_async_writes_in_background(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The daemon thread performs the write; the caller returns immediately."""
    p = get_cache_path(tmp_path)
    save_index_async(p, _make_index())
    _join_cache_save_threads()
    assert cache_exists(p)
    # the background-written file is a valid SGIX index loadable back.
    loaded = load_index(p)
    assert sorted(loaded.find_definitions("foo")) == [("a.py", 1)]


def test_save_index_worker_swallows_cache_error_and_logs(
    tmp_path, monkeypatch, caplog
) -> None:  # type: ignore[no-untyped-def]
    """grok ``move ||`` body swallows the error + logs a warning (fire-and-forget).

    Tests the worker synchronously (it *is* grok's thread body) to avoid a
    flaky cross-thread ``caplog`` race; :func:`save_index_async` only adds the
    thread spawn, which is covered by the background-write test above.
    """
    from minimax_code.xai_codebase_graph.manager import cache as cache_mod

    def boom(*args: object, **kwargs: object) -> None:
        raise CacheIOError(FileNotFoundError("denied"))

    monkeypatch.setattr(cache_mod, "save_index", boom)
    with caplog.at_level(logging.WARNING, logger=cache_mod.__name__):
        # the worker must NOT re-raise -- grok's fire-and-forget contract.
        cache_mod._save_index_worker(get_cache_path(tmp_path), _make_index())
    assert any(
        "Failed to save index cache" in record.getMessage()
        for record in caplog.records
    )


# === barrel contract =======================================================


def test_manager_barrel_exports_twelve_symbols() -> None:
    """``manager/__init__`` ``__all__`` = constant + 5 error classes + 6 functions = 12.

    grok ``manager/mod.rs`` re-exports 8 cache symbols (constant + ``CacheError``
    + 6 functions). The Python port additionally re-exports the 4 ``CacheError``
    subclasses because the subclass-per-variant clone needs them reachable at
    the package surface for ``except LegacyCacheFormat`` arms.
    """
    from minimax_code.xai_codebase_graph import manager

    expected = {
        "CACHE_FILE_NAME",
        "CacheDeserializeError",
        "CacheError",
        "CacheIOError",
        "CacheSerializeError",
        "LegacyCacheFormat",
        "cache_exists",
        "cache_size",
        "get_cache_path",
        "load_index",
        "save_index",
        "save_index_async",
    }
    assert set(manager.__all__) == expected
    assert len(manager.__all__) == 12


def test_crate_root_barrel_exports_cache_subset() -> None:
    """grok ``lib.rs`` L89-L93 re-exports the 8-symbol cache subset at crate root."""
    for sym in (
        "CACHE_FILE_NAME",
        "CacheError",
        "cache_exists",
        "cache_size",
        "get_cache_path",
        "load_index",
        "save_index",
        "save_index_async",
    ):
        assert sym in xcg_root.__all__, f"{sym} missing from crate-root barrel"
    # the crate-root symbol is the same object as the manager leaf symbol.
    assert xcg_root.load_index is load_index
    assert xcg_root.CacheError is CacheError


def test_crate_root_does_not_export_subclasses() -> None:
    """grok crate root re-exports only the ``CacheError`` base, not the variants.

    Subclasses stay ``manager``-subpackage-only (mirrors grok's single enum at
    the crate root); ``except LegacyCacheFormat`` arms import from the
    subpackage or the ``cache`` leaf directly.
    """
    for sub in (
        "CacheIOError",
        "CacheSerializeError",
        "CacheDeserializeError",
        "LegacyCacheFormat",
    ):
        assert sub not in xcg_root.__all__
    # but the subclasses ARE importable from both the subpackage and the leaf.
    from minimax_code.xai_codebase_graph.manager import (
        LegacyCacheFormat as from_subpackage,
    )
    from minimax_code.xai_codebase_graph.manager.cache import (
        LegacyCacheFormat as from_leaf,
    )

    assert from_subpackage is from_leaf is LegacyCacheFormat
