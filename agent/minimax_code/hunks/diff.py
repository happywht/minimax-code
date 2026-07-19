"""Diff computation primitives — fusion of grok's ``xai-hunk-tracker`` diff layer (R39).

Line-based diffing of a baseline against the current content of a file, turned
into a list of :class:`~.types.Hunk` records with source attribution, plus the
patch-rendering, line-patching, and hunk match/overlap predicates that a
"review the agent's edit block-by-block" UI builds on. Pure functions only —
no actor (tokio) or git (gix) surface; that is a wiring-round concern.

Mapping
-------

grok uses the ``similar`` crate; this port uses the standard-library
:mod:`difflib` (zero new dependencies). The mapping is exact on the cases the
grok tests pin (single-line modify, insertion, deletion, multi-hunk, content
match at different positions, best-overlap fallback):

* ``similar::TextDiff::configure().timeout(..).diff_lines(baseline, current)``
  with ``iter_all_changes()`` over ``ChangeTag::Equal/Delete/Insert`` →
  :class:`difflib.SequenceMatcher` over keepends line lists with
  :meth:`.get_opcodes` returning ``equal``/``delete``/``insert``/``replace``.
  ``replace`` is a delete+insert pair collapsed into the *same* hunk, matching
  grok's single-accumulator behavior (one ``HunkBuilder`` spans the pair).
* :func:`difflib.SequenceMatcher` is constructed with ``autojunk=False``
  (mandatory): the default ``autojunk=True`` turns frequently-repeated lines
  into "junk" and silently changes the diff of large files. ``similar`` does
  no such heuristic, so the default must be opted out of to stay faithful.
* Line split for **diffing** uses ``splitlines(keepends=True)`` so the
  ``old_text`` / ``new_text`` fragments keep their original ``\\n`` (grok's
  ``change.value()`` carries the newline). The **rendering** functions
  (:func:`format_unified_diff`, :func:`generate_hunk_patch`) then use plain
  ``splitlines()`` (grok's ``str::lines()``) to strip the newline for display.
* Line numbers: opcode ``i1`` / ``j1`` are 0-indexed; grok's ``old_start`` /
  ``new_start`` are 1-indexed. ``old_start = i1 + 1`` and
  ``new_start = j1 + 1``. This is provably equal to grok's cursor tracking
  (``old_line`` / ``new_line`` start at 1 and advance once per Equal/Delete or
  Equal/Insert change respectively): by the time an opcode ``(i1, i2, j1, j2)``
  is reached, the prior opcodes have consumed ``i1`` old lines and ``j1`` new
  lines, so the cursors are exactly ``i1 + 1`` and ``j1 + 1``.
* ``Rust str::len()`` (bytes) vs ``Python len(str)`` (chars): the
  :data:`MAX_DIFF_FILE_SIZE` guard uses :func:`len(s.encode("utf-8"))` to
  mirror grok's byte-based limit. Same call site decision as R38.

Resource guard
--------------

:data:`MAX_DIFF_FILE_SIZE` (1 MiB) is ported verbatim and enforced: a file
over the cap short-circuits to an empty hunk list (grok warns and returns
``vec![]``). :data:`DIFF_TIMEOUT` (10 s) is **documented but not enforced**
here: :mod:`difflib` is synchronous with no timeout parameter, so a wall-clock
budget can only be imposed by the caller (an async wiring layer wrapping
:func:`compute_hunks` in ``asyncio.wait_for``). This mirrors R38, which also
deferred its wall-clock timeout to out-of-process execution. Keeping the
constant here documents the budget a wiring round must honor.
"""

from __future__ import annotations

import difflib
import logging
from collections.abc import Iterable, Sequence

from .types import Hunk, HunkId, HunkLineInfo, HunkSource

#: Number of context lines to include around changes (like ``git diff``).
CONTEXT_LINES: int = 3

#: Maximum file size (in bytes) to attempt diffing. Files over this are skipped
#: to avoid pathological diff behavior (grok ``MAX_DIFF_FILE_SIZE``).
MAX_DIFF_FILE_SIZE: int = 1024 * 1024  # 1 MiB

#: Wall-clock budget grok allows for one diff (``similar`` has a native timeout).
#: ``difflib`` is synchronous with no timeout parameter, so this is documented
#: here but NOT enforced — a wiring round calling :func:`compute_hunks` from an
#: async context should wrap it in ``asyncio.wait_for(..., DIFF_TIMEOUT)``.
#: Same deferral shape as R38 (wall-clock timeout lives in the wiring layer).
DIFF_TIMEOUT: float = 10.0

_logger = logging.getLogger(__name__)


def _byte_len(text: str) -> int:
    """UTF-8 byte length (grok ``str::len()`` counts bytes; Python ``len`` counts chars)."""
    return len(text.encode("utf-8"))


def generate_unified_patch(path: str, baseline: str, current: str) -> str | None:
    """Render a full unified-diff patch string for ``path`` (grok ``generate_unified_patch``).

    Returns ``None`` when there is nothing to patch (identical content) or when
    either side exceeds :data:`MAX_DIFF_FILE_SIZE`. The patch carries the
    ``a/{path}`` / ``b/{path}`` file headers and ``CONTEXT_LINES`` lines of
    context around each change, matching grok's ``unified_diff`` output shape.
    """
    if baseline == current:
        return None
    if _byte_len(baseline) > MAX_DIFF_FILE_SIZE or _byte_len(current) > MAX_DIFF_FILE_SIZE:
        _logger.warning(
            "Skipping unified patch for %s: file exceeds %d-byte limit", path, MAX_DIFF_FILE_SIZE
        )
        return None
    # Plain splitlines (no keepends): difflib emits content lines verbatim, so
    # stripping the newline here keeps every line newline-free; the join below
    # reintroduces a uniform "\n" between them.
    old_split = baseline.splitlines()
    new_split = current.splitlines()
    diff_lines = difflib.unified_diff(
        old_split,
        new_split,
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=CONTEXT_LINES,
        lineterm="",
    )
    lines = list(diff_lines)
    if not lines:
        return None
    return "\n".join(lines) + "\n"


def compute_hunks(
    path: str, baseline: str, current: str, source: HunkSource
) -> list[Hunk]:
    """Diff ``baseline`` against ``current`` and return the change hunks (grok ``compute_hunks``).

    Returns an empty list when the content is identical or either side is over
    :data:`MAX_DIFF_FILE_SIZE`. Each hunk is attributed to ``source`` and
    carries the ``old_text`` / ``new_text`` fragments (with their original
    newlines) so downstream rendering can reproduce them verbatim.
    """
    if baseline == current:
        return []
    if _byte_len(baseline) > MAX_DIFF_FILE_SIZE or _byte_len(current) > MAX_DIFF_FILE_SIZE:
        _logger.warning(
            "Skipping diff for %s: file exceeds %d-byte limit", path, MAX_DIFF_FILE_SIZE
        )
        return []
    # Keepends: the fragments stored in old_text/new_text must retain their
    # original newline so rendering round-trips (grok's change.value() carries it).
    old_lines = baseline.splitlines(keepends=True)
    new_lines = current.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    hunks: list[Hunk] = []
    builder: _HunkBuilder | None = None
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            if builder is not None:
                hunks.append(builder.build(path, source))
                builder = None
        elif tag == "delete":
            if builder is None:
                builder = _HunkBuilder(i1 + 1, j1 + 1)
            builder.add_old_lines(old_lines[i1:i2])
        elif tag == "insert":
            if builder is None:
                builder = _HunkBuilder(i1 + 1, j1 + 1)
            builder.add_new_lines(new_lines[j1:j2])
        elif tag == "replace":
            if builder is None:
                builder = _HunkBuilder(i1 + 1, j1 + 1)
            builder.add_old_lines(old_lines[i1:i2])
            builder.add_new_lines(new_lines[j1:j2])
    if builder is not None:
        hunks.append(builder.build(path, source))
    return hunks


class _HunkBuilder:
    """Accumulate old/new lines while walking the diff opcodes (grok ``HunkBuilder``).

    grok walks ``iter_all_changes`` and calls ``add_old_line`` / ``add_new_line``
    once per change; this port walks grouped opcodes, so the adders take the
    whole slice for the opcode. The accumulator semantics are identical: one
    builder spans a delete+insert (replace) pair because they share an
    ``equal``-bounded run.
    """

    __slots__ = ("old_start", "new_start", "old_lines", "new_lines")

    def __init__(self, old_start: int, new_start: int) -> None:
        self.old_start = old_start
        self.new_start = new_start
        self.old_lines: list[str] = []
        self.new_lines: list[str] = []

    def add_old_lines(self, lines: Iterable[str]) -> None:
        self.old_lines.extend(lines)

    def add_new_lines(self, lines: Iterable[str]) -> None:
        self.new_lines.extend(lines)

    def build(self, path: str, source: HunkSource) -> Hunk:
        old_text = "".join(self.old_lines) if self.old_lines else None
        new_text = "".join(self.new_lines)
        return Hunk(
            id=HunkId.new(),
            path=path,
            line_info=HunkLineInfo(
                self.old_start,
                len(self.old_lines),
                self.new_start,
                len(self.new_lines),
            ),
            source=source,
            old_text=old_text,
            new_text=new_text,
        )


def generate_hunk_patch(baseline: str, current: str, hunk: Hunk) -> str:
    """Render a single-hunk patch fragment with context (grok ``generate_hunk_patch``).

    Returns just the hunk portion (no ``---`` / ``+++`` file headers): the
    ``@@ -os,oc +ns,nc @@`` header, ``CONTEXT_LINES`` lines of leading context,
    the ``-`` deleted lines, the ``+`` added lines, then trailing context.
    """
    old_lines = baseline.splitlines()
    new_lines = current.splitlines()

    # Change bounds, 0-indexed (grok saturating_sub).
    old_start_idx = max(hunk.line_info.old_start - 1, 0)
    new_start_idx = max(hunk.line_info.new_start - 1, 0)

    # Leading context (old-file coordinates).
    context_before_start = max(old_start_idx - CONTEXT_LINES, 0)
    context_before_end = old_start_idx

    # Trailing context uses new-file coordinates on the new side (changes may
    # have shifted things) and old-file coordinates on the old side.
    changes_end_new = new_start_idx + hunk.line_info.new_count
    context_after_start = changes_end_new
    context_after_end = min(changes_end_new + CONTEXT_LINES, len(new_lines))

    changes_end_old = old_start_idx + hunk.line_info.old_count
    context_after_start_old = changes_end_old
    context_after_end_old = min(changes_end_old + CONTEXT_LINES, len(old_lines))

    total_old_lines = (
        (context_before_end - context_before_start)
        + hunk.line_info.old_count
        + (context_after_end_old - context_after_start_old)
    )
    total_new_lines = (
        (context_before_end - context_before_start)
        + hunk.line_info.new_count
        + (context_after_end - context_after_start)
    )

    header_old_start = context_before_start + 1
    header_new_start = context_before_start + 1  # context is shared before the change

    output: list[str] = [
        f"@@ -{header_old_start},{total_old_lines} +{header_new_start},{total_new_lines} @@"
    ]

    for i in range(context_before_start, context_before_end):
        if 0 <= i < len(old_lines):
            output.append(f" {old_lines[i]}")

    if hunk.old_text is not None:
        for line in hunk.old_text.splitlines():
            output.append(f"-{line}")

    for line in hunk.new_text.splitlines():
        output.append(f"+{line}")

    for i in range(context_after_start, context_after_end):
        if 0 <= i < len(new_lines):
            output.append(f" {new_lines[i]}")

    return "\n".join(output) + "\n"


def format_unified_diff(hunk: Hunk) -> str:
    """Render a hunk as a unified-diff fragment with file headers (grok ``format_unified_diff``).

    Emits ``--- a/{path}``, ``+++ b/{path}``, the hunk header, the ``-`` deleted
    lines, then the ``+`` added lines.
    """
    output: list[str] = [f"--- a/{hunk.path}", f"+++ b/{hunk.path}", str(hunk.line_info)]
    if hunk.old_text is not None:
        for line in hunk.old_text.splitlines():
            output.append(f"-{line}")
    for line in hunk.new_text.splitlines():
        output.append(f"+{line}")
    return "\n".join(output) + "\n"


def patch_lines(content: str, start_line: int, remove_count: int, insert_text: str) -> str:
    """Apply a line-level patch to ``content`` (grok ``patch_lines``).

    Removes ``remove_count`` lines starting at the 1-indexed ``start_line`` and
    inserts ``insert_text`` (split into lines) at that point. A trailing newline
    is preserved when the original content had one.
    """
    lines = content.splitlines()
    start_idx = max(start_line - 1, 0)

    result: list[str] = list(lines[: min(start_idx, len(lines))])

    if insert_text:
        result.extend(insert_text.splitlines())

    end_idx = min(start_idx + remove_count, len(lines))
    result.extend(lines[end_idx:])

    output = "\n".join(result)
    if content.endswith("\n") and output:
        output += "\n"
    return output


def hunks_match_content(a: Hunk, b: Hunk) -> bool:
    """Same file, same old text, same new text (grok ``hunks_match_content``)."""
    return a.path == b.path and a.old_text == b.old_text and a.new_text == b.new_text


def hunk_moved(old: Hunk, new: Hunk) -> bool:
    """Same content at a different line position (grok ``hunk_moved``)."""
    return hunks_match_content(old, new) and old.line_info != new.line_info


def hunks_overlap(a: Hunk, b: Hunk) -> bool:
    """Whether two hunks overlap by line range in the baseline (grok ``hunks_overlap``).

    Uses ``old_start`` / ``old_count`` (baseline-relative) for stable overlap
    detection even when the file has shifted. Pure insertions
    (``old_count == 0``) get special handling: two insertions overlap only at
    the same baseline position, and a single insertion overlaps a regular hunk
    when its position falls within ``[start, end]``. Otherwise the standard
    "not disjoint" test applies, treating adjacent (touching) hunks as overlapping.
    """
    if a.path != b.path:
        return False

    a_start = a.line_info.old_start
    a_end = a.line_info.old_start + a.line_info.old_count
    b_start = b.line_info.old_start
    b_end = b.line_info.old_start + b.line_info.old_count

    # Two pure insertions overlap only at the same baseline position.
    if a.line_info.old_count == 0 and b.line_info.old_count == 0:
        return a_start == b_start

    # An insertion at position X overlaps a regular hunk [start, end] if start <= X <= end.
    if a.line_info.old_count == 0:
        return a_start >= b_start and a_start <= b_end
    if b.line_info.old_count == 0:
        return b_start >= a_start and b_start <= a_end

    return not (a_end < b_start or b_end < a_start)


def calculate_overlap_size(a: HunkLineInfo, b: HunkLineInfo) -> int:
    """Count of overlapping baseline lines between two hunks (grok ``calculate_overlap_size``)."""
    a_start = a.old_start
    a_end = a.old_start + a.old_count
    b_start = b.old_start
    b_end = b.old_start + b.old_count
    overlap_start = max(a_start, b_start)
    overlap_end = min(a_end, b_end)
    return max(overlap_end - overlap_start, 0)


def find_matching_old_hunk(new_hunk: Hunk, old_hunks: Sequence[Hunk]) -> Hunk | None:
    """Best matching old hunk for ``new_hunk`` (grok ``find_matching_old_hunk``).

    Priority: (1) exact content match closest by new-file line position (handles
    identical changes at multiple locations, e.g. a variable rename), then
    (2) the overlapping hunk with the maximum baseline overlap size. Returns
    ``None`` when nothing content-matches and nothing overlaps.
    """
    content_matches = [o for o in old_hunks if hunks_match_content(o, new_hunk)]
    if content_matches:
        return min(
            content_matches,
            key=lambda o: abs(o.line_info.new_start - new_hunk.line_info.new_start),
        )
    return max(
        (o for o in old_hunks if hunks_overlap(o, new_hunk)),
        key=lambda o: calculate_overlap_size(o.line_info, new_hunk.line_info),
        default=None,
    )


def find_overlapping_hunks(new_hunk: Hunk, old_hunks: Sequence[Hunk]) -> list[Hunk]:
    """All old hunks overlapping ``new_hunk`` (grok ``find_overlapping_hunks``)."""
    return [o for o in old_hunks if hunks_overlap(o, new_hunk)]


__all__ = [
    "CONTEXT_LINES",
    "MAX_DIFF_FILE_SIZE",
    "DIFF_TIMEOUT",
    "compute_hunks",
    "generate_unified_patch",
    "generate_hunk_patch",
    "format_unified_diff",
    "patch_lines",
    "hunks_match_content",
    "hunk_moved",
    "hunks_overlap",
    "calculate_overlap_size",
    "find_matching_old_hunk",
    "find_overlapping_hunks",
]
