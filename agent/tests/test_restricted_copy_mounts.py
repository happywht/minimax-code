"""Restricted copying rejects Linux mount aliases before reading their contents.

The real local files and copy pipeline run on Windows too. Only the namespace
coordinate projection and procfs snapshot are Linux fixtures; no stat-device
mock is needed to demonstrate the same-device bind-mount gap.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from minimax_code import app, process_mounts
from minimax_code.process_types import ProcessBoundaryError
from minimax_code.workspace import child as module

pytestmark = pytest.mark.security
BASE = b"1 99 8:1 / / rw,relatime - ext4 /dev/root rw\n"


def coordinate(path):
    text = path.as_posix()
    # A deterministic synthetic Linux namespace for real Windows fixture paths.
    return PurePosixPath(text if text.startswith("/") else "/" + text)


def escaped(path):
    return str(path).replace("\\", r"\134").replace(" ", r"\040").replace("\t", r"\011").replace("\n", r"\012")


def mount_row(point, *, root="/ordinary", device="8:1", mount_id=2, parent=1):
    return f"{mount_id} {parent} {device} {escaped(root)} {escaped(coordinate(point))} rw - ext4 /display-only rw\n".encode()


@pytest.fixture
def state(tmp_path, monkeypatch):
    home = tmp_path / "home"
    parent = home / "project"
    data = tmp_path / "service-data"
    runtime = tmp_path / "runtime-store"
    for directory in (home, parent, data, runtime):
        directory.mkdir(parents=True, exist_ok=True)
    (parent / "ordinary.txt").write_text("ordinary", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    catalog = SimpleNamespace(data_dir=data, supervisor=SimpleNamespace(_protected_roots=(runtime,)),
                              assert_extension_boundary=lambda: None)
    monkeypatch.setattr(app, "get_execution_profiles", lambda: catalog)
    monkeypatch.setattr(process_mounts, "_mount_path", coordinate)
    value = SimpleNamespace(home=home, parent=parent, data=data, runtime=runtime,
                            rows=[], reads=0, before_read=None)

    def read():
        value.reads += 1
        if value.before_read:
            value.before_read(value.reads)
        return process_mounts.parse_mountinfo(BASE + b"".join(value.rows))

    monkeypatch.setattr(process_mounts, "read_mount_table", read)
    return value


@pytest.mark.parametrize("kind", ["directory", "file"])
@pytest.mark.parametrize("device", ["8:1", "9:2"])
async def test_included_mounted_inputs_are_rejected_before_walk_or_copy(state, monkeypatch, kind, device):
    mounted = state.parent / "included"
    if kind == "directory":
        mounted.mkdir()
        (mounted / "canary.txt").write_text("secret", encoding="utf-8")
    else:
        mounted.write_text("secret", encoding="utf-8")
    assert mounted.stat().st_dev == state.parent.stat().st_dev
    state.rows.append(mount_row(mounted, device=device))

    def forbidden(*args, **kwargs):
        raise AssertionError("Mounted content must not be scanned or copied")

    monkeypatch.setattr(module.os, "walk", forbidden)
    monkeypatch.setattr(module, "atomic_copy_file", forbidden)
    with pytest.raises(ProcessBoundaryError, match="mounted files or directories"):
        await module.prepare_child(state.parent, "restricted", restricted=True)
    assert not (state.parent / ".minimax").exists()


@pytest.mark.parametrize("omitted", ["node_modules", "token.key", ".env.private", ".ssh"])
async def test_omitted_mounts_and_their_nested_mounts_are_never_read(state, monkeypatch, omitted):
    target = state.parent / omitted
    target.mkdir()
    secret = target / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    state.rows.extend([mount_row(target), mount_row(target / "secret.txt", mount_id=3, parent=2)])
    original = module._digest

    def digest(path, **kwargs):
        assert not path.is_relative_to(target), "Excluded mount content was read"
        return original(path, **kwargs)

    monkeypatch.setattr(module, "_digest", digest)
    child = await module.prepare_child(state.parent, "omitted", restricted=True)
    assert child.record["baseline"].keys() == {"ordinary.txt"}
    assert not (child.root / omitted).exists()
    assert secret.read_text() == "private"


async def test_ordinary_project_root_mount_and_prefix_sibling_are_valid_bases(state):
    state.rows.extend([mount_row(state.parent, root="/project-volume"),
                       mount_row(state.parent.with_name("project-other"), mount_id=3)])
    child = await module.prepare_child(state.parent, "volume", restricted=True)
    assert (child.root / "ordinary.txt").read_text() == "ordinary"
    assert state.reads == 5  # Initial source, copy source/owned, final source/owned.


async def test_omitted_stacked_mounts_leave_canary_unread(state, monkeypatch):
    omitted = state.parent / "node_modules"
    omitted.mkdir()
    (omitted / "canary.txt").write_text("private", encoding="utf-8")
    state.rows.extend([mount_row(omitted), mount_row(omitted, mount_id=3, parent=2)])
    original = module._digest

    def digest(path, **kwargs):
        assert not path.is_relative_to(omitted), "Omitted stacked mount was read"
        return original(path, **kwargs)

    monkeypatch.setattr(module, "_digest", digest)
    child = await module.prepare_child(state.parent, "omitted-stack", restricted=True)
    assert child.record["baseline"].keys() == {"ordinary.txt"}


async def test_unrelated_system_stack_does_not_block_real_copy_pipeline(state):
    state.rows.extend([mount_row(state.parent.parent.parent / "system-stack"),
                       mount_row(state.parent.parent.parent / "system-stack", mount_id=3, parent=2)])
    child = await module.prepare_child(state.parent, "unrelated-stack", restricted=True)
    assert child.record["state"] == "running"


@pytest.mark.parametrize("protected,relationship", [
    (protected, relationship)
    for protected in ("home", "data", "credentials", "runtime", "home_parent")
    for relationship in ("equal", "inside", "contains")
    if not (protected in {"home", "home_parent"} and relationship == "inside")
])
async def test_root_mount_sensitive_backing_alias_fails_before_reading(
    state, monkeypatch, protected, relationship,
):
    target = {"home": state.home, "home_parent": state.home.parent,
              "data": state.data, "credentials": state.home / ".ssh",
              "runtime": state.runtime}[protected]
    target.mkdir(exist_ok=True)
    backing = coordinate(target)
    if relationship == "inside":
        backing = backing / "nested"
    elif relationship == "contains":
        backing = backing.parent
    state.rows.append(mount_row(state.parent, root=str(backing)))
    original = module._digest

    def digest(path, **kwargs):
        assert not path.is_relative_to(state.parent), "Aliased source content was read"
        return original(path, **kwargs)

    monkeypatch.setattr(module, "_digest", digest)
    with pytest.raises(ProcessBoundaryError, match="HOME|protected service or credentials"):
        await module.prepare_child(state.parent, "alias", restricted=True)
    assert not (state.parent / ".minimax").exists()


async def test_root_alias_to_an_ordinary_home_project_is_allowed(state):
    state.rows.append(mount_row(state.parent, root=str(coordinate(state.home) / "other-project")))
    child = await module.prepare_child(state.parent, "home-project", restricted=True)
    assert child.record["state"] == "running"


@pytest.mark.parametrize("phase", ["copy_source", "copy_owned", "final_source", "final_owned"])
async def test_fresh_tables_reject_mounts_arriving_at_each_copy_boundary(state, monkeypatch, phase):
    run_id = "late"
    owned = state.parent / ".minimax" / "child-workspaces" / run_id / "workspace"
    calls = {"copy_source": 2, "copy_owned": 3, "final_source": 4, "final_owned": 5}

    def before_read(count):
        if count == calls[phase]:
            root = owned if phase.endswith("owned") else state.parent
            state.rows.append(mount_row(root / "ordinary.txt"))

    state.before_read = before_read
    copied = []
    original = module.atomic_copy_file

    def copy(source, target, **kwargs):
        copied.append(source)
        return original(source, target, **kwargs)

    monkeypatch.setattr(module, "atomic_copy_file", copy)
    with pytest.raises(ProcessBoundaryError, match="mounted files or directories"):
        await module.prepare_child(state.parent, run_id, restricted=True)
    record = module.load_child(state.parent, run_id).record
    assert record["state"] == "needs_review"
    if phase.startswith("copy_"):
        assert not copied
    else:
        assert copied


@pytest.mark.parametrize("phase", ["copy_source", "copy_owned", "final_source", "final_owned"])
async def test_each_boundary_rechecks_root_sensitive_alias_with_new_mount_table(state, phase):
    owned = state.parent / ".minimax" / "child-workspaces" / "root-swapped" / "workspace"
    calls = {"copy_source": 2, "copy_owned": 3, "final_source": 4, "final_owned": 5}

    def before_read(count):
        if count == calls[phase]:
            root = owned if phase.endswith("owned") else state.parent
            state.rows.append(mount_row(root, root=str(coordinate(state.data))))

    state.before_read = before_read
    with pytest.raises(ProcessBoundaryError, match="mount alias"):
        await module.prepare_child(state.parent, "root-swapped", restricted=True)
    assert module.load_child(state.parent, "root-swapped").record["state"] == "needs_review"


async def test_finish_mount_failure_is_persisted_as_needs_review(state):
    child = await module.prepare_child(state.parent, "finish", restricted=True)
    state.rows.append(mount_row(child.root / "ordinary.txt"))
    with pytest.raises(ProcessBoundaryError, match="mounted files or directories"):
        child.finish("completed")
    assert module.load_child(state.parent, "finish").record["state"] == "needs_review"


@pytest.mark.parametrize("failure", ["empty", "corrupt", "unreadable", "oversized"])
async def test_unusable_mount_snapshot_never_copies_source_content(state, monkeypatch, failure):
    def broken():
        if failure == "unreadable":
            raise ProcessBoundaryError("Restricted mountinfo could not be read")
        data = b"" if failure == "empty" else b"malformed\n"
        if failure == "oversized":
            data = b"x" * (process_mounts.MAX_MOUNTINFO_BYTES + 1)
        return process_mounts.parse_mountinfo(data)

    monkeypatch.setattr(process_mounts, "read_mount_table", broken)
    with pytest.raises(ProcessBoundaryError):
        await module.prepare_child(state.parent, "bad-snapshot", restricted=True)
    assert not (state.parent / ".minimax").exists()


async def test_host_copy_does_not_consult_linux_mounts(state, monkeypatch):
    def forbidden():
        raise AssertionError("Host copy must not consult mountinfo")

    monkeypatch.setattr(process_mounts, "read_mount_table", forbidden)
    child = await module.prepare_child(state.parent, "host")
    assert (child.root / "ordinary.txt").read_text() == "ordinary"
