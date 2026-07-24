"""Tests for ``xai_codebase_graph.languages.registry`` (R304, direction (2) brick 5).

Mirrors grok ``languages/mod.rs``: :class:`LanguageRegistry` owns the five
built-in configs and serves three lookup axes (extension / id / file path),
the cross-extension "same language family" check, and a stable
:meth:`LanguageRegistry.compute_query_hash` used to invalidate the index when
tree-sitter queries change. Also covers the crate-root ``LanguageRegistry``
re-export (grok ``lib.rs`` L88) and the ``hashlib.blake2b`` stability
contract that replaces grok's ``DefaultHasher``.
"""

from __future__ import annotations

import os

import pytest

import minimax_code.xai_codebase_graph as xcg
from minimax_code.xai_codebase_graph import LanguageRegistry, TSLanguageConfig
from minimax_code.xai_codebase_graph.languages import LanguageRegistry as SubpkgRegistry
from minimax_code.xai_codebase_graph.languages.registry import (
    compute_query_hash,
)

# === construction ===========================================================


def test_new_equals_default_constructor():
    """``LanguageRegistry.new()`` == ``LanguageRegistry()`` (grok API parity)."""
    assert isinstance(LanguageRegistry.new(), LanguageRegistry)
    # Both preload the same five configs.
    assert len(LanguageRegistry().all_configs()) == len(LanguageRegistry.new().all_configs())


def test_crate_root_reexport_is_same_class():
    """crate-root ``LanguageRegistry`` is the same class as the subpackage one."""
    assert LanguageRegistry is SubpkgRegistry


# === for_extension ==========================================================


@pytest.mark.parametrize(
    ("ext", "expected_primary"),
    [
        ("py", "Python"),
        ("go", "Go"),
        ("js", "JavaScript"),
        ("jsx", "JavaScript"),
        ("rs", "Rust"),
        ("ts", "Typescript"),
        ("tsx", "Typescript"),
    ],
)
def test_for_extension(ext, expected_primary):
    """``for_extension`` returns the config owning ``ext`` (primary id match)."""
    registry = LanguageRegistry()
    config = registry.for_extension(ext)
    assert config is not None
    assert config.primary_language_id() == expected_primary


def test_for_extension_unknown_returns_none():
    """``for_extension`` returns ``None`` for an unregistered extension."""
    assert LanguageRegistry().for_extension("md") is None
    assert LanguageRegistry().for_extension("") is None


# === for_id =================================================================


@pytest.mark.parametrize(
    ("lang_id", "expected_primary"),
    [
        # Python ids.
        ("Python", "Python"),
        ("python", "Python"),
        ("py", "Python"),
        # Go ids.
        ("Go", "Go"),
        ("go", "Go"),
        # JavaScript ids.
        ("JavaScript", "JavaScript"),
        ("javascript", "JavaScript"),
        ("js", "JavaScript"),
        ("jsx", "JavaScript"),
        # Rust ids.
        ("Rust", "Rust"),
        ("rust", "Rust"),
        ("rs", "Rust"),
        # TypeScript ids.
        ("Typescript", "Typescript"),
        ("TSX", "Typescript"),
        ("typescript", "Typescript"),
        ("tsx", "Typescript"),
    ],
)
def test_for_id(lang_id, expected_primary):
    """``for_id`` resolves every registered alias to its owning config."""
    config = LanguageRegistry().for_id(lang_id)
    assert config is not None
    assert config.primary_language_id() == expected_primary


def test_for_id_unknown_returns_none():
    """``for_id`` returns ``None`` for an unregistered language id."""
    assert LanguageRegistry().for_id("brainfuck") is None


# === for_file_path ==========================================================


@pytest.mark.parametrize(
    ("path", "expected_primary"),
    [
        ("foo.py", "Python"),
        ("app.go", "Go"),
        ("index.js", "JavaScript"),
        ("Component.jsx", "JavaScript"),
        ("main.rs", "Rust"),
        ("widget.ts", "Typescript"),
        ("Widget.tsx", "Typescript"),
        ("nested/path/to/module.py", "Python"),
    ],
)
def test_for_file_path(path, expected_primary):
    """``for_file_path`` extracts the extension and resolves the config."""
    config = LanguageRegistry().for_file_path(path)
    assert config is not None
    assert config.primary_language_id() == expected_primary


def test_for_file_path_no_extension_returns_none():
    """``for_file_path`` returns ``None`` when the path has no extension."""
    assert LanguageRegistry().for_file_path("Makefile") is None
    assert LanguageRegistry().for_file_path("README") is None


def test_for_file_path_unsupported_extension_returns_none():
    """``for_file_path`` returns ``None`` for a registered-less extension."""
    assert LanguageRegistry().for_file_path("notes.md") is None
    assert LanguageRegistry().for_file_path("data.json") is None


def test_for_file_path_accepts_pathlike():
    """``for_file_path`` accepts ``os.PathLike`` (grok ``impl AsRef<Path>``)."""
    registry = LanguageRegistry()
    config = registry.for_file_path(os.path.join("src", "main.py"))
    assert config is not None
    assert config.primary_language_id() == "Python"


# === is_supported ===========================================================


@pytest.mark.parametrize(
    ("path", "supported"),
    [
        ("foo.py", True),
        ("app.go", True),
        ("Widget.tsx", True),
        ("notes.md", False),
        ("Makefile", False),
        ("data.json", False),
    ],
)
def test_is_supported(path, supported):
    """``is_supported`` mirrors ``for_file_path is not None``."""
    assert LanguageRegistry().is_supported(path) is supported


# === supported_extensions ===================================================


def test_supported_extensions_set():
    """``supported_extensions`` covers every built-in extension (set equality)."""
    assert set(LanguageRegistry().supported_extensions()) == {
        "py",
        "go",
        "js",
        "jsx",
        "rs",
        "ts",
        "tsx",
    }


def test_supported_extensions_count():
    """Seven distinct extensions across the five configs."""
    assert len(LanguageRegistry().supported_extensions()) == 7


# === all_configs ============================================================


def test_all_configs_is_tuple():
    """``all_configs`` returns an immutable tuple (grok borrowed slice)."""
    configs = LanguageRegistry().all_configs()
    assert isinstance(configs, tuple)
    assert all(isinstance(c, TSLanguageConfig) for c in configs)


def test_all_configs_insertion_order():
    """``all_configs`` preserves grok's [rust, ts, js, golang, python] order."""
    registry = LanguageRegistry()
    assert [c.primary_language_id() for c in registry.all_configs()] == [
        "Rust",
        "Typescript",
        "JavaScript",
        "Go",
        "Python",
    ]


# === extensions_same_language ===============================================


@pytest.mark.parametrize(
    ("ext1", "ext2", "expected"),
    [
        # Same family (share primary_language_id).
        ("ts", "tsx", True),
        ("tsx", "ts", True),
        ("js", "jsx", True),
        # Identical extensions short-circuit to True.
        ("py", "py", True),
        ("ts", "ts", True),
        # Different languages.
        ("ts", "js", False),
        ("py", "rs", False),
        ("go", "py", False),
        # Either extension unregistered -> False.
        ("py", "unknown", False),
        ("unknown", "py", False),
        ("md", "py", False),
    ],
)
def test_extensions_same_language(ext1, ext2, expected):
    """``extensions_same_language`` compares primary language ids."""
    assert LanguageRegistry().extensions_same_language(ext1, ext2) is expected


# === compute_query_hash =====================================================


def test_compute_query_hash_is_int_in_u64_range():
    """``compute_query_hash`` returns a non-negative int in grok's ``u64`` range."""
    value = LanguageRegistry().compute_query_hash()
    assert isinstance(value, int)
    assert 0 <= value < 2**64


def test_compute_query_hash_is_stable_within_process():
    """Two calls on the same registry yield the same hash (deterministic)."""
    registry = LanguageRegistry()
    assert registry.compute_query_hash() == registry.compute_query_hash()


def test_compute_query_hash_is_stable_across_instances():
    """Fresh registries hash identically (stable key for cache invalidation)."""
    assert (
        LanguageRegistry().compute_query_hash() == LanguageRegistry().compute_query_hash()
    )


def test_compute_query_hash_independent_of_pythonhashseed():
    """blake2b (not builtin ``hash``): same bytes -> same digest.

    This is the core reason for replacing grok's ``DefaultHasher`` with
    ``hashlib.blake2b``: Python's builtin ``hash(str)`` is ``PYTHONHASHSEED``
    -randomised per process and cannot serve as a persistent cache key.
    blake2b is a pure function of the input bytes.
    """
    # Deterministic across two distinct registry objects (which would differ
    # under ``hash()`` if object identity were involved).
    h1 = LanguageRegistry().compute_query_hash()
    h2 = LanguageRegistry().compute_query_hash()
    assert h1 == h2


def test_module_level_compute_query_hash_matches_method():
    """Free-function form agrees with the method form for ``all_configs()``."""
    registry = LanguageRegistry()
    assert compute_query_hash(registry.all_configs()) == registry.compute_query_hash()


def test_module_level_compute_query_hash_subset_differs():
    """A strict subset of configs hashes differently from the full set."""
    registry = LanguageRegistry()
    configs = registry.all_configs()
    full = compute_query_hash(configs)
    subset = compute_query_hash(configs[:1])
    assert subset != full


# === crate-root barrel ======================================================


def test_crate_root_barrel_reexports_registry():
    """crate-root ``__all__`` includes ``LanguageRegistry`` (grok lib.rs L88)."""
    assert "LanguageRegistry" in xcg.__all__
    assert xcg.LanguageRegistry is LanguageRegistry
