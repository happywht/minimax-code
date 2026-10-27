"""Strict mountinfo decoding and lexical backing-coordinate admission."""

from __future__ import annotations

import io
import sys
from pathlib import PurePosixPath as P

import pytest

from minimax_code import process_mounts as module
from minimax_code.process_types import ProcessBoundaryError
from minimax_code.workspace.child import _included

pytestmark = pytest.mark.security
BASE = b"1 99 8:1 / / rw,relatime - ext4 /dev/root rw\n"
AUTOFS = b"""29 1 253:2 / / rw,relatime shared:1 - ext4 /dev/vda2 rw
25 29 0:23 / /proc rw,nosuid,nodev,noexec,relatime shared:12 - proc proc rw
36 25 0:31 / /proc/sys/fs/binfmt_misc rw,relatime shared:13 - autofs systemd-1 rw,fd=32,pgrp=1,timeout=0,minproto=5,maxproto=5,direct,pipe_ino=3583
44 36 0:34 / /proc/sys/fs/binfmt_misc rw,nosuid,nodev,noexec,relatime shared:29 - binfmt_misc binfmt_misc rw
"""


def row(point, *, root="/", device="8:1", mount_id=2, parent=1, filesystem="ext4", source="/dev/root"):
    return f"{mount_id} {parent} {device} {root} {point} rw - {filesystem} {source} rw\n".encode()


def table(*rows):
    return module.parse_mountinfo(BASE + b"".join(rows))


@pytest.mark.parametrize("encoded,decoded", [
    (r"/space\040here", "/space here"), (r"/tab\011here", "/tab\there"),
    (r"/line\012here", "/line\nhere"), (r"/slash\134here", "/slash\\here"),
    (r"/literal\134040", r"/literal\040"), ("/percent%20", "/percent%20"),
])
def test_kernel_escapes_are_decoded_exactly_once(encoded, decoded):
    mount = table(row(encoded, root=encoded)).mounts[1]
    assert mount.point == P(decoded) and mount.root == P(decoded)


@pytest.mark.parametrize("encoded", [r"/bad\041", r"/bad\000", r"/bad\x20", r"/bad\u0020",
                                      "/bad\\", "/bad\\04"])
def test_unknown_and_truncated_escapes_are_rejected(encoded):
    with pytest.raises(ProcessBoundaryError, match="escape"):
        table(row(encoded))


@pytest.mark.parametrize("path", ["relative", "//double", "/trailing/", "/double//slash",
                                    "/a/../b", "/a/./b"])
def test_mount_paths_are_absolute_and_canonical(path):
    with pytest.raises(ProcessBoundaryError, match="absolute and canonical"):
        table(row(path))


@pytest.mark.parametrize("record", [
    b"", b"\n", BASE[:-1], BASE.replace(b" - ", b"  "),
    BASE.replace(b" - ", b" - - "), BASE.replace(b" rw,relatime ", b"  rw,relatime "),
    BASE.replace(b"8:1", b"8"), BASE.replace(b"8:1", b"08:1"),
    BASE.replace(b"8:1", b"8:-1"), BASE.replace(b"8:1", b"4294967296:1"),
    BASE.replace(b"1 99 ", b"0 99 "), BASE.replace(b"1 99 ", b"01 99 "),
    BASE.replace(b" rw\n", b"\n"), BASE.replace(b" rw\n", b" rw extra\n"),
    BASE.replace(b" rw\n", b" rw,\n"), BASE.replace(b"/dev/root", b"/dev/\xff"),
    BASE.replace(b"/dev/root", b"/dev/\x00root"),
    BASE.replace(b"/dev/root", b"/dev/\rroot"),
])
def test_malformed_empty_and_truncated_snapshots_fail_closed(record):
    with pytest.raises(ProcessBoundaryError):
        module.parse_mountinfo(record)


@pytest.mark.parametrize("field", [b"shared:0", b"master", b"propagate_from:abc", b"bad\\040tag"])
def test_malformed_optional_fields_fail_closed(field):
    with pytest.raises(ProcessBoundaryError):
        module.parse_mountinfo(BASE.replace(b" - ", b" " + field + b" - "))


def test_unknown_wellformed_optional_fields_do_not_change_mount_authority():
    parsed = module.parse_mountinfo(BASE.replace(b" - ", b" shared:12 master:2 future:yes unbindable - "))
    assert parsed.backing(P("/project")).path == P("/project")


@pytest.mark.parametrize("extra", [row("/other", mount_id=1), row("/a", parent=99),
                                     row("/a", parent=2)])
def test_duplicate_ids_and_invalid_parents_fail_closed(extra):
    with pytest.raises(ProcessBoundaryError):
        table(extra)


def test_root_and_parent_coverage_are_required():
    with pytest.raises(ProcessBoundaryError, match="root mount"):
        module.parse_mountinfo(row("/project"))
    with pytest.raises(ProcessBoundaryError, match="parent"):
        table(row("/a"), row("/sibling", mount_id=3, parent=2))
    with pytest.raises(ProcessBoundaryError, match="parent"):
        table(row("/a"), row("/a/nested", mount_id=3, parent=1))


def test_unrelated_real_autofs_stack_is_preserved_without_guessing_visible_top():
    for lines in (AUTOFS.splitlines(keepends=True), reversed(AUTOFS.splitlines(keepends=True))):
        parsed = module.parse_mountinfo(b"".join(lines))
        assert parsed.ambiguous_points == {P("/proc/sys/fs/binfmt_misc")}
        parsed.reject_sensitive_aliases(P("/tmp/ordinary"), home=P("/home/user"),
                                        protected_roots=(P("/service/data"),))
        parsed.reject_included_mounts(P("/tmp/ordinary"), _included)
        with pytest.raises(ProcessBoundaryError, match="ambiguous mount ancestor"):
            parsed.backing(P("/proc/sys/fs/binfmt_misc/register"))


@pytest.mark.parametrize("point", ["/project", "/home/user", "/service/data", "/home/user/.ssh", "/runtime"])
def test_ambiguity_in_workspace_home_or_protected_path_fails_closed(point):
    parsed = table(row(point, root="/first"), row(point, root="/second", mount_id=3, parent=2))
    with pytest.raises(ProcessBoundaryError, match="ambiguous mount ancestor"):
        parsed.reject_sensitive_aliases(P("/project"), home=P("/home/user"),
            protected_roots=(P("/service/data"), P("/home/user/.ssh"), P("/runtime")))


@pytest.mark.parametrize("parent", [2, 3])
def test_unique_deeper_mount_does_not_erase_ambiguous_ancestor(parent):
    parsed = table(row("/ambiguous", root="/first"),
                   row("/ambiguous", root="/second", mount_id=3, parent=2),
                   row("/ambiguous/deeper", root="/unique", mount_id=4, parent=parent))
    assert parsed.ambiguous_points == {P("/ambiguous")}
    with pytest.raises(ProcessBoundaryError, match="ambiguous mount ancestor"):
        parsed.backing(P("/ambiguous/deeper/file"))
    assert parsed.backing(P("/other")).path == P("/other")


def test_root_stack_makes_every_backing_coordinate_ambiguous():
    parsed = table(row("/", root="/other-root"), row("/project", mount_id=3, parent=2))
    for point in ("/", "/project", "/home/user", "/service/data"):
        with pytest.raises(ProcessBoundaryError, match="ambiguous mount ancestor"):
            parsed.backing(P(point))


@pytest.mark.parametrize("records", [
    BASE.replace(b"1 99 ", b"1 1 "),
    BASE + row("/same", parent=2),
    BASE + row("/same", parent=3) + row("/same", mount_id=3, parent=2),
    BASE + row("/layer", parent=3) + row("/layer/deeper", mount_id=3, parent=2),
    BASE.replace(b"1 99 ", b"1 2 ") + row("/", parent=1),
])
def test_visible_parent_cycles_and_cross_layer_cycles_are_globally_rejected(records):
    with pytest.raises(ProcessBoundaryError):
        module.parse_mountinfo(records)


def test_stacked_included_mounts_reject_but_omitted_stack_is_not_traversed():
    included = table(row("/project/vendor"), row("/project/vendor", mount_id=3, parent=2))
    with pytest.raises(ProcessBoundaryError, match="mounted files or directories"):
        included.reject_included_mounts(P("/project"), _included)
    omitted = table(row("/project/node_modules"),
                    row("/project/node_modules", mount_id=3, parent=2))
    omitted.reject_sensitive_aliases(P("/project"), home=P("/home/user"),
                                    protected_roots=(P("/service/data"),))
    omitted.reject_included_mounts(P("/project"), _included)


@pytest.mark.parametrize("limit", ["MAX_MOUNTINFO_BYTES", "MAX_MOUNTINFO_LINE_BYTES", "MAX_MOUNTINFO_LINES"])
def test_snapshot_limits_are_enforced(monkeypatch, limit):
    monkeypatch.setattr(module, limit, 1)
    with pytest.raises(ProcessBoundaryError):
        table(row("/project"))


def test_read_is_bounded_and_read_errors_fail_closed(monkeypatch):
    monkeypatch.setattr(module.sys, "platform", "linux")
    reads = []

    class Stream(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)

    class Source:
        def open(self, mode):
            assert mode == "rb"
            return Stream(BASE)

    monkeypatch.setattr(module, "_MOUNTINFO", Source())
    assert module.read_mount_table().mounts[0].point == P("/")
    assert reads == [module.MAX_MOUNTINFO_BYTES + 1]

    class Broken:
        def open(self, mode):
            raise PermissionError("denied")

    monkeypatch.setattr(module, "_MOUNTINFO", Broken())
    with pytest.raises(ProcessBoundaryError, match="could not be read"):
        module.read_mount_table()


def test_non_linux_keeps_existing_behavior_without_reading(monkeypatch):
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(module, "_MOUNTINFO", None)
    assert module.read_mount_table() is None


@pytest.mark.parametrize("point", ["/project/file.txt", "/project/vendor", "/project/nested/mount"])
@pytest.mark.parametrize("device", ["8:1", "9:2"])
def test_all_included_mounts_are_rejected_regardless_of_device_or_bind_options(point, device):
    with pytest.raises(ProcessBoundaryError, match="mounted files or directories"):
        table(row(point, device=device)).reject_included_mounts(P("/project"), _included)


def test_component_relations_allow_root_base_mount_prefix_siblings_and_omitted_descendants():
    parsed = table(row("/project", root="/ordinary-project"),
                   row("/project-other", mount_id=3),
                   row("/project/node_modules", mount_id=4, parent=2),
                   row("/project/node_modules/nested", mount_id=5, parent=4),
                   row("/project/token.key", mount_id=6, parent=2),
                   row("/project/token.key/inner", mount_id=7, parent=6))
    parsed.reject_included_mounts(P("/project"), _included)


def test_backing_coordinates_use_deepest_covering_mount_and_ignore_source_text():
    parsed = table(row("/project", root="/real/project", source="/pretend/secret"),
                   row("/project/nested", root="/real/nested", mount_id=3, parent=2))
    assert parsed.backing(P("/project/file")).path == P("/real/project/file")
    assert parsed.backing(P("/project/nested/file")).path == P("/real/nested/file")


@pytest.mark.parametrize("backing", ["/home/user", "/home", "/"])
def test_root_mount_alias_that_contains_home_is_rejected(backing):
    parsed = table(row("/project", root=backing))
    with pytest.raises(ProcessBoundaryError, match="contains HOME"):
        parsed.reject_sensitive_aliases(P("/project"), home=P("/home/user"))


@pytest.mark.parametrize("backing", ["/service/data", "/service/data/nested", "/service"])
def test_protected_backing_overlap_in_both_directions_is_rejected(backing):
    parsed = table(row("/project", root=backing))
    with pytest.raises(ProcessBoundaryError, match="protected service or credentials"):
        parsed.reject_sensitive_aliases(P("/project"), home=P("/home/user"),
                                        protected_roots=(P("/service/data"),))


def test_protected_alias_itself_uses_its_deepest_mount_backing():
    parsed = table(row("/project", root="/real/data/nested"),
                   row("/service/data", root="/real/data", mount_id=3))
    with pytest.raises(ProcessBoundaryError, match="mount alias"):
        parsed.reject_sensitive_aliases(P("/project"), home=P("/home/user"),
                                        protected_roots=(P("/service/data"),))


def test_normal_home_project_and_distinct_filesystem_coordinates_are_allowed():
    table(row("/project", root="/home/user/project")).reject_sensitive_aliases(
        P("/project"), home=P("/home/user"), protected_roots=(P("/service/data"),))
    table(row("/project", root="/service/data", device="9:1")).reject_sensitive_aliases(
        P("/project"), home=P("/home/user"), protected_roots=(P("/service/data"),))


@pytest.mark.skipif(sys.platform != "linux", reason="real procfs mount table requires Linux")
def test_actual_linux_mount_table_is_parsed_and_covers_workspace():
    from pathlib import Path

    parsed = module.read_mount_table()
    assert parsed.mounts
    assert parsed.backing(Path.cwd()).path.is_absolute()
