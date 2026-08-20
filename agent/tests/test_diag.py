"""Diagnostics tests (M8 / R45 + R47).

``diag.export`` — sanitized diagnostic bundle: envelope shape,
platform/config sections, per-table row counts, no-storage degradation,
in-memory log tail, and the end-to-end RPC round trip.

R47 adds the sanitization guarantees as explicit assertions: no secret
value (API key, CORS origin) and no absolute user path can appear in a
serialized bundle, and the log tail is scrubbed by the same
``SanitizerFilter`` as the stderr sink.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from minimax_code import __version__
from minimax_code import app as app_module
from minimax_code.ipc.client import IPCClient
from minimax_code.ipc.handlers_diag import (
    DIAG_FORMAT,
    build_diagnostic_bundle,
)
from minimax_code.logging_setup import (
    MEMORY_LOG_TAIL_LINES,
    _MemoryTailHandler,
    _recent_lines,
    get_recent_log_lines,
)
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path
from minimax_code.telemetry.redact import SanitizerFilter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def async_db(tmp_path: Path) -> AsyncDatabase:
    """A migrated async Database backed by a per-test temp file."""
    db = AsyncDatabase(make_temp_database_path(tmp_path))
    await db.connect()
    await db.migrate()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
async def client(async_db: AsyncDatabase) -> IPCClient:
    """IPC client with the DB singleton injected (diag handlers included)."""
    app_module._DB_SINGLETON = async_db
    try:
        yield IPCClient()
    finally:
        app_module._DB_SINGLETON = None


# ---------------------------------------------------------------------------
# build_diagnostic_bundle (pure function)
# ---------------------------------------------------------------------------


class TestBuildBundle:
    async def test_envelope_metadata(self, async_db: AsyncDatabase) -> None:
        bundle = await build_diagnostic_bundle(async_db)
        assert bundle["format"] == DIAG_FORMAT
        assert bundle["version"] == __version__
        assert "T" in bundle["generated_at"]  # ISO-8601 UTC timestamp
        assert isinstance(bundle["runtime"]["uptime_s"], int)

    async def test_platform_fields(self, async_db: AsyncDatabase) -> None:
        plat = (await build_diagnostic_bundle(async_db))["platform"]
        assert plat["system"]  # e.g. "Windows"
        assert plat["python"][0].isdigit()  # "3.12.x"
        assert isinstance(plat["pid"], int)

    async def test_config_shape_is_enums_and_counts(self, async_db: AsyncDatabase) -> None:
        """Config section carries enums/numbers/bools only — by construction
        neither secret values nor absolute paths can appear in it."""
        section = (await build_diagnostic_bundle(async_db))["config"]
        assert section["log_level"] in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        assert isinstance(section["log_file_configured"], bool)
        assert isinstance(section["cors_custom_origin_count"], int)
        # No string value anywhere contains a path separator.
        assert not any(
            isinstance(v, str) and ("\\" in v or "/" in v) for v in section.values()
        )

    async def test_storage_counts_match_seed(self, async_db: AsyncDatabase) -> None:
        await async_db.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at) "
            "VALUES ('s1', 'alpha', '2026-08-01T10:00:00Z', "
            "'2026-08-01T10:00:00Z')"
        )
        storage = (await build_diagnostic_bundle(async_db))["storage"]
        assert storage["db_available"] is True
        assert storage["tables"]["sessions"] == 1
        assert storage["table_count"] == len(storage["tables"])
        assert isinstance(storage["migrations_applied"], int)
        assert storage["migrations_applied"] >= 1

    async def test_storage_excludes_migration_bookkeeping(
        self, async_db: AsyncDatabase
    ) -> None:
        tables = (await build_diagnostic_bundle(async_db))["storage"]["tables"]
        assert "schema_migrations" not in tables

    async def test_storage_degrades_without_db(self) -> None:
        """NO_DB mode: the bundle must still assemble — diagnostics are
        most valuable exactly when storage is broken."""
        bundle = await build_diagnostic_bundle(None)
        assert bundle["format"] == DIAG_FORMAT
        assert bundle["storage"] == {"db_available": False}

    async def test_log_tail_is_string_list(self, async_db: AsyncDatabase) -> None:
        tail = (await build_diagnostic_bundle(async_db))["log_tail"]
        assert isinstance(tail, list)
        assert all(isinstance(line, str) for line in tail)


# ---------------------------------------------------------------------------
# In-memory log tail handler (R45, logging_setup)
# ---------------------------------------------------------------------------


def _make_record(msg: str) -> logging.LogRecord:
    return logging.LogRecord(
        "diag.test", logging.INFO, __file__, 1, msg, None, None
    )


class TestMemoryTailHandler:
    def setup_method(self) -> None:
        _recent_lines.clear()

    def test_records_formatted_lines(self) -> None:
        handler = _MemoryTailHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.emit(_make_record("hello-tail"))
        assert get_recent_log_lines() == ["hello-tail"]

    def test_caps_at_max_and_keeps_newest(self) -> None:
        handler = _MemoryTailHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        for i in range(MEMORY_LOG_TAIL_LINES + 50):
            handler.emit(_make_record(f"line-{i}"))
        tail = get_recent_log_lines()
        assert len(tail) == MEMORY_LOG_TAIL_LINES
        assert tail[0] == "line-50"  # oldest survivor
        assert tail[-1] == f"line-{MEMORY_LOG_TAIL_LINES + 49}"

    def test_limit_zero_returns_empty(self) -> None:
        handler = _MemoryTailHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.emit(_make_record("x"))
        assert get_recent_log_lines(0) == []


# ---------------------------------------------------------------------------
# IPC end-to-end
# ---------------------------------------------------------------------------


class TestDiagExportIPC:
    async def test_rpc_round_trip(self, client: IPCClient) -> None:
        result = await client.request("diag.export", {})
        assert result["format"] == DIAG_FORMAT
        assert result["version"] == __version__
        assert result["storage"]["db_available"] is True


# ---------------------------------------------------------------------------
# Sanitization guarantees (R47) — the bug-report-safety contract
# ---------------------------------------------------------------------------


def _json_literal(s: str) -> str:
    """The exact in-JSON representation of *s* (quotes stripped, escapes
    applied) — a Windows path serializes with doubled backslashes, so a
    raw ``str(path) not in text`` check would silently pass on leaks."""
    return json.dumps(s)[1:-1]


class TestSanitizationGuarantees:
    async def test_bundle_hides_api_key_and_data_dir(
        self, async_db: AsyncDatabase, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The two leaks a bug-report attachment could plausibly carry:
        a configured API key and the absolute user data directory."""
        secret_dir = tmp_path / "DiagSecretDir"
        monkeypatch.setenv("MINIMAX_API_KEY", "sk-diagsecret0123456789abcdef")
        monkeypatch.setenv("MINIMAX_CODE_DATA_DIR", str(secret_dir))
        bundle = await build_diagnostic_bundle(async_db)
        text = json.dumps(bundle)
        assert "sk-diagsecret0123456789abcdef" not in text
        assert _json_literal(str(secret_dir)) not in text
        # The data dir survives only as its basename (the /health precedent).
        assert bundle["config"]["data_dir_name"] == "DiagSecretDir"

    async def test_bundle_hides_cors_origin_values(
        self, async_db: AsyncDatabase, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CORS origins are reported as a count only — the origin values
        themselves (hostnames of the user's trusted frontends) never
        enter the bundle."""
        monkeypatch.setenv(
            "MINIMAX_CODE_CORS_ORIGINS",
            "https://secret-frontend.example.com,https://another.example.org",
        )
        bundle = await build_diagnostic_bundle(async_db)
        text = json.dumps(bundle)
        assert "secret-frontend.example.com" not in text
        assert "another.example.org" not in text
        assert bundle["config"]["cors_custom_origin_count"] == 2

    async def test_bundle_hides_user_home(
        self, async_db: AsyncDatabase
    ) -> None:
        """No field of the bundle may carry the user's home directory —
        the strongest absolute-path guarantee, independent of any env var."""
        bundle = await build_diagnostic_bundle(async_db)
        text = json.dumps(bundle)
        home = str(Path.home())
        if home and home not in (".", "/"):  # degenerate CI containers
            assert _json_literal(home) not in text

    def test_log_tail_is_scrubbed_by_sanitizer_filter(self) -> None:
        """The memory tail handler composes with ``SanitizerFilter`` the
        way ``configure_logging`` mounts it: filter at handler level, so
        the formatted tail never contains a credential, and a message
        that *is* a home-relative path collapses to ``~``.

        Driven through ``handler.handle`` (not ``emit``) — that is the
        real dispatch path where handler filters run, mirroring
        ``logger.info(...) → callHandlers → handle → filter → emit``.

        Scope note: ``redact_paths`` collapses a string whose value is a
        home path (the tool-argument case, e.g. ``{"path": ~/proj}``);
        it is not a substring scrubber for prose. The credential shapes
        (``sk-…``, Bearer, assignments) *are* substring-scrubbed
        wherever they appear."""
        _recent_lines.clear()
        handler = _MemoryTailHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.addFilter(SanitizerFilter())
        home = str(Path.home())
        handler.handle(_make_record("connect failed with sk-tailsecret1234567890"))
        handler.handle(_make_record(f"{home}\\proj\\notes.txt"))
        handler.handle(_make_record(f"{home}/proj/notes.txt"))
        tail = get_recent_log_lines()
        assert len(tail) == 3
        assert "sk-tailsecret1234567890" not in tail[0]
        assert "[REDACTED]" in tail[0]
        if home and home not in (".", "/"):
            assert tail[1].startswith("~"), tail[1]
            assert home not in tail[1]
            assert tail[2].startswith("~"), tail[2]
            assert home not in tail[2]
