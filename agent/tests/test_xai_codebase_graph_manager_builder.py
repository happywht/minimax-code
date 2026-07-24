"""Black-box tests for the ``manager`` builder (R305g).

Ported from grok ``xai-codebase-graph/src/manager/builder.rs`` (507 lines, no
inline ``mod tests`` -- this suite is the sole coverage). Exercises the four
concerns the module owns:

* the ``MAX_INDEXABLE_FILE_SIZE`` constant (5 MiB, grok ``index_manager.rs:42``)
* the :class:`IndexBuildError` hierarchy (base + 3 subclasses mirroring grok's
  ``enum IndexError`` variants ``WalkError`` / ``ThreadPanic`` / ``IoError``)
* the :class:`IndexBuilder` builder-pattern config (threads / chunk / batch /
  gitignore / hidden) + the :meth:`IndexBuilder.build` entry
* the dual file-collection strategy (``git ls-files`` -> ``os.walk`` fallback)
  and the parallel ``_build_fast`` orchestration (ThreadPoolExecutor + bounded
  merge-batches), plus the single-file :func:`process_file_fast` degradation
  paths (unsupported / empty / binary / oversized).

Environment constraint: ``tree_sitter`` grammars are not installed in the test
environment, so :func:`process_file_fast` on a real ``.py`` file degrades to
``None`` (the ``_get_parser_and_query`` ``RuntimeError`` path -- confirmed via
probe). The degradation *paths* (size guard / NUL probe / unsupported) are
exercised directly on real files; the orchestration tests
(collect -> parallel parse -> sequential merge) monkeypatch
:func:`process_file_fast` to inject deterministic :class:`_FileSymbols`,
isolating the merge logic from the tree-sitter binding (which has its own
coverage in R305e). The ``force_walk`` fixture pins ``_collect_files`` to the
walk path so parent-directory git leakage cannot change which files a
``build`` sees.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import minimax_code.xai_codebase_graph as xcg_root
from minimax_code.xai_codebase_graph.languages import LanguageRegistry
from minimax_code.xai_codebase_graph.manager import (
    IndexBuilder,
    IndexBuildError,
    IndexIOError,
    IndexThreadPanic,
    IndexWalkError,
)
from minimax_code.xai_codebase_graph.manager import builder as builder_mod
from minimax_code.xai_codebase_graph.manager.builder import (
    MAX_INDEXABLE_FILE_SIZE,
    _FileSymbols,
)
from minimax_code.xai_codebase_graph.types import (
    FileMeta,
    SymbolAlias,
    SymbolOccurrence,
)

# === helpers ===============================================================


def _make_file_symbols(
    path: str,
    *,
    defs: list[tuple[str, int]] | None = None,
    refs: list[tuple[str, int]] | None = None,
    aliases: list[tuple[str, str]] | None = None,
    file_meta: FileMeta | None = None,
) -> _FileSymbols:
    """Build a fake ``_FileSymbols`` for orchestration (merge) tests."""
    return _FileSymbols(
        path=path,
        definitions=[SymbolOccurrence.new(name, line) for name, line in (defs or [])],
        references=[SymbolOccurrence.new(name, line) for name, line in (refs or [])],
        aliases=[SymbolAlias.new(a, o) for a, o in (aliases or [])],
        file_meta=file_meta or FileMeta.new(100, 1000, 0),
    )


def _fake_process_factory(fakes: dict[str, _FileSymbols | None]):
    """Return a ``process_file_fast`` replacement keyed by absolute path."""

    def fake_process(path, root_path, registry):  # noqa: ARG001 -- signature parity
        return fakes.get(str(Path(path)))

    return fake_process


@pytest.fixture
def builder() -> IndexBuilder:
    """A fresh :class:`IndexBuilder` with the default language registry."""
    return IndexBuilder()


@pytest.fixture
def force_walk(monkeypatch):  # type: ignore[no-untyped-def]
    """Pin ``_collect_files`` to the walk path (no parent-dir git leakage).

    ``git ls-files`` in a non-repo subdir still exits non-zero, but if a
    parent directory is a git repo the command succeeds and leaks the parent's
    tracked files. This fixture forces a non-zero exit so ``_collect_files``
    deterministically falls back to :meth:`IndexBuilder._collect_files_walk`.
    """
    result = subprocess.CompletedProcess(
        args=["git", "ls-files"], returncode=128, stdout="", stderr=""
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: result)


# === MAX_INDEXABLE_FILE_SIZE ===============================================


def test_max_indexable_file_size_is_five_mib() -> None:
    """grok ``index_manager.rs:42`` -> ``5 * 1024 * 1024`` == 5242880 bytes."""
    assert MAX_INDEXABLE_FILE_SIZE == 5 * 1024 * 1024
    assert MAX_INDEXABLE_FILE_SIZE == 5_242_880


# === error hierarchy =======================================================
# grok ``enum IndexError`` -> base + 3 subclasses (functional clone).


@pytest.mark.parametrize("cls", [IndexWalkError, IndexThreadPanic, IndexIOError])
def test_subclass_is_index_build_error(cls: type[Exception]) -> None:
    """grok single enum -> ``issubclass(variant, IndexBuildError)`` for all 3."""
    assert issubclass(cls, IndexBuildError)
    assert issubclass(cls, Exception)


def test_except_index_build_error_catches_every_variant() -> None:
    """grok ``Result<T, IndexError>`` -> ``except IndexBuildError`` catches all."""
    variants: list[IndexBuildError] = [
        IndexWalkError("walk-failed"),
        IndexThreadPanic("panic"),
        IndexIOError(PermissionError("io")),
    ]
    for variant in variants:
        with pytest.raises(IndexBuildError):
            raise variant


def test_except_walk_error_does_not_catch_io_error() -> None:
    """``except IndexWalkError`` targets only the walk variant (grok match arm)."""
    with pytest.raises(IndexIOError):
        try:
            raise IndexIOError(PermissionError("io"))
        except IndexWalkError:  # noqa: PERF203 -- the point: must NOT catch
            pytest.fail("IndexIOError must not be caught as IndexWalkError")


def test_walk_and_panic_errors_carry_message() -> None:
    """grok ``WalkError(String)`` / ``ThreadPanic(String)`` -> ``.message``."""
    assert IndexWalkError("walk").message == "walk"
    assert IndexThreadPanic("panic").message == "panic"
    assert str(IndexWalkError("walk")) == "walk"


def test_index_io_error_carries_cause() -> None:
    """grok ``IoError(io::Error)`` -> ``.cause`` holds the wrapped OSError."""
    err = IndexIOError(PermissionError("denied"))
    assert isinstance(err.cause, PermissionError)
    assert "denied" in str(err)


# === IndexBuilder defaults + setters =======================================


def test_builder_defaults(builder: IndexBuilder) -> None:
    """grok defaults: N-1 threads, chunk=100, batch=5000, gitignore/hidden on."""
    assert isinstance(builder.registry, LanguageRegistry)
    assert builder.num_threads >= 1
    assert builder.chunk_size == 100
    assert builder.build_batch_size == 5_000
    assert builder._respect_gitignore is True  # noqa: SLF001 -- backing field
    assert builder._skip_hidden is True  # noqa: SLF001 -- backing field


@pytest.mark.parametrize(
    ("setter", "value", "attr"),
    [
        ("with_threads", 4, "num_threads"),
        ("with_chunk_size", 50, "chunk_size"),
        ("with_build_batch_size", 1000, "build_batch_size"),
        ("respect_gitignore", False, "_respect_gitignore"),
        ("skip_hidden", False, "_skip_hidden"),
    ],
)
def test_setters_are_chainable(
    builder: IndexBuilder, setter: str, value: object, attr: str
) -> None:
    """Every builder setter returns ``self`` (grok ``#[must_use] -> Self``)."""
    method = getattr(builder, setter)
    result = method(value)  # type: ignore[operator]
    assert result is builder
    assert getattr(builder, attr) == value


def test_with_registry_injects_registry() -> None:
    """``with_registry`` classmethod swaps the language registry (grok)."""
    custom = LanguageRegistry()
    b = IndexBuilder.with_registry(custom)
    assert b.registry is custom


# === process_file_fast degradation =========================================
# No tree_sitter grammars in the test env -> a real .py parse degrades to None.
# The four paths below trip before the parse step, so they are env-independent.


def test_process_file_fast_unsupported_extension_returns_none(
    builder: IndexBuilder, tmp_path: Path
) -> None:
    """A ``.txt`` file has no language config -> ``None`` before any parse."""
    p = tmp_path / "notes.txt"
    p.write_text("hello world")
    assert (
        builder_mod.process_file_fast(str(p), str(tmp_path), builder.registry) is None
    )


def test_process_file_fast_empty_file_returns_none(
    builder: IndexBuilder, tmp_path: Path
) -> None:
    """A zero-byte ``.py`` is skipped at the size guard (grok ``size == 0``)."""
    p = tmp_path / "empty.py"
    p.write_bytes(b"")
    assert (
        builder_mod.process_file_fast(str(p), str(tmp_path), builder.registry) is None
    )


def test_process_file_fast_binary_file_returns_none(
    builder: IndexBuilder, tmp_path: Path
) -> None:
    """A NUL byte in the 8KB prefix -> binary -> ``None`` (grok NUL check)."""
    p = tmp_path / "blob.py"
    p.write_bytes(b"\x00\x01\x02\x03 not really python")
    assert (
        builder_mod.process_file_fast(str(p), str(tmp_path), builder.registry) is None
    )


def test_process_file_fast_oversized_file_returns_none(
    builder: IndexBuilder, tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """A file over ``MAX_INDEXABLE_FILE_SIZE`` -> ``None`` (grok size guard).

    Patches the module constant down to 4 bytes so a 5-byte stub trips the
    ``size > MAX`` guard without writing 5 MiB to disk.
    """
    monkeypatch.setattr(builder_mod, "MAX_INDEXABLE_FILE_SIZE", 4)
    p = tmp_path / "big.py"
    p.write_bytes(b"abcde")  # 5 bytes > patched cap of 4.
    assert (
        builder_mod.process_file_fast(str(p), str(tmp_path), builder.registry) is None
    )


def test_process_file_fast_missing_file_returns_none(
    builder: IndexBuilder, tmp_path: Path
) -> None:
    """A path that does not exist -> ``os.stat`` raises -> ``None`` (grok ``?``)."""
    missing = str(tmp_path / "ghost.py")
    assert (
        builder_mod.process_file_fast(missing, str(tmp_path), builder.registry) is None
    )


# === _collect_files_walk ===================================================


def test_collect_files_walk_finds_supported_only(
    builder: IndexBuilder, tmp_path: Path
) -> None:
    """``os.walk`` collects ``.py`` but skips unsupported ``.txt``."""
    (tmp_path / "a.py").write_text("x = 1")
    (tmp_path / "b.py").write_text("y = 2")
    (tmp_path / "notes.txt").write_text("ignore me")
    names = {Path(f).name for f in builder._collect_files_walk(str(tmp_path))}  # noqa: SLF001
    assert names == {"a.py", "b.py"}


def test_collect_files_walk_skips_hidden(
    builder: IndexBuilder, tmp_path: Path
) -> None:
    """Hidden files/dirs are pruned when ``_skip_hidden`` is True (default)."""
    (tmp_path / "visible.py").write_text("x = 1")
    (tmp_path / ".hidden.py").write_text("x = 2")
    (tmp_path / ".secret").mkdir()
    (tmp_path / ".secret" / "inner.py").write_text("x = 3")
    names = {Path(f).name for f in builder._collect_files_walk(str(tmp_path))}  # noqa: SLF001
    assert names == {"visible.py"}


def test_collect_files_walk_includes_hidden_when_disabled(
    builder: IndexBuilder, tmp_path: Path
) -> None:
    """``skip_hidden(False)`` lets hidden files through (grok toggle)."""
    builder.skip_hidden(False)
    (tmp_path / "visible.py").write_text("x = 1")
    (tmp_path / ".hidden.py").write_text("x = 2")
    names = {Path(f).name for f in builder._collect_files_walk(str(tmp_path))}  # noqa: SLF001
    assert names == {"visible.py", ".hidden.py"}


def test_collect_files_walk_prunes_dot_git(
    builder: IndexBuilder, tmp_path: Path
) -> None:
    """``.git`` is always pruned, even with hidden-skip off (grok invariant)."""
    builder.skip_hidden(False)
    (tmp_path / "visible.py").write_text("x = 1")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "inner.py").write_text("x = 2")
    (tmp_path / ".git" / "config").write_text("x = 3")
    names = {Path(f).name for f in builder._collect_files_walk(str(tmp_path))}  # noqa: SLF001
    assert names == {"visible.py"}


# === _collect_files_git ====================================================


def test_collect_files_git_parses_supported(
    builder: IndexBuilder, tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """``git ls-files`` stdout is parsed; unsupported lines filtered by ``is_supported``."""
    fake = subprocess.CompletedProcess(
        args=["git", "ls-files"],
        returncode=0,
        stdout="src/a.py\nsrc/b.py\nREADME.md\nsrc/c.txt\n",
        stderr="",
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake)
    files = builder._collect_files_git(str(tmp_path))  # noqa: SLF001
    assert [Path(f).name for f in files] == ["a.py", "b.py"]
    assert all(Path(f).is_absolute() for f in files)


def test_collect_files_git_nonzero_returncode_returns_empty(
    builder: IndexBuilder, tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """A non-zero exit (not a git repo / git unavailable) -> empty list, no raise."""
    fake = subprocess.CompletedProcess(
        args=["git", "ls-files"],
        returncode=128,
        stdout="",
        stderr="fatal: not a git repository",
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake)
    assert builder._collect_files_git(str(tmp_path)) == []  # noqa: SLF001


# === _collect_files (dual strategy) ========================================


def test_collect_files_prefers_git_when_non_empty(
    builder: IndexBuilder, tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """Non-empty git result wins; the walk fallback is not taken."""
    (tmp_path / "walk_only.py").write_text("x = 1")  # would be found by walk.
    fake = subprocess.CompletedProcess(
        args=["git", "ls-files"], returncode=0, stdout="tracked.py\n", stderr=""
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake)
    files = builder._collect_files(str(tmp_path))  # noqa: SLF001
    assert [Path(f).name for f in files] == ["tracked.py"]


def test_collect_files_falls_back_to_walk_when_git_empty(
    builder: IndexBuilder, tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """Empty git result -> walk fallback (the tmp_path tree is walked)."""
    (tmp_path / "a.py").write_text("x = 1")
    fake = subprocess.CompletedProcess(
        args=["git", "ls-files"], returncode=128, stdout="", stderr=""
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake)
    files = builder._collect_files(str(tmp_path))  # noqa: SLF001
    assert [Path(f).name for f in files] == ["a.py"]


# === build (empty workspace) ===============================================


def test_build_empty_dir_returns_stamped_index(
    builder: IndexBuilder, tmp_path: Path, force_walk: None  # noqa: ARG001
) -> None:
    """An empty workspace yields a valid index with the query-version stamp set.

    Mirrors grok: even an empty build stamps ``query_version`` so cache
    validation works (a stale-query empty index is still a cache miss).
    """
    idx = builder.build(str(tmp_path))
    assert idx.query_version.version == builder.registry.compute_query_hash()
    assert idx.alias_count() == 0
    assert not idx.has_definition("anything")


# === build orchestration (monkeypatched process_file_fast) =================


def test_build_merges_file_symbols(
    builder: IndexBuilder, tmp_path: Path, monkeypatch, force_walk: None  # noqa: ARG001
) -> None:  # type: ignore[no-untyped-def]
    """``_build_fast`` merges each ``_FileSymbols`` into one index (grok two-phase).

    Monkeypatches :func:`process_file_fast` to inject deterministic
    :class:`_FileSymbols`, isolating the merge from the tree-sitter binding.
    Verifies the four merge calls (add_definition / add_reference / add_alias /
    set_file_meta) land in the final index.
    """
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    fakes = {
        str(tmp_path / "a.py"): _make_file_symbols(
            "a.py", defs=[("foo", 1)], refs=[("foo", 5)], aliases=[("fn", "foo")]
        ),
        str(tmp_path / "b.py"): _make_file_symbols("b.py", defs=[("bar", 10)]),
    }
    monkeypatch.setattr(builder_mod, "process_file_fast", _fake_process_factory(fakes))

    idx = builder.build(str(tmp_path))
    assert sorted(idx.find_definitions("foo")) == [("a.py", 1)]
    assert sorted(idx.find_definitions("bar")) == [("b.py", 10)]
    assert sorted(idx.find_references("foo")) == [("a.py", 5)]
    assert idx.alias_count() == 1  # the fn -> foo alias from a.py.


def test_build_skips_none_results(
    builder: IndexBuilder, tmp_path: Path, monkeypatch, force_walk: None  # noqa: ARG001
) -> None:  # type: ignore[no-untyped-def]
    """Files that return ``None`` (degraded / unsupported) are skipped silently."""
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "bad.py").write_text("y = 2\n")
    fakes = {
        str(tmp_path / "a.py"): _make_file_symbols("a.py", defs=[("foo", 1)]),
        str(tmp_path / "bad.py"): None,
    }
    monkeypatch.setattr(builder_mod, "process_file_fast", _fake_process_factory(fakes))

    idx = builder.build(str(tmp_path))
    assert sorted(idx.find_definitions("foo")) == [("a.py", 1)]
    assert not idx.has_definition("bar")


def test_build_multiple_batches_merge_all(
    builder: IndexBuilder, tmp_path: Path, monkeypatch, force_walk: None  # noqa: ARG001
) -> None:  # type: ignore[no-untyped-def]
    """``build_batch_size < file count`` still merges every file (grok bounded batches).

    Sets both ``chunk_size`` and ``build_batch_size`` to 1 so each file is its
    own batch (``build_batch_size = max(build_batch_size, chunk_size)`` = 1),
    then verifies all three files' symbols land in the final index -- the batch
    loop is exhaustive, no file is dropped at a batch boundary.
    """
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text("x = 1\n")
    fakes = {
        str(tmp_path / "a.py"): _make_file_symbols("a.py", defs=[("a", 1)]),
        str(tmp_path / "b.py"): _make_file_symbols("b.py", defs=[("b", 1)]),
        str(tmp_path / "c.py"): _make_file_symbols("c.py", defs=[("c", 1)]),
    }
    monkeypatch.setattr(builder_mod, "process_file_fast", _fake_process_factory(fakes))

    builder.with_chunk_size(1).with_build_batch_size(1)
    idx = builder.build(str(tmp_path))
    for sym, fname in (("a", "a.py"), ("b", "b.py"), ("c", "c.py")):
        assert sorted(idx.find_definitions(sym)) == [(fname, 1)]


# === error paths ===========================================================


def test_build_raises_index_walk_error_on_zero_threads(
    builder: IndexBuilder, tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """``num_threads < 1`` -> :class:`IndexWalkError` (grok ThreadPool build failure).

    A genuine file must be present (with ``force_walk`` semantics) so
    ``_collect_files`` is non-empty and ``_build_fast`` is entered -- the
    empty-tree early return would bypass the thread-count guard.
    """
    (tmp_path / "a.py").write_text("x = 1\n")
    fake = subprocess.CompletedProcess(
        args=["git", "ls-files"], returncode=128, stdout="", stderr=""
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake)
    builder.with_threads(0)
    with pytest.raises(IndexWalkError):
        builder.build(str(tmp_path))


def test_build_wraps_worker_exception_as_thread_panic(
    builder: IndexBuilder, tmp_path: Path, monkeypatch, force_walk: None  # noqa: ARG001
) -> None:  # type: ignore[no-untyped-def]
    """An unexpected exception in ``process_file_fast`` -> :class:`IndexThreadPanic`.

    Mirrors grok's rayon panic capture: ``process_file_fast`` is expected to
    return ``None`` on every known failure, so a stray exception is wrapped to
    surface as the build-level ``ThreadPanic`` variant (no silent swallow).
    """
    (tmp_path / "a.py").write_text("x = 1\n")

    def boom(path, root_path, registry):  # noqa: ARG001 -- signature parity
        raise RuntimeError("unexpected worker fault")

    monkeypatch.setattr(builder_mod, "process_file_fast", boom)
    with pytest.raises(IndexThreadPanic):
        builder.build(str(tmp_path))


# === barrel contract =======================================================


def test_manager_barrel_exports_twenty_two_symbols() -> None:
    """``manager/__init__`` ``__all__`` = 12 cache + 5 builder + 5 lock = 22.

    grok ``manager/mod.rs`` re-exports 8 cache + 3 builder + 5 lock symbols
    (the builder 3rd is ``type Result<T> = Result<T, IndexError>``, no Python
    equivalent). The Python port additionally re-exports the cache (4) +
    builder (3) subclasses so the subclass-per-variant clone keeps every
    distinguishable failure mode reachable at the package surface for ``except``
    arms. The lock surface is identical to grok's: 5 symbols, no enum-variant
    subclasses to hoist (R305h added these).
    """
    from minimax_code.xai_codebase_graph import manager

    expected = {
        # cache (R305f) -- 12.
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
        # builder (R305g) -- 5.
        "IndexBuildError",
        "IndexBuilder",
        "IndexIOError",
        "IndexThreadPanic",
        "IndexWalkError",
        # lock (R305h) -- 5 (mirrors grok mod.rs verbatim).
        "IndexOperation",
        "LockResult",
        "WorkspaceLockGuard",
        "is_operation_in_progress",
        "try_lock",
    }
    assert set(manager.__all__) == expected
    assert len(manager.__all__) == 22


def test_crate_root_exports_builder_and_base() -> None:
    """grok ``lib.rs`` re-exports ``IndexBuilder`` + ``IndexError`` at crate root."""
    assert "IndexBuilder" in xcg_root.__all__
    assert "IndexBuildError" in xcg_root.__all__
    assert xcg_root.IndexBuilder is IndexBuilder
    assert xcg_root.IndexBuildError is IndexBuildError


def test_crate_root_does_not_export_builder_subclasses() -> None:
    """grok crate root re-exports only the ``IndexError`` base, not the variants.

    Subclasses stay ``manager``-subpackage-only (mirrors the cache design: the
    single enum sits at the crate root, the distinguishable failure modes live
    one layer down).
    """
    for sub in ("IndexWalkError", "IndexThreadPanic", "IndexIOError"):
        assert sub not in xcg_root.__all__
    # but the subclasses ARE importable from both the subpackage and the leaf.
    from minimax_code.xai_codebase_graph.manager import (
        IndexWalkError as from_subpackage,
    )
    from minimax_code.xai_codebase_graph.manager.builder import (
        IndexWalkError as from_leaf,
    )

    assert from_subpackage is from_leaf is IndexWalkError
