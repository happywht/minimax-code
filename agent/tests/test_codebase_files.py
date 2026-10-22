from __future__ import annotations

import pytest

from minimax_code.codebase.files import discover_sources, read_source


def test_source_discovery_is_bounded_and_skips_build_directories(tmp_path):
    (tmp_path / "a.py").write_bytes(b"a")
    (tmp_path / "b.py").write_bytes(b"b")
    (tmp_path / "huge.py").write_bytes(b"x" * 20)
    dependencies = tmp_path / "node_modules"
    dependencies.mkdir()
    (dependencies / "hidden.py").write_bytes(b"x")
    found = discover_sources(tmp_path, supported=lambda p: p.suffix == ".py",
                             max_files=10, max_file_size=10)
    assert [row[0] for row in found] == ["a.py", "b.py"]
    limited = discover_sources(tmp_path, supported=lambda p: p.suffix == ".py",
                               max_files=1, max_file_size=10)
    assert [row[0] for row in limited] == ["a.py"]


def test_source_read_revalidates_a_file_replaced_by_symlink(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "a.py"
    source.write_bytes(b"original")
    assert discover_sources(root, supported=lambda _: True, max_files=10, max_file_size=100)
    external = tmp_path / "outside.py"
    external.write_bytes(b"private")
    source.unlink()
    try:
        source.symlink_to(external)
    except OSError:
        pytest.skip("symlink requires OS permission")
    with pytest.raises(ValueError):
        read_source(root, "a.py", max_file_size=100)
    assert not discover_sources(root, supported=lambda _: True, max_files=10, max_file_size=100)


def test_source_growth_does_not_bypass_read_limit(tmp_path):
    (tmp_path / "a.py").write_bytes(b"x" * 20)
    with pytest.raises(ValueError, match="size limit"):
        read_source(tmp_path, "a.py", max_file_size=10)
