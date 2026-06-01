"""Skill loader — parses ``SKILL.md`` files into :class:`Skill` objects.

The loader is the only piece of the skill system that touches the
filesystem. :class:`SkillRegistry` consumes whatever this module
produces and never re-reads the disk, so swapping in a different
loader (e.g. a network-distributed registry) is a localised change.

Frontmatter format
------------------

A ``SKILL.md`` starts with a YAML-ish frontmatter block delimited by
``---`` lines, then a Markdown body::

    ---
    name: commit-helper
    version: 1.0.0
    description: |
      Helps craft well-formed git commit messages following
      Conventional Commits.
    when_to_use: |
      Use this skill when the user asks the agent to commit
      staged changes, or to suggest a commit message.
    tools:
      - get_git_diff
    ---

    # Commit Helper

    <Markdown body — injected as the skill's instructions when active.>

The parser is deliberately a *minimal* hand-rolled implementation —
it does not require PyYAML and tolerates the subset of YAML the
project actually emits. If a future skill needs richer YAML features
we can swap in PyYAML behind the same parse function signature.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SkillLoadError(ValueError):
    """Raised when a single SKILL.md cannot be parsed.

    The loader catches this internally and logs a warning, so a
    bad skill never crashes the whole registry.
    """


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Skill:
    """In-memory representation of a parsed SKILL.md.

    Attributes
    ----------
    name:
        Skill name from the frontmatter. Also the natural key on
        disk (one folder per name, no duplicates).
    version:
        Semver-ish string, defaults to ``"0.0.0"``.
    description:
        One- or two-line human description (shown in the UI).
    when_to_use:
        LLM-readable guidance for *when* to invoke the skill —
        injected into the system prompt alongside the body.
    tools:
        Names of the tool functions the skill registers. Each
        name must exist in the global :class:`ToolRegistry` at
        invoke time; missing names are reported via
        :meth:`missing_tools`.
    body:
        Markdown body of the SKILL.md (everything after the
        closing ``---`` of the frontmatter).
    path:
        Absolute path to the directory containing the SKILL.md.
    skill_id:
        Stable composite identifier ``"<dir_name>:<name>"``. Used
        as the in-memory dict key and exposed over IPC so the
        UI can refer to a skill without ambiguity.
    enabled:
        In-memory enable flag. The persistent copy lives in the
        ``skills`` table; the loader only sets the in-memory
        default (``True``); the registry reconciles it with
        the DB on construction.
    extra:
        Free-form frontmatter keys we don't model explicitly
        (``author``, ``homepage``, …). Preserved so nothing is
        silently dropped when re-serialising the manifest.
    """

    name: str
    description: str
    when_to_use: str
    body: str
    path: Path
    version: str = "0.0.0"
    tools: list[str] = field(default_factory=list)
    skill_id: str = ""
    enabled: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Coerce path so consumers can rely on Path operations.
        self.path = Path(self.path)
        if not self.skill_id:
            self.skill_id = make_skill_id(self.path, self.name)

    # -- derived views ------------------------------------------------------

    @property
    def instructions(self) -> str:
        """Combined text the runtime injects into the system prompt.

        Format: a short header plus the Markdown body. Keeping the
        body verbatim (no auto-formatting) means skill authors can
        include code samples, tables, etc. without surprises.
        """
        parts: list[str] = [f"# Skill: {self.name} (v{self.version})"]
        if self.when_to_use.strip():
            parts.append("## When to use\n" + self.when_to_use.strip())
        if self.body.strip():
            parts.append(self.body.strip())
        return "\n\n".join(parts)

    def manifest(self) -> dict[str, Any]:
        """Return the JSON-friendly view of the skill for IPC / persistence.

        Excludes the (potentially large) body — the UI can fetch
        it on demand via :meth:`SkillRegistry.get_full`.
        """
        return {
            "id": self.skill_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "when_to_use": self.when_to_use,
            "tools": list(self.tools),
            "path": str(self.path),
            "enabled": bool(self.enabled),
        }

    def missing_tools(self, available: Iterable[str]) -> list[str]:
        """Return the subset of ``self.tools`` not present in ``available``."""
        have = set(available)
        return [t for t in self.tools if t not in have]

    # -- mutation helpers (used by the registry) ----------------------------

    def with_enabled(self, enabled: bool) -> Skill:
        """Return a shallow copy with the ``enabled`` flag flipped."""
        new = Skill(
            name=self.name,
            description=self.description,
            when_to_use=self.when_to_use,
            body=self.body,
            path=self.path,
            version=self.version,
            tools=list(self.tools),
            skill_id=self.skill_id,
            enabled=bool(enabled),
            extra=dict(self.extra),
        )
        return new


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_skill_id(skill_dir: Path, name: str) -> str:
    """Build the composite skill id ``"<dir_name>:<name>"``.

    Falls back to ``"<basename>:<name>"`` if the path is empty.
    """
    dir_name = Path(skill_dir).name or "skill"
    return f"{dir_name}:{name}"


# Frontmatter regex: capture everything between the first pair of
# ``---`` lines at the top of the file. The body is everything that
# follows the *closing* ``---`` (plus optional leading whitespace).
_FRONTMATTER_RE = re.compile(
    r"\A\s*---\s*\n(?P<yaml>.*?)\n---\s*\n?(?P<body>.*)\Z",
    re.DOTALL,
)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse a ``SKILL.md`` string into ``(frontmatter, body)``.

    Raises
    ------
    SkillLoadError
        If the frontmatter delimiters are missing, the YAML
        block is empty, or a scalar can't be coerced.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise SkillLoadError("missing or malformed frontmatter (need leading '---' delimiters)")
    yaml_text = match.group("yaml")
    body = match.group("body")
    try:
        data = _parse_yaml_subset(yaml_text)
    except _YAMLError as exc:
        raise SkillLoadError(f"invalid frontmatter YAML: {exc}") from exc
    if not isinstance(data, Mapping):
        raise SkillLoadError("frontmatter must parse to a key/value mapping")
    return dict(data), body


# ---------------------------------------------------------------------------
# Minimal YAML-ish parser
# ---------------------------------------------------------------------------
#
# We support exactly the constructs used by SKILL.md files today:
#
#   * Bare scalars:        name: commit-helper
#   * Quoted scalars:      name: "commit helper"
#   * Block scalars:       description: |
#                            Multi-line content.
#                            Indented two spaces.
#   * Lists:               tools:
#                            - foo
#                            - bar
#   * Empty values:        enabled:
#
# Anything fancier (anchors, tags, nested mappings) raises
# ``_YAMLError``. That keeps the loader deterministic and the test
# surface small.


class _YAMLError(ValueError):
    pass


def _parse_yaml_subset(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    result: dict[str, Any] = {}
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        # Skip blank lines and comments.
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        # We only accept top-level "key: value" pairs.
        if not (line.startswith((" ", "\t"))):
            key, sep, rest = line.partition(":")
            if not sep:
                raise _YAMLError(f"expected ':' on line {i + 1}: {line!r}")
            key = key.strip()
            if not key:
                raise _YAMLError(f"empty key on line {i + 1}")
            rest_stripped = rest.strip()
            if not rest_stripped:
                # Could be a block scalar, a list, or an empty value.
                # First check whether the *next* line is indented
                # more than this key — if so, it is the start of a
                # block scalar / list child of this key.
                key_indent = _measure_indent(line)
                consumed, value = _parse_block(lines, i + 1, base_indent=key_indent, allow_implicit=True)
                if consumed == 0:
                    # Truly empty value.
                    value = ""
                result[key] = value
                i = i + 1 + consumed
                continue
            # Inline value — single line.
            # The value might still be a block-scalar header
            # (e.g. ``description: |``), in which case the
            # following indented lines are the scalar body.
            if rest_stripped in {"|", "|-", "|+", ">", ">-", ">+"}:
                consumed, value = _parse_block_scalar_after_marker(
                    lines, i + 1, base_indent=_measure_indent(line), marker=rest_stripped[0]
                )
                result[key] = value
                i = i + 1 + consumed
                continue
            result[key] = _parse_scalar(rest_stripped, line_no=i + 1)
            i += 1
        else:
            raise _YAMLError(
                f"unexpected indentation on line {i + 1} (top-level keys must not be indented)"
            )
    return result


def _measure_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _parse_block(
    lines: list[str],
    start: int,
    base_indent: int,
    *,
    allow_implicit: bool = False,
) -> tuple[int, Any]:
    """Parse a block that begins at ``lines[start]``.

    Returns ``(lines_consumed, value)``; ``lines_consumed=0`` means
    "no block here". Supports two shapes:

    * ``|`` / ``>`` block scalars (folded / literal).
    * List items ``- value`` (one per line, simple strings only).

    If ``allow_implicit`` is ``True``, the first content line
    that is just text (no marker) is treated as the start of
    an implicit block scalar — used after ``key: |`` so the
    parser can fall through to the body without a second
    marker line.
    """
    if start >= len(lines):
        return 0, None
    first = lines[start]
    if not first.strip():
        return 0, None
    first_indent = _measure_indent(first)
    if first_indent <= base_indent:
        return 0, None
    stripped = first.lstrip()
    if stripped.startswith(("- ", "-")):
        return _parse_block_list(lines, start, first_indent)
    if stripped.startswith(("|", ">", "|", "|-", "|-", ">-", ">+")):
        return _parse_block_scalar(lines, start, first_indent, marker=stripped[0])
    if allow_implicit:
        # Implicit block scalar: the marker (e.g. ``|``) was
        # already consumed on the key line; the body starts
        # on the *next* line and is just text. We treat it
        # as a literal block scalar.
        return _parse_block_scalar(lines, start, first_indent, marker="|")
    return 0, None


def _parse_block_scalar_after_marker(
    lines: list[str], start: int, base_indent: int, marker: str
) -> tuple[int, str]:
    """Parse a block scalar whose marker was consumed on the *previous* line.

    Used when the YAML reads ``key: |`` followed by indented
    body lines. The first content line is at ``lines[start]``;
    we read until we find a line at indent ``<= base_indent``.
    """
    if start >= len(lines):
        return 0, ""
    first = lines[start]
    if not first.strip():
        return 0, ""
    indent = _measure_indent(first)
    if indent <= base_indent:
        return 0, ""

    body_lines: list[str] = []
    i = start
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            body_lines.append("")
            i += 1
            continue
        cur_indent = _measure_indent(line)
        if cur_indent < indent:
            break
        body_lines.append(line[indent:])
        i += 1

    # Strip trailing blank lines (default chomping: "clip").
    while body_lines and body_lines[-1] == "":
        body_lines.pop()

    if marker == ">":
        folded: list[str] = []
        buf: list[str] = []
        for ln in body_lines:
            if not ln:
                if buf:
                    folded.append(" ".join(buf))
                    buf = []
                folded.append("")
            else:
                buf.append(ln)
        if buf:
            folded.append(" ".join(buf))
        text = "\n".join(folded)
    else:
        text = "\n".join(body_lines)

    return i - start, text


def _parse_block_scalar(
    lines: list[str], start: int, indent: int, marker: str
) -> tuple[int, str]:
    """Parse a ``|`` or ``>`` block scalar (legacy entry point).

    The marker lives on the *first* line (the one at
    ``lines[start]``). The body follows on subsequent
    indented lines. Kept for backwards compatibility with the
    list-marker parsing path that occasionally hits it.
    """
    head = lines[start].lstrip()
    # Header line is "marker" possibly followed by chomping / indentation.
    header = head[1:]  # drop the marker
    chomp = ""
    rest = header.strip()
    if rest and rest[0] in "+-":
        chomp = rest[0]
        rest = rest[1:].strip()
    explicit_indent: int | None = None
    if rest:
        try:
            explicit_indent = int(rest)
        except ValueError as exc:
            raise _YAMLError(f"invalid block-scalar header: {head!r}") from exc

    body_lines: list[str] = []
    i = start + 1
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            body_lines.append("")
            i += 1
            continue
        cur_indent = _measure_indent(line)
        if cur_indent < indent:
            break
        body_lines.append(line[indent:])
        i += 1

    # Trim trailing blank lines according to chomping.
    while body_lines and body_lines[-1] == "":
        body_lines.pop()
        if chomp == "-":
            break
    if chomp == "+":
        body_lines.append("")

    if marker == ">":
        # Folded: newlines become spaces, blank lines separate paragraphs.
        folded: list[str] = []
        buf: list[str] = []
        for ln in body_lines:
            if not ln:
                if buf:
                    folded.append(" ".join(buf))
                    buf = []
                folded.append("")
            else:
                buf.append(ln)
        if buf:
            folded.append(" ".join(buf))
        text = "\n".join(folded)
    else:
        text = "\n".join(body_lines)

    return i - start - 1, text


def _parse_block_list(lines: list[str], start: int, indent: int) -> tuple[int, list[Any]]:
    """Parse a simple ``- value`` list (one scalar per item)."""
    out: list[Any] = []
    i = start
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        cur_indent = _measure_indent(line)
        if cur_indent < indent:
            break
        if cur_indent > indent:
            raise _YAMLError(f"unexpected over-indent on line {i + 1}: {line!r}")
        stripped = line.lstrip()
        if not stripped.startswith("-"):
            break
        rest = stripped[1:].lstrip()
        if not rest:
            # Empty list item; treat as empty string.
            out.append("")
            i += 1
            continue
        out.append(_parse_scalar(rest, line_no=i + 1))
        i += 1
    return i - start, out


def _parse_scalar(text: str, *, line_no: int) -> Any:
    """Coerce a single-line scalar.

    Supports quoted strings, ints, floats, booleans, and null —
    everything else falls through as a plain string. We do *not*
    try to be clever about types because the frontmatter only
    needs strings + booleans + numeric versions.
    """
    if not text:
        return ""
    # Strip surrounding quotes.
    if (text[0] == text[-1]) and text[0] in ('"', "'"):
        return text[1:-1]
    low = text.lower()
    if low in {"true", "yes", "on"}:
        return True
    if low in {"false", "no", "off"}:
        return False
    if low in {"null", "~", ""}:
        return None
    # Numeric coercion — but only if the whole token looks numeric,
    # so a version string like "1.0.0" stays a string.
    if re.fullmatch(r"-?\d+", text):
        try:
            return int(text)
        except ValueError as exc:  # pragma: no cover — defensive
            raise _YAMLError(str(exc)) from exc
    if re.fullmatch(r"-?\d+\.\d+", text):
        try:
            return float(text)
        except ValueError as exc:  # pragma: no cover
            raise _YAMLError(str(exc)) from exc
    return text


# ---------------------------------------------------------------------------
# Public loading API
# ---------------------------------------------------------------------------


_REQUIRED_FRONTMATTER_KEYS: tuple[str, ...] = ("name",)


def load_skill_file(path: Path) -> Skill:
    """Load a single ``SKILL.md`` file.

    Parameters
    ----------
    path:
        Absolute path to the ``SKILL.md`` (or a file with a
        different name — the caller chooses).

    Raises
    ------
    SkillLoadError
        On any parse problem. ``SkillRegistry.load_all`` catches
        this; tests assert on it directly.
    FileNotFoundError
        If the file does not exist.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    manifest, body = parse_frontmatter(text)
    for required in _REQUIRED_FRONTMATTER_KEYS:
        if required not in manifest or not manifest[required]:
            raise SkillLoadError(f"frontmatter missing required key {required!r}")

    name = str(manifest["name"]).strip()
    if not name:
        raise SkillLoadError("frontmatter 'name' is empty")
    if any(c.isspace() for c in name):
        raise SkillLoadError(f"skill name must not contain whitespace: {name!r}")

    description = str(manifest.get("description", "") or "").strip()
    when_to_use = str(manifest.get("when_to_use", "") or "").strip()
    version = str(manifest.get("version", "0.0.0") or "0.0.0").strip() or "0.0.0"

    raw_tools = manifest.get("tools", []) or []
    if not isinstance(raw_tools, list):
        raise SkillLoadError("frontmatter 'tools' must be a list")
    tools: list[str] = []
    for t in raw_tools:
        if not isinstance(t, str) or not t.strip():
            raise SkillLoadError("each entry in 'tools' must be a non-empty string")
        tools.append(t.strip())

    known = {
        "name",
        "version",
        "description",
        "when_to_use",
        "tools",
    }
    extra = {k: v for k, v in manifest.items() if k not in known}

    skill = Skill(
        name=name,
        version=version,
        description=description,
        when_to_use=when_to_use,
        body=body,
        path=path.parent,
        tools=tools,
        extra=extra,
    )
    return skill


def load_skill_dir(dir_path: Path, *, filename: str = "SKILL.md") -> Skill | None:
    """Load a skill from a directory; returns ``None`` if no file is found.

    A directory without the expected file is *not* an error — it
    just means the directory is not a skill (e.g. it holds
    arbitrary resources, or is a partially-initialised workspace).
    """
    candidate = Path(dir_path) / filename
    if not candidate.is_file():
        return None
    return load_skill_file(candidate)


def load_all(
    skills_root: Path,
    *,
    filename: str = "SKILL.md",
    skip_names: Iterable[str] = ("__pycache__", ".git", ".venv"),
) -> list[Skill]:
    """Scan a directory tree for skill folders and load each.

    A "skill folder" is a direct subdirectory of ``skills_root``
    (or any nested subdirectory) that contains a ``SKILL.md`` file.
    The first ``SKILL.md`` at the top of each folder wins; nested
    duplicates are logged and skipped.

    Parameters
    ----------
    skills_root:
        Root directory to scan. Non-existent paths yield ``[]``.
    filename:
        File name to look for in each subdirectory.
    skip_names:
        Directory names that should be skipped (default: VCS,
        virtualenv, and bytecode caches). Matched case-sensitively
        against the immediate subdirectory name.

    Returns
    -------
    list[Skill]
        Loaded skills in directory-walk order. Malformed skills
        are skipped with a warning, not raised.
    """
    skills_root = Path(skills_root)
    if not skills_root.exists():
        return []
    skip_set = set(skip_names)
    out: list[Skill] = []
    seen_names: set[str] = set()

    for child in sorted(skills_root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        if child.name in skip_set or child.name.startswith("."):
            continue
        try:
            skill = load_skill_dir(child, filename=filename)
        except SkillLoadError as exc:
            logger.warning("skipping malformed skill at %s: %s", child, exc)
            continue
        except OSError as exc:
            logger.warning("could not read skill at %s: %s", child, exc)
            continue
        if skill is None:
            continue
        if skill.name in seen_names:
            logger.warning(
                "duplicate skill name %r at %s — first wins, skipping", skill.name, child
            )
            continue
        seen_names.add(skill.name)
        out.append(skill)

    return out


def validate_tools(skills: Iterable[Skill], available_tool_names: Iterable[str]) -> dict[str, list[str]]:
    """Return a mapping ``skill_id -> missing_tool_names`` for validation.

    The registry calls this on startup so the UI can flag skills
    whose tools were not registered. Missing tools are *not* a
    hard failure — the skill still loads, but invoking it will
    fail when the LLM tries to call a non-existent tool.
    """
    have = set(available_tool_names)
    out: dict[str, list[str]] = {}
    for skill in skills:
        missing = [t for t in skill.tools if t not in have]
        if missing:
            out[skill.skill_id] = missing
    return out


__all__ = [
    "Skill",
    "SkillLoadError",
    "load_all",
    "load_skill_dir",
    "load_skill_file",
    "make_skill_id",
    "parse_frontmatter",
    "validate_tools",
]
