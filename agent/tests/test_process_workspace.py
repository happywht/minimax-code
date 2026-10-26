"""Workspace relationship checks use canonical paths and physical identities."""
from __future__ import annotations

from pathlib import Path

import pytest

from minimax_code import process_workspace as module
from minimax_code.process_types import ProcessBoundaryError


@pytest.fixture
def trees(tmp_path, monkeypatch):
    home, data = tmp_path / "home", tmp_path / "service-data"
    home.mkdir()
    data.mkdir()
    project = home / "project"
    project.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home, data, project


def test_normal_home_project_is_allowed(trees):
    home, data, project = trees
    assert module.validate_process_workspace(project, data_dir=data) == project
    nested = project / "nested"
    nested.mkdir()
    assert module.validate_process_workspace(nested, data_dir=data) == nested


@pytest.mark.parametrize("ancestor", [False, True])
def test_home_and_its_parent_are_rejected(trees, ancestor):
    home, data, _ = trees
    with pytest.raises(ProcessBoundaryError, match="contains HOME"):
        module.validate_process_workspace(home.parent if ancestor else home, data_dir=data)


@pytest.mark.parametrize("ancestor", [False, True])
def test_physical_alias_of_home_or_its_parent_is_rejected(trees, monkeypatch, ancestor):
    home, data, project = trees
    alias = project / "alias"
    alias.mkdir()
    identity = module._identity(home.parent if ancestor else home)
    original = module._identity
    monkeypatch.setattr(module, "_identity", lambda path: identity if path == alias else original(path))
    with pytest.raises(ProcessBoundaryError, match="contains HOME"):
        module.validate_process_workspace(alias, data_dir=data)


@pytest.mark.parametrize("relationship", ["equal", "inside", "contains", "physical_alias"])
@pytest.mark.parametrize("protected", ["data", "credentials", "additional"])
def test_protected_trees_reject_both_overlap_directions_and_aliases(trees, monkeypatch, relationship, protected):
    home, data, project = trees
    if protected == "credentials":
        protected_root = home / ".aws"
        protected_root.mkdir()
    elif protected == "additional":
        protected_root = home.parent / "runtime-store"
        protected_root.mkdir()
    else:
        protected_root = data
    roots = (protected_root,) if protected == "additional" else ()
    if relationship == "equal":
        source = protected_root
    elif relationship == "inside":
        source = protected_root / "nested"
        source.mkdir()
    elif relationship == "contains":
        # Place a sensitive root inside an otherwise ordinary HOME project.
        source = project
        protected_root = project / "sensitive"
        protected_root.mkdir()
        if protected == "data":
            data = protected_root
        elif protected == "credentials":
            # Additional protected roots use exactly the same overlap rule as
            # built-in credential roots; the other cases exercise .aws itself.
            roots = (protected_root,)
        else:
            roots = (protected_root,)
    else:
        source = project / "alias"
        source.mkdir()
        identity = module._identity(protected_root)
        original = module._identity
        monkeypatch.setattr(module, "_identity", lambda path: identity if path == source else original(path))
    with pytest.raises(ProcessBoundaryError, match="protected service or credentials"):
        module.validate_process_workspace(source, data_dir=data, protected_roots=roots)
