"""Tests for the type-safe path wrappers — fusion of grok's ``xai-grok-paths`` (R44).

Mirrors grok's test matrix (``AbsPathBuf::new`` / ``contains_path`` / the lexical
normalizer on both unix and windows / ``RelPathBuf`` construct + serde round-trip
/ the ``to_relative_path`` & ``from_relative_path`` helpers / the ``ToAbsPath``
trait impls) and pins the Python mapping: the frozen newtypes, the
``ValueError``-subclass errors, the bare-string serde surface, and the lexical
normalizer's clamp-at-root semantics.

Platform split mirrors grok's ``#[cfg(unix)]`` / ``#[cfg(windows)]``: posix-shape
tests skip on Windows (``/a/b`` is drive-relative there), windows-shape tests
skip off Windows, and cross-platform tests use :func:`os.path.abspath` so an
``absolute`` input is absolute on every platform.
"""

from __future__ import annotations

import os
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path, PurePath

import pytest

from minimax_code.paths import (
    AbsPathBuf,
    AbsPathError,
    RelPathBuf,
    RelPathError,
    from_relative_path,
    normalize_lexically,
    to_abs_path,
    to_relative_path,
)

_posix_only = pytest.mark.skipif(sys.platform == "win32", reason="posix path semantics")
_win32_only = pytest.mark.skipif(sys.platform != "win32", reason="windows path semantics")


def _abs(p: str) -> str:
    """A platform-native absolute path (resolves the drive on Windows)."""
    return os.path.abspath(p)


# --- AbsPathBuf -------------------------------------------------------------


def test_abs_path_buf_new_valid():
    """AbsPathBuf::new accepts an absolute path; as_str round-trips it."""
    abs1 = AbsPathBuf.new(_abs("/home/user"))
    assert abs1.as_str() == _abs("/home/user")
    assert abs1.as_path().is_absolute()


def test_abs_path_buf_new_relative_fails():
    """AbsPathBuf::new rejects a relative path (grok test_abs_path_buf_new_relative_fails)."""
    with pytest.raises(AbsPathError) as exc:
        AbsPathBuf.new("relative/path")
    assert exc.value.input == str(PurePath("relative/path"))


def test_abs_path_buf_as_str_to_path_buf_and_fspath():
    """as_str / to_path_buf / __fspath__ all expose the same path string."""
    abs1 = AbsPathBuf.new(_abs("/x"))
    assert abs1.as_str() == str(abs1.to_path_buf())
    assert os.fspath(abs1) == abs1.as_str()


def test_abs_path_buf_join_stays_absolute():
    """join appends a segment and the result is still absolute (grok join)."""
    abs1 = AbsPathBuf.new(_abs("/a")).join("b/c")
    assert abs1.as_path().is_absolute()
    assert abs1.as_path().name == "c"


def test_abs_path_buf_value_equality_and_frozen():
    """Frozen value semantics: equal paths are equal; assignment raises."""
    a = AbsPathBuf.new(_abs("/a/b"))
    b = AbsPathBuf.new(_abs("/a/b"))
    assert a == b
    assert hash(a) == hash(b)
    assert a != AbsPathBuf.new(_abs("/a/c"))
    with pytest.raises(FrozenInstanceError):
        a._path = Path(".")  # type: ignore[misc]


@_win32_only
def test_abs_path_buf_contains_path_windows():
    """contains_path: under cwd True; exact cwd True; ``..`` escapes False."""
    cwd = AbsPathBuf.new(r"C:\proj")
    assert cwd.contains_path(AbsPathBuf.new(r"C:\proj\src"))
    assert cwd.contains_path(AbsPathBuf.new(r"C:\proj\src\main.rs"))
    assert cwd.contains_path(AbsPathBuf.new(r"C:\proj"))  # exactly cwd
    assert not cwd.contains_path(AbsPathBuf.new(r"C:\proj\.."))  # escape
    assert not cwd.contains_path(AbsPathBuf.new(r"C:\proj\..\other"))


@_posix_only
def test_abs_path_buf_contains_path_posix():
    """grok test_abs_path_buf_contains_path: escapes and round-trip normalization."""
    cwd = AbsPathBuf.new("/a/b")
    assert cwd.contains_path(AbsPathBuf.new("/a/b/c"))
    assert cwd.contains_path(AbsPathBuf.new("/a/b/c/d"))
    assert cwd.contains_path(AbsPathBuf.new("/a/b"))  # exactly cwd
    assert not cwd.contains_path(AbsPathBuf.new("/a/b/.."))  # escape
    assert not cwd.contains_path(AbsPathBuf.new("/a/b/../c"))
    # going above root and back normalizes to under cwd
    assert cwd.contains_path(AbsPathBuf.new("/a/b/../../../a/b"))
    # excessive above root still normalizes outside cwd
    assert not cwd.contains_path(AbsPathBuf.new("/a/b/../../../../../c"))


@_posix_only
def test_contains_path_does_not_normalize_the_stored_root():
    """grok: the stored root is matched literally (no normalization of self)."""
    non_normalized_root = AbsPathBuf.new("/a/b/..")
    candidate = AbsPathBuf.new("/a/c")
    assert not non_normalized_root.contains_path(candidate)


# --- normalize_lexically ----------------------------------------------------


@_posix_only
def test_normalize_lexical_dot_segments_posix():
    """grok lexical_normalize_resolves_dot_segments_without_filesystem_access."""
    assert str(normalize_lexically("/work/project/src/./nested/../main.rs")) == (
        "/work/project/src/main.rs"
    )
    assert str(normalize_lexically("../outside/./file.rs")) == "../outside/file.rs"
    assert str(normalize_lexically("src/..")) == "."
    assert str(normalize_lexically("/../../tmp")) == "/tmp"


@_win32_only
def test_normalize_lexical_preserves_windows_prefixes():
    """grok lexical_normalize_preserves_windows_prefixes."""
    assert str(normalize_lexically(r"C:\work\project\..\file.rs")) == r"C:\work\file.rs"
    assert str(normalize_lexically(r"C:\..\file.rs")) == r"C:\file.rs"
    # drive-relative (no root separator): ``..`` is preserved, not clamped.
    assert str(normalize_lexically(r"C:..\outside.rs")) == r"C:..\outside.rs"


def test_normalize_lexical_empty_to_dot():
    """An all-``.``/``..``-clamped path collapses to the dot (grok empty→``.``)."""
    assert str(normalize_lexically("src/..")) == "."
    assert str(normalize_lexically(".")) == "."


# --- RelPathBuf -------------------------------------------------------------


def test_rel_path_buf_new_valid():
    """grok test_rel_path_buf_new: a relative path constructs and round-trips.

    Compared via :class:`~pathlib.Path` equality — pathlib normalizes the
    separator to the platform default (``\\`` on Windows), so a string compare
    against the literal ``"src/main.rs"`` would be platform-fragile. grok's
    camino keeps the input bytes verbatim; Python's pathlib is the faithful
    native choice (the literal-separator serde fidelity is YAGNI until a Config
    model gains a path field).
    """
    rel = RelPathBuf.new("src/main.rs")
    assert rel.as_path() == Path("src/main.rs")


def test_rel_path_buf_new_absolute_fails():
    """grok test_rel_path_buf_new_absolute_fails (platform-native absolute input)."""
    with pytest.raises(RelPathError) as exc:
        RelPathBuf.new(_abs("/absolute/path"))
    assert exc.value.input == _abs("/absolute/path")


@_posix_only
def test_rel_path_buf_from_absolute_posix():
    """grok test_rel_path_buf_from_absolute: strip root → relative."""
    root = "/home/user/project"
    abs1 = "/home/user/project/src/main.rs"
    assert RelPathBuf.from_absolute(root, abs1).as_str() == "src/main.rs"


@_posix_only
def test_rel_path_buf_from_absolute_not_under_root_posix():
    """grok test_rel_path_buf_from_absolute_not_under_root: errors when not below root."""
    root = "/home/user/project"
    abs1 = "/other/path/file.rs"
    with pytest.raises(RelPathError):
        RelPathBuf.from_absolute(root, abs1)


def test_rel_path_buf_to_absolute_cross_platform():
    """grok test_rel_path_buf_to_absolute: join with root (platform-native root)."""
    rel = RelPathBuf.new("src/main.rs")
    root = _abs("/home/user/project")
    assert rel.to_absolute(root) == Path(root) / "src/main.rs"


def test_rel_path_buf_conversions_and_serde_roundtrip():
    """grok test_rel_path_buf_conversions + serde round-trip (from_str ↔ into_string).

    String forms are compared via :func:`str` of the same :class:`~pathlib.Path`
    so the platform separator (``\\`` on Windows) does not make the assertion
    fragile. The serde *value* round-trip (from_str ↔ into_string) is platform-
    independent; only the literal spelling of the separator is platform-specific
    (pathlib normalizes it, grok's camino does not).
    """
    rel = RelPathBuf.new("src/main.rs")
    expected = str(Path("src/main.rs"))

    # into String (grok into / serde into = "String")
    assert rel.into_string() == expected
    assert str(rel) == expected

    # try_from String (grok serde try_from = "String") — value round-trip
    rel_from_str = RelPathBuf.from_str("src/main.rs")
    assert rel_from_str == rel


def test_rel_path_buf_serde_absolute_fails():
    """grok test_rel_path_buf_serde_absolute_fails: deserializing an absolute str errors."""
    with pytest.raises(RelPathError):
        RelPathBuf.from_str(_abs("/absolute/path"))


def test_rel_path_buf_value_equality_and_frozen():
    """Frozen value semantics for RelPathBuf (mirrors AbsPathBuf)."""
    a = RelPathBuf.new("a/b")
    b = RelPathBuf.new("a/b")
    assert a == b
    assert hash(a) == hash(b)
    assert a != RelPathBuf.new("a/c")
    with pytest.raises(FrozenInstanceError):
        a._path = Path(".")  # type: ignore[misc]


# --- to_relative_path / from_relative_path ----------------------------------


@_posix_only
def test_to_relative_path_under_root_posix():
    """grok test_to_relative_path_under_root."""
    assert str(to_relative_path("/home/user/project", "/home/user/project/src/main.rs")) == (
        "src/main.rs"
    )


@_posix_only
def test_to_relative_path_not_under_root_posix():
    """grok test_to_relative_path_not_under_root: unchanged when not under root."""
    assert str(to_relative_path("/home/user/project", "/other/path/file.rs")) == (
        "/other/path/file.rs"
    )


@_posix_only
def test_to_relative_path_exact_root_posix():
    """grok test_to_relative_path_exact_root: Python yields ``.`` (Rust yields ``""``)."""
    assert to_relative_path("/home/user/project", "/home/user/project") == PurePath(".")


@_posix_only
def test_from_relative_path_relative_posix():
    """grok test_from_relative_path_relative: join root with a relative path."""
    assert str(from_relative_path("/home/user/project", "src/main.rs")) == (
        "/home/user/project/src/main.rs"
    )


@_posix_only
def test_from_relative_path_already_absolute_posix():
    """grok test_from_relative_path_already_absolute: an absolute input is returned as-is."""
    assert str(from_relative_path("/home/user/project", "/other/path/file.rs")) == (
        "/other/path/file.rs"
    )


# --- to_abs_path (grok ToAbsPath trait, six impls → one fn) -----------------


def test_to_abs_path_dispatch():
    """AbsPathBuf ignores root; RelPathBuf joins; abs str/Path ignores; rel joins."""
    root = _abs("/root")
    abs1 = AbsPathBuf.new(_abs("/other"))
    rel = RelPathBuf.new("sub/file")

    assert to_abs_path(abs1, root) == abs1.as_path()  # already absolute
    assert to_abs_path(rel, root) == Path(root) / "sub/file"  # joined
    assert to_abs_path(_abs("/other"), root) == Path(_abs("/other"))  # abs str ignores root
    assert to_abs_path("sub/file", root) == Path(root) / "sub/file"  # rel str joins
    assert to_abs_path(Path(_abs("/other")), root) == Path(_abs("/other"))  # abs Path ignores


def test_to_abs_path_str_absolute_ignores_root():
    """grok test_to_abs_path_str_absolute: an absolute str path ignores root."""
    root = _abs("/home/user")
    assert to_abs_path(_abs("/other/path"), root) == Path(_abs("/other/path"))


# --- errors carry input -----------------------------------------------------


def test_errors_carry_input_field():
    """AbsPathError / RelPathError expose the offending input (grok enum payload)."""
    with pytest.raises(AbsPathError) as exc:
        AbsPathBuf.new("rel")
    assert exc.value.input == str(PurePath("rel"))
    assert "not absolute" in str(exc.value)

    with pytest.raises(RelPathError) as exc:
        RelPathBuf.new(_abs("/abs"))
    assert exc.value.input == _abs("/abs")
    assert "not relative" in str(exc.value)
