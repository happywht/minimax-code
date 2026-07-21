"""Tests for :mod:`minimax_code.ipc.handlers_crash` (R231, crash.* consumption surface).

These cover the **read half** of the crash-recovery module: the three IPC
handlers read the persisted report files the R225-R230 write half produced
(``last-crash-report.txt`` + ``history/crash-<ts>.txt``) and never re-run the
one-shot ``check_previous_crash``. The handlers are stateless and fail-open,
so every test asserts the honest "nothing available" shape rather than a
JSON-RPC error when the inputs are missing / malformed.

The ``_FakeServer`` / ``_FakeCtx`` pair mirrors the lightweight pattern used
by the other read-only namespace tests (``git`` / ``telemetry``): no real
``IPCServer`` is spun up -- the contract under test is the handler body, not
the dispatch layer.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from minimax_code.ipc.handlers_crash import register_crash_handlers
from minimax_code.ipc.protocol import INVALID_PARAMS


class _FakeServer:
    """Records ``register`` calls so a handler can be invoked directly."""

    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}

    def register(self, method: str, handler: object) -> None:
        self.handlers[method] = handler


class _FakeCtx:
    """Captures ``reply`` / ``reply_error`` payloads for assertion."""

    def __init__(self) -> None:
        self.replies: list[object] = []
        self.errors: list[tuple[int, str, object]] = []

    async def reply(self, result: object) -> None:
        self.replies.append(result)

    async def reply_error(
        self, code: int, message: str, data: object = None
    ) -> None:
        self.errors.append((code, message, data))


@pytest.fixture
def crash_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> SimpleNamespace:
    """Pin ``crash_dir`` to ``tmp_path/crashes`` and register the handlers.

    ``MINIMAX_CODE_DATA_DIR`` is the same override lever R230's startup wiring
    honours, so the handler resolves the same sandboxed dir as the write half.
    """
    monkeypatch.setenv("MINIMAX_CODE_DATA_DIR", str(tmp_path))
    server = _FakeServer()
    register_crash_handlers(server)
    crash_dir = tmp_path / "crashes"
    return SimpleNamespace(server=server, crash_dir=crash_dir, data_dir=tmp_path)


async def _call(
    server: _FakeServer, method: str, params: object = None
) -> _FakeCtx:
    """Invoke ``method``'s handler with ``params`` and return the captured ctx."""
    ctx = _FakeCtx()
    await server.handlers[method](params, ctx)  # type: ignore[index]
    return ctx


class TestCrashPreviousReport:
    """``crash.previous_report`` -> ``{available, report_text}``."""

    async def test_no_report_available_false(self, crash_env: SimpleNamespace) -> None:
        # No crash dir, no report -> honest "nothing to show".
        ctx = await _call(crash_env.server, "crash.previous_report")
        assert ctx.errors == []
        assert ctx.replies == [{"available": False, "report_text": None}]

    async def test_report_present_available_true(self, crash_env: SimpleNamespace) -> None:
        crash_env.crash_dir.mkdir(parents=True)
        (crash_env.crash_dir / "last-crash-report.txt").write_text(
            "SIGBUS at 0xdeadbeef\n", encoding="utf-8"
        )
        ctx = await _call(crash_env.server, "crash.previous_report")
        assert ctx.errors == []
        assert ctx.replies == [
            {"available": True, "report_text": "SIGBUS at 0xdeadbeef\n"}
        ]

    async def test_params_none_accepted(self, crash_env: SimpleNamespace) -> None:
        # None is the explicit "no params" shape (stdio transport passes None).
        ctx = await _call(crash_env.server, "crash.previous_report", None)
        assert ctx.errors == []
        assert ctx.replies == [{"available": False, "report_text": None}]

    async def test_empty_object_accepted(self, crash_env: SimpleNamespace) -> None:
        # ``{}`` is the canonical empty object from the HTTP transport.
        ctx = await _call(crash_env.server, "crash.previous_report", {})
        assert ctx.errors == []
        assert ctx.replies == [{"available": False, "report_text": None}]

    async def test_extra_params_rejected(self, crash_env: SimpleNamespace) -> None:
        ctx = await _call(crash_env.server, "crash.previous_report", {"x": 1})
        assert ctx.errors == [
            (INVALID_PARAMS, "crash.* methods take no parameters", None)
        ]
        assert ctx.replies == []


class TestCrashHistory:
    """``crash.history`` -> ``{entries: [{filename, timestamp, report_text}]}``."""

    async def test_empty_history(self, crash_env: SimpleNamespace) -> None:
        ctx = await _call(crash_env.server, "crash.history")
        assert ctx.errors == []
        assert ctx.replies == [{"entries": []}]

    async def test_three_entries_descending(self, crash_env: SimpleNamespace) -> None:
        history = crash_env.crash_dir / "history"
        history.mkdir(parents=True)
        for ts in (1000, 2000, 3000):
            (history / f"crash-{ts}.txt").write_text(
                f"crash {ts}\n", encoding="utf-8"
            )
        ctx = await _call(crash_env.server, "crash.history")
        assert ctx.errors == []
        entries = ctx.replies[0]["entries"]  # type: ignore[index]
        assert [e["timestamp"] for e in entries] == [3000, 2000, 1000]
        assert entries[0]["filename"] == "crash-3000.txt"
        assert entries[0]["report_text"] == "crash 3000\n"

    async def test_non_crash_files_skipped(self, crash_env: SimpleNamespace) -> None:
        history = crash_env.crash_dir / "history"
        history.mkdir(parents=True)
        (history / "crash-1000.txt").write_text("real\n", encoding="utf-8")
        (history / "README.txt").write_text("noise\n", encoding="utf-8")
        (history / "crash-not-a-number.txt").write_text("noise\n", encoding="utf-8")
        ctx = await _call(crash_env.server, "crash.history")
        assert ctx.errors == []
        entries = ctx.replies[0]["entries"]  # type: ignore[index]
        assert len(entries) == 1
        assert entries[0]["filename"] == "crash-1000.txt"

    async def test_capped_at_fifty(self, crash_env: SimpleNamespace) -> None:
        # A pathological history (55 files) must not stream more than
        # ``_HISTORY_MAX_ENTRIES`` over the wire; the newest 50 win.
        history = crash_env.crash_dir / "history"
        history.mkdir(parents=True)
        for ts in range(1000, 1000 + 55):  # ts 1000..1054, 55 files
            (history / f"crash-{ts}.txt").write_text(
                f"crash {ts}\n", encoding="utf-8"
            )
        ctx = await _call(crash_env.server, "crash.history")
        assert ctx.errors == []
        entries = ctx.replies[0]["entries"]  # type: ignore[index]
        assert len(entries) == 50
        assert entries[0]["timestamp"] == 1054  # newest first


class TestCrashDismiss:
    """``crash.dismiss`` -> ``{dismissed}`` (removes the report, keeps history)."""

    async def test_dismiss_present_report(self, crash_env: SimpleNamespace) -> None:
        crash_env.crash_dir.mkdir(parents=True)
        report = crash_env.crash_dir / "last-crash-report.txt"
        report.write_text("crash\n", encoding="utf-8")
        ctx = await _call(crash_env.server, "crash.dismiss")
        assert ctx.errors == []
        assert ctx.replies == [{"dismissed": True}]
        assert not report.exists()

    async def test_dismiss_absent_report(self, crash_env: SimpleNamespace) -> None:
        ctx = await _call(crash_env.server, "crash.dismiss")
        assert ctx.errors == []
        assert ctx.replies == [{"dismissed": False}]

    async def test_dismiss_keeps_history(
        self, crash_env: SimpleNamespace
    ) -> None:
        # Dismiss only clears the prompt's report; the archival history is
        # untouched so ``crash.history`` still lists past crashes.
        crash_env.crash_dir.mkdir(parents=True)
        report = crash_env.crash_dir / "last-crash-report.txt"
        report.write_text("crash\n", encoding="utf-8")
        history = crash_env.crash_dir / "history" / "crash-1000.txt"
        history.parent.mkdir(parents=True)
        history.write_text("archived\n", encoding="utf-8")
        await _call(crash_env.server, "crash.dismiss")
        assert not report.exists()
        assert history.exists()


class TestRegistration:
    """``register_crash_handlers`` wires exactly the three methods."""

    def test_three_methods_registered(self, crash_env: SimpleNamespace) -> None:
        assert set(crash_env.server.handlers) == {
            "crash.previous_report",
            "crash.history",
            "crash.dismiss",
        }
