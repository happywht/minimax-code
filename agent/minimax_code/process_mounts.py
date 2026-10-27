"""Bounded Linux mount snapshots for restricted workspace admission.

Mount points are lexical namespace paths. Resolving them through the host
filesystem or inferring bind sources from the display-only source field would
lose the backing coordinates required to reject aliases.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .process_types import ProcessBoundaryError

MAX_MOUNTINFO_BYTES = 4 * 1024 * 1024
MAX_MOUNTINFO_LINES = 16_384
MAX_MOUNTINFO_LINE_BYTES = 65_536
_MOUNTINFO = Path("/proc/self/mountinfo")
_ESCAPES = {"040": " ", "011": "\t", "012": "\n", "134": "\\"}
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z", re.ASCII)
_TYPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*\Z", re.ASCII)
_OPTIONAL = re.compile(r"[A-Za-z0-9_.-]+(?::[A-Za-z0-9_.:,=/+-]+)?\Z", re.ASCII)


def _invalid(message: str) -> ProcessBoundaryError:
    return ProcessBoundaryError(f"Restricted mountinfo is incompatible: {message}")


def _number(value: str, *, positive=False) -> int:
    if len(value) > 10 or not _DECIMAL.fullmatch(value):
        raise _invalid("invalid numeric field")
    result = int(value)
    if result > 0xFFFFFFFF or (positive and result == 0):
        raise _invalid("numeric field out of range")
    return result


def _decode(value: str) -> str:
    """Decode the four kernel path escapes exactly once, never recursively."""
    result = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\":
            escape = value[index + 1:index + 4]
            if escape not in _ESCAPES or len(escape) != 3:
                raise _invalid("unknown or truncated kernel escape")
            result.append(_ESCAPES[escape])
            index += 4
        else:
            if ord(char) < 32 or ord(char) == 127:
                raise _invalid("unescaped control character")
            result.append(char)
            index += 1
    return "".join(result)


def _canonical(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (not value.startswith("/") or value.startswith("//")
            or ".." in path.parts or path.as_posix() != value):
        raise _invalid("path must be absolute and canonical")
    return path


def _mount_path(path: Path | PurePosixPath) -> PurePosixPath:
    return _canonical(path.as_posix())


@dataclass(frozen=True)
class Mount:
    id: int
    parent_id: int
    device: tuple[int, int]
    root: PurePosixPath
    point: PurePosixPath
    filesystem: str


@dataclass(frozen=True)
class BackingPath:
    device: tuple[int, int]
    filesystem: str
    path: PurePosixPath


@dataclass(frozen=True)
class MountTable:
    mounts: tuple[Mount, ...]
    ambiguous_points: frozenset[PurePosixPath] = field(init=False)

    def __post_init__(self):
        counts = Counter(mount.point for mount in self.mounts)
        object.__setattr__(self, "ambiguous_points",
                           frozenset(point for point, count in counts.items() if count > 1))

    def backing(self, path: Path | PurePosixPath) -> BackingPath:
        path = _mount_path(path)
        if any(path.is_relative_to(point) for point in self.ambiguous_points):
            raise _invalid("path has an ambiguous mount ancestor")
        covering = [mount for mount in self.mounts if path.is_relative_to(mount.point)]
        if not covering:
            raise _invalid("path has no covering mount")
        mount = max(covering, key=lambda item: len(item.point.parts))
        return BackingPath(mount.device, mount.filesystem,
                           mount.root / path.relative_to(mount.point))

    def reject_included_mounts(self, root: Path | PurePosixPath,
                               included: Callable[[str], bool]) -> None:
        root = _mount_path(root)
        for mount in self.mounts:
            if mount.point == root or not mount.point.is_relative_to(root):
                continue
            relative = mount.point.relative_to(root)
            if all(included(PurePosixPath(*relative.parts[:index]).as_posix())
                   for index in range(1, len(relative.parts) + 1)):
                raise ProcessBoundaryError("Restricted copy cannot include mounted files or directories")

    def reject_sensitive_aliases(self, root: Path | PurePosixPath, *,
                                 home: Path | PurePosixPath, protected_roots=()) -> None:
        backing = self.backing(root)
        home_backing = self.backing(home)
        if self._same_filesystem(backing, home_backing) and home_backing.path.is_relative_to(backing.path):
            raise ProcessBoundaryError("Restricted workspace contains HOME through a mount alias")
        for protected in protected_roots:
            other = self.backing(protected)
            if self._same_filesystem(backing, other) and (
                    backing.path.is_relative_to(other.path) or other.path.is_relative_to(backing.path)):
                raise ProcessBoundaryError("Restricted workspace overlaps a protected service or credentials tree through a mount alias")

    @staticmethod
    def _same_filesystem(first: BackingPath, second: BackingPath) -> bool:
        return (first.device, first.filesystem) == (second.device, second.filesystem)


def parse_mountinfo(data: bytes) -> MountTable:
    if not data or len(data) > MAX_MOUNTINFO_BYTES or not data.endswith(b"\n"):
        raise _invalid("empty, truncated, or oversized snapshot")
    lines = data.split(b"\n", MAX_MOUNTINFO_LINES + 1)
    if len(lines) > MAX_MOUNTINFO_LINES + 1:
        raise _invalid("too many mounts")
    lines = lines[:-1]
    mounts = []
    ids = set()
    points = set()
    for raw in lines:
        if len(raw) + 1 > MAX_MOUNTINFO_LINE_BYTES:
            raise _invalid("mount record is oversized")
        try:
            line = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise _invalid("mount record is not UTF-8") from exc
        if line.count(" - ") != 1:
            raise _invalid("missing or ambiguous separator")
        before, after = line.split(" - ")
        fields, trailing = before.split(" "), after.split(" ")
        if len(fields) < 6 or len(trailing) != 3 or not all((*fields, *trailing)):
            raise _invalid("incomplete mount record")
        mount_id, parent_id = (_number(value, positive=True) for value in fields[:2])
        device = fields[2].split(":")
        if len(device) != 2:
            raise _invalid("invalid device field")
        device = tuple(_number(value) for value in device)
        root, point = (_canonical(_decode(value)) for value in fields[3:5])
        if not _TYPE.fullmatch(trailing[0]):
            raise _invalid("invalid filesystem type")
        for value in (fields[5], trailing[2]):
            if any(not item for item in value.split(",")):
                raise _invalid("empty mount option")
            _decode(value)
        _decode(trailing[1])  # Source is display data, never a bind-source authority.
        for optional in fields[6:]:
            if not _OPTIONAL.fullmatch(optional):
                raise _invalid("invalid optional field")
            if optional.split(":")[0] in {"shared", "master", "propagate_from"}:
                if ":" not in optional:
                    raise _invalid("missing propagation identifier")
                _number(optional.split(":", 1)[1], positive=True)
        if mount_id in ids:
            raise _invalid("duplicate mount IDs")
        ids.add(mount_id)
        points.add(point)
        mounts.append(Mount(mount_id, parent_id, device, root, point, trailing[0]))
    by_id = {mount.id: mount for mount in mounts}
    if PurePosixPath("/") not in points:
        raise _invalid("namespace root mount is missing")
    for mount in mounts:
        parent = by_id.get(mount.parent_id)
        if mount.point == PurePosixPath("/"):
            if parent is not None and parent.point != mount.point:
                raise _invalid("namespace root has an inconsistent parent")
        else:
            nearest_ancestor = next((path for path in mount.point.parents if path in points), None)
            if parent is None or parent.point not in {mount.point, nearest_ancestor}:
                raise _invalid("mount parent is missing or inconsistent")
    complete = set()
    for mount in mounts:
        chain = set()
        current = mount.id
        while current in by_id and current not in complete:
            if current in chain:
                raise _invalid("mount parent chain contains a cycle")
            chain.add(current)
            current = by_id[current].parent_id
        complete.update(chain)
    return MountTable(tuple(mounts))


def read_mount_table() -> MountTable | None:
    """Host/Windows callers retain existing behavior; Linux reads fail closed."""
    if sys.platform != "linux":
        return None
    try:
        with _MOUNTINFO.open("rb") as stream:
            data = stream.read(MAX_MOUNTINFO_BYTES + 1)
    except OSError as exc:
        raise _invalid("snapshot could not be read") from exc
    return parse_mountinfo(data)
