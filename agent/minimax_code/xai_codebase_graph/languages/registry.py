"""Language registry (direction (2) brick 5).

Ported from grok ``xai-codebase-graph/src/languages/mod.rs``. The
:class:`LanguageRegistry` owns the five supported language configs and serves
three lookup axes -- by file extension, by language id, and by file path --
plus the cross-extension "same language family" check and the query-content
hash used to invalidate the index when queries change.

Migration decisions (clone-by-function, not line-by-line):

* ``Arc<TSLanguageConfig>`` shared ownership -> direct Python references.
  :class:`~minimax_code.xai_codebase_graph.languages.types.TSLanguageConfig`
  is frozen (R303), so the two reverse maps store direct references and the
  accessors return the config itself (no ``Arc`` wrapper needed).
* ``DefaultHasher`` (Rust SipHash-1-3) -> ``hashlib.blake2b(digest_size=8)``.
  Python's builtin ``hash()`` is process-randomised (``PYTHONHASHSEED``) and
  unsuitable as a persistent cache-invalidation key; ``blake2b`` is stable
  across processes / machines / interpreter runs, and its 8-byte digest maps
  cleanly onto grok's ``u64`` return type. Each (id, query) field is fed with
  a length-prefix mirroring Rust's ``str::hash`` (which hashes the length
  then the bytes), preserving grok's anti-collision contract.
* ``impl AsRef<Path>`` path acceptance -> ``str | os.PathLike[str]`` resolved
  via :class:`pathlib.PurePath` (no filesystem IO; grok's ``Path::extension``
  is pure string parsing).
* ``Path::extension()`` returns the bare extension -> ``PurePath.suffix`` with
  the leading dot stripped (grok stores ``"py"`` not ``".py"``).

Public surface mirrors grok ``lib.rs`` L88
``pub use languages::{LanguageRegistry, TSLanguageConfig};`` --
``LanguageRegistry`` is re-exported at the crate root (see
``xai_codebase_graph/__init__.py``).
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from pathlib import PurePath

from minimax_code.xai_codebase_graph.languages.golang import golang
from minimax_code.xai_codebase_graph.languages.javascript import js_lang
from minimax_code.xai_codebase_graph.languages.python import python_lang
from minimax_code.xai_codebase_graph.languages.rust import rust_lang
from minimax_code.xai_codebase_graph.languages.types import TSLanguageConfig
from minimax_code.xai_codebase_graph.languages.typescript import ts_lang

#: Insertion order of the five built-in configs (grok ``LanguageRegistry::new``).
#: grok builds ``[rust, ts, js, golang, python]``; :meth:`all_configs` returns
#: them in this order.
_BUILTIN_CONFIGS: tuple[TSLanguageConfig, ...] = (
    rust_lang(),
    ts_lang(),
    js_lang(),
    golang(),
    python_lang(),
)


class LanguageRegistry:
    """Registry of all supported languages (grok ``LanguageRegistry``).

    Provides lookup by extension and language ID, file-path driven lookup,
    a same-language-family check across two extensions, and a stable hash of
    every language's definitions query (used to trigger an index rebuild when
    queries change even if file contents have not).
    """

    __slots__ = ("_configs", "_by_extension", "_by_id")

    def __init__(self) -> None:
        """Create a registry preloaded with all supported languages.

        Mirrors grok ``LanguageRegistry::new`` / ``Default::default``: the five
        built-in configs are indexed by every extension and every language id
        they own.
        """
        by_extension: dict[str, TSLanguageConfig] = {}
        by_id: dict[str, TSLanguageConfig] = {}
        for config in _BUILTIN_CONFIGS:
            for ext in config.file_extensions():
                by_extension[ext] = config
            for lang_id in config.language_ids():
                by_id[lang_id] = config
        self._configs: tuple[TSLanguageConfig, ...] = _BUILTIN_CONFIGS
        self._by_extension = by_extension
        self._by_id = by_id

    @classmethod
    def new(cls) -> LanguageRegistry:
        """Create a registry with all supported languages (grok ``new``).

        Equivalent to ``LanguageRegistry()``; kept for grok API parity.
        """
        return cls()

    def for_extension(self, ext: str) -> TSLanguageConfig | None:
        """Get a language config by file extension (grok ``for_extension``).

        Returns ``None`` if no registered language owns ``ext``.
        """
        return self._by_extension.get(ext)

    def for_id(self, id: str) -> TSLanguageConfig | None:
        """Get a language config by language ID (grok ``for_id``).

        Returns ``None`` if ``id`` is not a registered language id.
        """
        return self._by_id.get(id)

    def for_file_path(self, path: str | os.PathLike[str]) -> TSLanguageConfig | None:
        """Get a language config for a file path (grok ``for_file_path``).

        Extracts the extension from ``path`` (without the leading dot, matching
        Rust ``Path::extension``) and looks up the config. Returns ``None`` if
        the path has no extension or the extension is unsupported.
        """
        suffix = PurePath(os.fspath(path)).suffix
        if not suffix:
            return None
        return self.for_extension(suffix.removeprefix("."))

    def is_supported(self, path: str | os.PathLike[str]) -> bool:
        """Check if ``path`` has a supported extension (grok ``is_supported``)."""
        return self.for_file_path(path) is not None

    def supported_extensions(self) -> list[str]:
        """All supported file extensions (grok ``supported_extensions``).

        Order is insertion order (rust -> ts -> js -> golang -> python); grok
        returns ``HashMap`` keys in unspecified order, so callers must not rely
        on ordering.
        """
        return list(self._by_extension.keys())

    def all_configs(self) -> tuple[TSLanguageConfig, ...]:
        """All registered language configs (grok ``all_configs``).

        Returned as an immutable tuple in insertion order (rust, ts, js,
        golang, python) in place of grok's borrowed ``&[Arc<TSLanguageConfig>]``
        slice.
        """
        return self._configs

    def extensions_same_language(self, ext1: str, ext2: str) -> bool:
        """Check if two extensions belong to the same language (grok method).

        Returns ``True`` if both extensions are registered under configs that
        share the same primary language id. Identical extensions short-circuit
        to ``True``; either extension being unregistered returns ``False``.
        """
        if ext1 == ext2:
            return True
        config1 = self._by_extension.get(ext1)
        config2 = self._by_extension.get(ext2)
        if config1 is None or config2 is None:
            return False
        return config1.primary_language_id() == config2.primary_language_id()

    def compute_query_hash(self) -> int:
        """Stable 64-bit hash of all tree-sitter queries (grok ``compute_query_hash``).

        grok feeds each language's primary id + definitions query into a
        ``DefaultHasher`` (SipHash-1-3) -> ``u64``. This port uses
        ``hashlib.blake2b(digest_size=8)``: Python's builtin ``hash()`` is
        ``PYTHONHASHSEED``-randomised per process and cannot serve as a
        persistent cache-invalidation key, whereas ``blake2b`` is stable across
        processes / machines / runs. The 8-byte digest maps onto grok's
        ``u64`` range.

        Configs are sorted by primary language id for deterministic ordering
        (mirrors grok's ``sort_by_key``). Each field is fed with an 8-byte
        little-endian length prefix (mirrors Rust's ``str::hash``: length then
        bytes), preserving grok's anti-collision contract.
        """
        sorted_configs = sorted(self._configs, key=lambda c: c.primary_language_id())
        hasher = hashlib.blake2b(digest_size=8)
        for config in sorted_configs:
            lang_id = config.primary_language_id().encode("utf-8")
            query = config.file_definition_queries().encode("utf-8")
            # Length-prefix each field (Rust str::hash: length then bytes) so
            # concatenated fields cannot collide (e.g. ("ab","c") vs ("a","bc")).
            hasher.update(len(lang_id).to_bytes(8, "little"))
            hasher.update(lang_id)
            hasher.update(len(query).to_bytes(8, "little"))
            hasher.update(query)
        return int.from_bytes(hasher.digest(), "little")


def compute_query_hash(configs: Iterable[TSLanguageConfig]) -> int:
    """Stable 64-bit hash of an arbitrary config set's queries.

    Module-level helper exposing the same blake2b length-prefixed digest as
    :meth:`LanguageRegistry.compute_query_hash` for any iterable of configs.
    grok only computes the hash via the registry method; this free-function
    form is a Pythonic convenience for callers that hold a config list without
    a registry (kept out of the crate-root barrel, matching grok's surface).
    """
    hasher = hashlib.blake2b(digest_size=8)
    for config in sorted(configs, key=lambda c: c.primary_language_id()):
        lang_id = config.primary_language_id().encode("utf-8")
        query = config.file_definition_queries().encode("utf-8")
        hasher.update(len(lang_id).to_bytes(8, "little"))
        hasher.update(lang_id)
        hasher.update(len(query).to_bytes(8, "little"))
        hasher.update(query)
    return int.from_bytes(hasher.digest(), "little")
