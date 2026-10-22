import json

import pytest

from minimax_code.workspace.child import load_child, prepare_child


async def test_removed_child_directory_requires_review(tmp_path):
    child = await prepare_child(tmp_path, "removed")
    child.root.rmdir()
    with pytest.raises(ValueError):
        child.finish("completed")
    assert load_child(tmp_path, "removed").record["state"] == "needs_review"


async def test_unreadable_directory_is_not_silently_skipped(tmp_path, monkeypatch):
    child = await prepare_child(tmp_path, "unreadable")

    def broken_walk(root, **kwargs):
        error = PermissionError("input directory unreadable")
        callback = kwargs.get("onerror")
        if callback:
            callback(error)
        return iter(())

    monkeypatch.setattr("minimax_code.workspace.child.os.walk", broken_walk)
    with pytest.raises(PermissionError, match="unreadable"):
        child.finish("completed")
    assert load_child(tmp_path, "unreadable").record["state"] == "needs_review"


@pytest.mark.parametrize("field,value", [("baseline", []), ("state", "bogus"),
                                         ("baseline", {"../outside": "0" * 64})])
async def test_corrupt_manifest_is_rejected(tmp_path, field, value):
    child = await prepare_child(tmp_path, "corrupt")
    record = child.record | {field: value}
    child.manifest.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError):
        load_child(tmp_path, "corrupt")


async def test_input_size_failure_still_records_needs_review(tmp_path, monkeypatch):
    (tmp_path / "large.txt").write_text("12345")
    monkeypatch.setattr("minimax_code.workspace.child.MAX_FILE_BYTES", 4)
    with pytest.raises(ValueError, match="exceeds"):
        await prepare_child(tmp_path, "limited")
    assert load_child(tmp_path, "limited").record["state"] == "needs_review"
