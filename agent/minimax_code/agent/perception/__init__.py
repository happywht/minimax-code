"""Perception engine — codebase awareness for the LLM.

Provides a compressed symbol tree (repo-map) that gives the LLM
deep awareness of the project structure without consuming excessive
context tokens.
"""

from __future__ import annotations

from .cache import FileChangeCache
from .indexer import FileOutline, RepoMapIndexer, SymbolNode
from .parsers import PARSERS

__all__ = [
    "FileChangeCache",
    "FileOutline",
    "RepoMapIndexer",
    "SymbolNode",
    "PARSERS",
]
