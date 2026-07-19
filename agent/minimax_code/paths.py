"""Type-safe absolute/relative path wrappers — fusion of grok's ``xai-grok-paths`` (R44).

``AbsPathBuf`` / ``RelPathBuf`` newtypes that validate at construction (absolute
vs relative), a lexical ``.``/``..`` resolver that never touches the filesystem,
and a workspace-containment check that blocks path-escape via ``..``. Mirrors
grok's ``xai-grok-paths`` (lib.rs, 575 lines): "Type-safe path wrappers for
absolute and relative UTF-8 paths."

Mapping
-------
* ``camino::Utf8PathBuf`` (a UTF-8-guaranteed path) → :class:`pathlib.Path`.
  ``str`` / :class:`~pathlib.Path` are Unicode-native in Python, so camino's
  UTF-8 invariant has no equivalent to enforce; the grok ``NotUtf8`` error
  variant is therefore dropped (it cannot be raised here).
* ``AbsPathBuf`` (newtype, ``Clone+Debug+Eq+PartialEq+Ord+PartialOrd+Hash``,
  **no** ``Serialize`` — line 84 of grok's lib.rs derives only the value traits)
  → :class:`AbsPathBuf`, a frozen+slots dataclass wrapping :class:`~pathlib.Path`
  (branch 2 of the payload decision tree — pure value equality, no wire surface,
  same as R40 ``QueueEntryMeta`` / R42 ``Version`` / R43 ``BuildEndpoints``).
  grok's ``Ord`` / ``PartialOrd`` are deferred (no caller needs ordered paths
  today — YAGNI); ``__eq__`` / ``__hash__`` cover ``Eq`` / ``Hash``.
* ``RelPathBuf`` (newtype, the same value traits **plus** ``Serialize`` /
  ``Deserialize`` with ``#[serde(try_from = "String", into = "String")]``) →
  :class:`RelPathBuf`, the same frozen+slots dataclass shape as
  :class:`AbsPathBuf`. The serde surface is kept as :meth:`RelPathBuf.from_str`
  (``try_from = "String"``) and :meth:`RelPathBuf.__str__` /
  :meth:`RelPathBuf.into_string` (``into = "String"``): Python strings *are* the
  JSON wire surface, so a pydantic model is not introduced (a pydantic model
  serialises to a dict, which would *diverge* from grok's bare-string round-trip;
  the pydantic field-type integration is a future round — YAGNI until a Config
  model gains a path field).
* ``AbsPathError`` / ``RelPathError`` (``thiserror::Error`` enums with
  ``NotAbsolute``/``NotRelative`` plus an unreachable ``NotUtf8``) →
  :class:`AbsPathError` / :class:`RelPathError`, ``ValueError`` subclasses
  carrying the offending ``input``. Python folds the impossible ``NotUtf8``
  variant away; the surviving variant is distinguished by class + message.
* ``ToAbsPath`` trait (six impls: ``AbsPathBuf`` / ``&AbsPathBuf`` / ``&Path`` /
  ``&PathBuf`` / ``&str`` / ``String``) → :func:`to_abs_path`, a single free
  function with ``isinstance`` dispatch. Python has no trait polymorphism; one
  function covers the six grok impls (abs inputs ignore ``root``; relative inputs
  join with ``root``).
* ``to_relative_path`` / ``from_relative_path`` (free functions) → module-level
  functions of the same name; ``strip_prefix`` + ``unwrap_or_else`` →
  :meth:`~pathlib.PurePath.relative_to` + ``except ValueError`` fallback.
* ``normalize_lexically`` (free function; ``Component`` walk, no FS access) →
  :func:`normalize_lexically`, a hand-written walk over
  :attr:`~pathlib.PurePath.parts` mirroring grok's component match (``.`` drops;
  ``..`` pops a normal / clamps at a root anchor / pushes otherwise).
* ``camino`` / ``serde`` / ``thiserror`` crate deps → unused: ``pathlib`` +
  ``re`` + ``os`` (stdlib only, zero non-stdlib deps).

Product fusion
--------------
MiniMax Code's file tools (``file_ops`` / ``edit`` / ``search``) accept a bare
``file_path: str`` with no type-level guarantee. :class:`AbsPathBuf` makes
"absolute at construction" an enforceable invariant, and
:meth:`AbsPathBuf.contains_path` is a workspace-containment / path-escape check:
an agent file tool can reject an edit whose LLM-supplied path escapes the
project root via ``..`` even after lexical normalization. This module is the
vocabulary the file tools will adopt at their entry points in a future round;
for now it is the leaf they build on.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePath

# --- Errors (grok AbsPathError / RelPathError, thiserror enum) --------------


class AbsPathError(ValueError):
    """Raised when constructing an :class:`AbsPathBuf` from a non-absolute path.

    Mirrors grok's ``AbsPathError`` enum. The ``NotUtf8`` variant has no Python
    equivalent — ``str`` / :class:`~pathlib.Path` are Unicode-native, so a path
    that is not valid UTF-8 cannot be expressed here. Only the ``NotAbsolute``
    case survives (Python folds the absent variant into one class carrying the
    offending ``input``).
    """

    def __init__(self, input: str) -> None:
        super().__init__(f"Path is not absolute: {input}")
        self.input = input


class RelPathError(ValueError):
    """Raised when constructing a :class:`RelPathBuf` from an absolute path.

    Mirrors grok's ``RelPathError`` (``NotRelative`` variant; ``NotUtf8`` is
    impossible in Python — see :class:`AbsPathError`).
    """

    def __init__(self, input: str) -> None:
        super().__init__(f"Path is not relative: {input}")
        self.input = input


# --- lexical helpers (no filesystem access; grok normalize_lexically) --------

#: A Windows drive prefix with an optional trailing separator, e.g. ``C:`` or
#: ``C:\\``. Used to recognise path anchors retained as the first ``parts``
#: element by :class:`pathlib.PurePath`.
_ANCHOR_RE = re.compile(r"^[A-Za-z]:[\\/]?$")
#: A Windows drive prefix that *includes* a root separator (``C:\\`` / ``C:/``).
#: ``..`` clamps at these (grok ``RootDir``) but is pushed after a bare ``C:``
#: (grok ``PrefixDir`` — drive-relative, no root).
_DRIVE_ROOT_RE = re.compile(r"^[A-Za-z]:[\\/]$")


def _is_anchor(part: str) -> bool:
    """Whether ``part`` is a path anchor (root or drive prefix)."""
    return part in ("/", "\\") or bool(_ANCHOR_RE.match(part))


def _is_root_anchor(part: str) -> bool:
    """Whether ``part`` is a *rooted* anchor (a root dir; ``..`` clamps here).

    A bare ``C:`` (drive-relative, no trailing separator) is an anchor but NOT a
    root — grok treats it as ``PrefixDir``, so ``..`` is pushed, not clamped.
    """
    return part in ("/", "\\") or bool(_DRIVE_ROOT_RE.match(part))


def _is_normal(part: str) -> bool:
    """Whether ``part`` is a normal component (not an anchor, not ``.``/``..``)."""
    return part not in (".", "..") and not _is_anchor(part)


def normalize_lexically(path: str | os.PathLike[str]) -> PurePath:
    """Resolve ``.`` / ``..`` components without touching the filesystem.

    Lexical only — use for display or containment, never as a filesystem truth.
    If a segment is a symlink, ``a/b/../c`` may name a different target than the
    OS would resolve; filesystem consumers must keep the original path or
    deliberately canonicalize it. Mirrors grok's ``normalize_lexically``.

    Semantics (match grok's ``Component`` walk):
    * ``.`` is dropped;
    * ``..`` pops a preceding normal component, clamps at a root anchor
      (``/`` / ``C:\\``), and is pushed otherwise (a relative-leading ``..`` or
      after a drive-relative prefix like ``C:``);
    * an empty result becomes ``.``.
    """
    parts = PurePath(os.fspath(path)).parts
    stack: list[str] = []
    for part in parts:
        if part in (".", ""):
            continue
        if part == "..":
            if stack and _is_normal(stack[-1]):
                stack.pop()
            elif stack and _is_root_anchor(stack[-1]):
                continue  # clamp at root — do not push
            else:
                stack.append(part)
            continue
        stack.append(part)
    if not stack:
        return PurePath(".")
    return PurePath(*stack)


def to_relative_path(root: str | os.PathLike[str], abs_path: str | os.PathLike[str]) -> PurePath:
    """Strip ``root`` from ``abs_path``; return unchanged if not under ``root``.

    Mirrors grok's ``to_relative_path`` (``strip_prefix`` with an
    ``unwrap_or_else`` fallback to the input). For strict validation use
    :meth:`RelPathBuf.from_absolute` instead.

    Note: when ``abs_path`` equals ``root`` exactly, Python's
    :meth:`~pathlib.PurePath.relative_to` yields ``PurePath('.')`` whereas
    Rust's ``strip_prefix`` yields an empty ``PathBuf`` (``""``); the Python
    mapping keeps the platform-native ``.``.
    """
    root_p = PurePath(os.fspath(root))
    abs_p = PurePath(os.fspath(abs_path))
    try:
        return abs_p.relative_to(root_p)
    except ValueError:
        return abs_p


def from_relative_path(root: str | os.PathLike[str], rel_path: str | os.PathLike[str]) -> PurePath:
    """Join ``rel_path`` with ``root``; an already-absolute path is returned as-is.

    Mirrors grok's ``from_relative_path``.
    """
    rel_p = PurePath(os.fspath(rel_path))
    if rel_p.is_absolute():
        return rel_p
    return PurePath(os.fspath(root)) / rel_p


# --- AbsPathBuf (grok AbsPathBuf, branch 2: frozen value wrapper) -----------


@dataclass(frozen=True, slots=True)
class AbsPathBuf:
    """An absolute path (grok ``AbsPathBuf``, ``camino::Utf8PathBuf`` newtype).

    Construct via :meth:`new` (validates absoluteness). A frozen value wrapper
    (branch 2 of the payload decision tree — ``Clone+Debug+Eq+PartialEq+Hash``,
    **no** ``Serialize``). ``camino::Utf8PathBuf`` → :class:`~pathlib.Path`:
    Python strings are Unicode-native, so camino's UTF-8 guarantee has no
    Python equivalent to enforce (grok's ``NotUtf8`` variant is dropped).

    grok's ``Ord`` / ``PartialOrd`` are deferred (no caller needs ordered paths
    today — YAGNI); ``__eq__`` / ``__hash__`` cover ``Eq`` / ``Hash``.
    """

    _path: Path

    @classmethod
    def new(cls, path: str | os.PathLike[str]) -> AbsPathBuf:
        """Construct from ``path``; raise :class:`AbsPathError` if not absolute."""
        p = Path(os.fspath(path))
        if not p.is_absolute():
            raise AbsPathError(str(p))
        return cls(p)

    def as_path(self) -> Path:
        """The wrapped :class:`~pathlib.Path` (grok ``as_path``)."""
        return self._path

    def as_str(self) -> str:
        """The path as a string (grok ``as_str``)."""
        return str(self._path)

    def to_path_buf(self) -> Path:
        """A fresh :class:`~pathlib.Path` clone (grok ``to_path_buf``)."""
        return Path(self._path)

    def into_string(self) -> str:
        """Consume into a string (grok ``into_string``)."""
        return str(self._path)

    def join(self, path: str | os.PathLike[str]) -> AbsPathBuf:
        """Append ``path``; the result stays absolute (grok ``join``)."""
        return AbsPathBuf(self._path / os.fspath(path))

    def is_dir(self) -> bool:
        """Whether the path is an existing directory (grok ``is_dir``; FS access)."""
        return self._path.is_dir()

    def contains_path(self, other: AbsPathBuf) -> bool:
        """Whether ``other`` is under ``self`` after lexical normalization.

        Mirrors grok's ``contains_path``: normalizes ``.``/``..`` in ``other``
        (no symlink resolution) and tests a prefix match. The workspace-
        containment / path-escape check — an agent file tool can reject edits
        outside the project root even when an LLM-supplied path uses ``..``.
        """
        return normalize_lexically(other.as_path()).is_relative_to(self.as_path())

    def __str__(self) -> str:
        return str(self._path)

    def __fspath__(self) -> str:
        # os.PathLike support (grok ``AsRef<Path>``); lets ``open(self)`` work.
        return str(self._path)


# --- RelPathBuf (grok RelPathBuf, branch 2 + serde try_from/into String) -----


@dataclass(frozen=True, slots=True)
class RelPathBuf:
    """A relative path (grok ``RelPathBuf``, ``camino::Utf8PathBuf`` newtype).

    Like :class:`AbsPathBuf` but :meth:`new` validates *relativeness*, plus
    :meth:`from_absolute` (strips a root) and :meth:`to_absolute` (joins one).
    grok's ``RelPathBuf`` derives ``Serialize``/``Deserialize``
    (``#[serde(try_from = "String", into = "String")]``) — it round-trips
    through a bare string. The Python mapping keeps that as :meth:`from_str`
    (``try_from``) and :meth:`__str__` / :meth:`into_string` (``into``); Python
    strings are the JSON wire surface, so a pydantic model is not introduced.
    """

    _path: Path

    @classmethod
    def new(cls, path: str | os.PathLike[str]) -> RelPathBuf:
        """Construct from ``path``; raise :class:`RelPathError` if absolute."""
        p = Path(os.fspath(path))
        if p.is_absolute():
            raise RelPathError(str(p))
        return cls(p)

    @classmethod
    def from_str(cls, text: str) -> RelPathBuf:
        """Construct from a bare string (grok serde ``try_from = "String"``)."""
        return cls.new(text)

    @classmethod
    def from_absolute(
        cls, root: str | os.PathLike[str], abs_path: str | os.PathLike[str]
    ) -> RelPathBuf:
        """Strip ``root`` from ``abs_path``; raise if not under ``root``.

        Mirrors grok's ``RelPathBuf::from_absolute`` (strict — errors when
        ``abs_path`` is not below ``root``, unlike :func:`to_relative_path`).
        """
        rel = to_relative_path(root, abs_path)
        if rel.is_absolute():
            raise RelPathError(str(abs_path))
        return cls(Path(rel))

    def to_absolute(self, root: str | os.PathLike[str]) -> Path:
        """Join with ``root`` to get an absolute path (grok ``to_absolute``)."""
        return Path(os.fspath(root)) / self._path

    def as_path(self) -> Path:
        """The wrapped :class:`~pathlib.Path`."""
        return self._path

    def as_str(self) -> str:
        """The path as a string."""
        return str(self._path)

    def to_path_buf(self) -> Path:
        """A fresh :class:`~pathlib.Path` clone."""
        return Path(self._path)

    def into_string(self) -> str:
        """Consume into a string (grok ``into`` / serde ``into = "String"``)."""
        return str(self._path)

    def __str__(self) -> str:
        return str(self._path)

    def __fspath__(self) -> str:
        # os.PathLike support (grok ``AsRef<Path>``).
        return str(self._path)


# --- to_abs_path (grok ToAbsPath trait, six impls → one dispatched fn) -------


def to_abs_path(
    path: str | os.PathLike[str] | AbsPathBuf | RelPathBuf,
    root: str | os.PathLike[str],
) -> Path:
    """Coerce ``path`` to absolute against ``root`` (grok ``ToAbsPath`` trait).

    Absolute inputs ignore ``root``; relative inputs join with ``root``.
    Dispatches across the grok trait's six impls (``AbsPathBuf`` / ``&AbsPathBuf``
    / ``&Path`` / ``&PathBuf`` / ``&str`` / ``String``) — Python has no trait
    polymorphism, so one free function covers them.
    """
    if isinstance(path, AbsPathBuf):
        return path.as_path()
    if isinstance(path, RelPathBuf):
        return path.to_absolute(root)
    p = Path(os.fspath(path))
    if p.is_absolute():
        return p
    return Path(os.fspath(root)) / p


__all__ = [
    "AbsPathBuf",
    "AbsPathError",
    "RelPathBuf",
    "RelPathError",
    "from_relative_path",
    "normalize_lexically",
    "to_abs_path",
    "to_relative_path",
]
