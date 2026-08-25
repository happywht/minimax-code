"""v1.5.0 CAS regression tests — sha256 optimistic locking on write tools.

Field report (concurrency stress, 3 parallel sub-agents): write-write to
the same file is last-write-wins with no conflict detection. These tests
pin the new contract:

* ``read_file`` fingerprints the full on-disk bytes (``sha256``, null
  when truncated — CAS unavailable for oversized files).
* ``write_file`` / ``edit_file`` accept ``expected_sha256`` and refuse
  to run when the file's current hash differs, reporting the fresh hash
  so the model can re-read and reapply.
* Successful writes report ``previous_sha256`` + ``sha256`` for the
  next optimistic lock.

All tool exercises go through ``registry.dispatch`` — the project
convention since v1.4.1 (calling ``tool.run`` directly bypasses the
routing layer the LLM actually hits).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from minimax_code.agent.tools import get_default_registry
from minimax_code.agent.tools.file_ops import file_sha256


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the safety policy at ``tmp_path`` for the duration of the test."""
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(tmp_path))
    return tmp_path


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _dispatch(name: str, args: dict) -> object:
    reg = get_default_registry()
    return await reg.dispatch(name, args)


# ---------------------------------------------------------------------------
# file_sha256 helper
# ---------------------------------------------------------------------------


def test_file_sha256_matches_hashlib_and_none_on_missing(workspace: Path) -> None:
    f = workspace / "bin.dat"
    payload = b"the quick brown fox" * 1000
    f.write_bytes(payload)
    assert file_sha256(f) == hashlib.sha256(payload).hexdigest()
    assert file_sha256(workspace / "missing.bin") is None


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


async def test_read_file_returns_sha256_of_full_file(workspace: Path) -> None:
    f = workspace / "a.txt"
    payload = b"hello cas\n"
    f.write_bytes(payload)
    result = await _dispatch("read_file", {"path": "a.txt"})
    assert result.success, result.error
    sha = result.output["sha256"]
    assert isinstance(sha, str) and len(sha) == 64
    assert sha == _sha(payload)


async def test_read_file_sha_null_when_truncated(workspace: Path) -> None:
    f = workspace / "big.txt"
    f.write_bytes(b"x" * 2048)
    result = await _dispatch(
        "read_file", {"path": "big.txt", "max_bytes": 1024}
    )
    assert result.success, result.error
    assert result.output["truncated"] is True
    assert result.output["sha256"] is None


async def test_read_file_sha_covers_whole_file_despite_line_slice(
    workspace: Path,
) -> None:
    f = workspace / "lines.txt"
    payload = b"one\ntwo\nthree\nfour\n"
    f.write_bytes(payload)
    result = await _dispatch(
        "read_file", {"path": "lines.txt", "start_line": 2, "end_line": 3}
    )
    assert result.success, result.error
    assert result.output["content"] == "two\nthree\n"
    assert result.output["sha256"] == _sha(payload)


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


async def test_write_file_previous_sha_null_on_create_sha_on_success(
    workspace: Path,
) -> None:
    result = await _dispatch("write_file", {"path": "new.txt", "content": "v1"})
    assert result.success, result.error
    assert result.output["created"] is True
    assert result.output["previous_sha256"] is None
    assert result.output["sha256"] == _sha(b"v1")


async def test_write_file_second_write_reports_previous_sha_equal_to_first(
    workspace: Path,
) -> None:
    first = await _dispatch("write_file", {"path": "f.txt", "content": "v1"})
    assert first.success, first.error
    second = await _dispatch("write_file", {"path": "f.txt", "content": "v2"})
    assert second.success, second.error
    assert second.output["overwritten"] is True
    assert second.output["previous_sha256"] == first.output["sha256"]
    assert second.output["sha256"] == _sha(b"v2")


async def test_write_file_cas_mismatch_fails_with_current_sha(
    workspace: Path,
) -> None:
    stale = _sha(b"what the model last saw")
    Path(workspace, "c.txt").write_bytes(b"what the model last saw")
    # Someone else mutates the file after the model's read.
    Path(workspace, "c.txt").write_bytes(b"meanwhile, someone else wrote")

    result = await _dispatch(
        "write_file",
        {"path": "c.txt", "content": "mine now", "expected_sha256": stale},
    )
    assert not result.success
    assert "re-read" in (result.error or "")
    assert result.output["current_sha256"] == _sha(
        b"meanwhile, someone else wrote"
    )
    assert result.output["file_exists"] is True
    # On-disk content unchanged — the write was refused, not clobbered.
    assert Path(workspace, "c.txt").read_bytes() == b"meanwhile, someone else wrote"


async def test_write_file_cas_match_succeeds(workspace: Path) -> None:
    Path(workspace, "ok.txt").write_bytes(b"base")
    result = await _dispatch(
        "write_file",
        {
            "path": "ok.txt",
            "content": "updated",
            "expected_sha256": _sha(b"base"),
        },
    )
    assert result.success, result.error
    assert Path(workspace, "ok.txt").read_bytes() == b"updated"
    assert result.output["previous_sha256"] == _sha(b"base")


async def test_write_file_expected_but_file_deleted_fails(
    workspace: Path,
) -> None:
    result = await _dispatch(
        "write_file",
        {
            "path": "gone.txt",
            "content": "recreate?",
            "expected_sha256": _sha(b"used to exist"),
        },
    )
    assert not result.success
    assert result.output["file_exists"] is False


async def test_write_file_malformed_expected_fails(workspace: Path) -> None:
    result = await _dispatch(
        "write_file",
        {"path": "x.txt", "content": "v", "expected_sha256": "not-a-hash"},
    )
    assert not result.success
    assert "64-char hex" in (result.error or "")


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------


async def test_edit_file_cas_mismatch_leaves_file_untouched(
    workspace: Path,
) -> None:
    Path(workspace, "e.txt").write_bytes(b"alpha\nbeta\n")
    result = await _dispatch(
        "edit_file",
        {
            "path": "e.txt",
            "old_string": "alpha",
            "new_string": "gamma",
            "expected_sha256": _sha(b"stale fingerprint\n"),
        },
    )
    assert not result.success
    assert "re-read" in (result.error or "")
    # Byte-for-byte unchanged and no backup minted for a blocked edit.
    assert Path(workspace, "e.txt").read_bytes() == b"alpha\nbeta\n"
    assert not (workspace / ".minimax" / "backups").exists()


async def test_edit_file_reports_previous_and_new_sha(workspace: Path) -> None:
    payload = b"keep\nold line\nkeep\n"
    Path(workspace, "rt.txt").write_bytes(payload)
    read = await _dispatch("read_file", {"path": "rt.txt"})
    assert read.success, read.error

    edit = await _dispatch(
        "edit_file",
        {
            "path": "rt.txt",
            "old_string": "old line",
            "new_string": "new line",
            "expected_sha256": read.output["sha256"],
        },
    )
    assert edit.success, edit.error
    # Round-trip: the fingerprint read_file handed out must be exactly
    # what edit_file saw as its previous state.
    assert edit.output["previous_sha256"] == read.output["sha256"]
    assert edit.output["sha256"] == file_sha256(Path(workspace, "rt.txt"))
