"""Security-regression suite hub (roadmap R19, v0.14.0).

This module is the single entry point for the security regression suite.
The security tests themselves stay in their domain files (next to the
fixtures they need); each is marked with ``@pytest.mark.security`` so the
whole surface runs as one command:

    uv run pytest -m security

Security test map (marker coverage):

==========================  =============================================  ====
Surface                     File                                           #tests (floor)
==========================  =============================================  ====
Permission rules + R18      ``tests/test_permissions.py``                  33
factory defaults
Log / RPC secret redaction  ``tests/test_secret_audit.py``                 6
(R17)
Secrets keyring storage     ``tests/test_secrets.py``                     16
Secrets RPC handlers        ``tests/test_handlers_secrets.py``            15
Terminal process hardening  ``tests/test_terminal_hardening.py``          16
(v0.5.0)
Memory prompt injection     ``tests/test_memory_injection.py``             5
Audit log trail             ``tests/test_audit.py``                       19
RPC rejection + CORS        12 functions inside ``tests/test_http_server.py``
(R15/R16)
==========================  =============================================  ====

This file adds three guard rails on top of that map:

1. **Suite floor** — ``pytest -m security`` must collect at least
   ``SUITE_FLOOR`` tests. If a security file is deleted, emptied, or its
   marker is removed, the floor breaks instead of the coverage silently
   evaporating.
2. **Per-file floors** — every domain file above must keep at least its
   baseline test count (AST count, no import needed).
3. **Cross-cut smoke** — two end-to-end invariants that span files:
   the R18 factory default must actually gate ``exec_*``, and the redactor
   must scrub every credential shape it claims to handle.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

from minimax_code.permissions import PermissionStore
from minimax_code.storage.dao.permissions import PermissionRuleDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path
from minimax_code.telemetry.redact import redact_value

pytestmark = pytest.mark.security

TESTS_DIR = Path(__file__).resolve().parent

# (file, minimum number of top-level-or-class test functions)
DOMAIN_FILE_FLOORS: tuple[tuple[str, int], ...] = (
    ("test_permissions.py", 33),
    ("test_secret_audit.py", 6),
    ("test_secrets.py", 16),
    ("test_handlers_secrets.py", 15),
    ("test_terminal_hardening.py", 16),
    ("test_memory_injection.py", 5),
    ("test_audit.py", 19),
)

# Functions inside the mixed http-server test file that carry the marker.
HTTP_SERVER_SECURITY_TESTS: tuple[str, ...] = (
    "test_rpc_missing_method_returns_invalid_request",
    "test_rpc_oversized_body_never_reaches_dispatch",
    "test_rpc_body_at_exact_limit_is_processed",
    "test_rpc_get_method_is_rejected",
    "test_ws_malformed_inbound_frames_are_dropped",
    "test_cors_allows_vite_origin",
    "test_cors_allows_127_origin",
    "test_cors_allows_explicit_additional_origin",
    "test_cors_rejects_unlisted_origin",
    "test_cors_env_append_never_replaces_defaults",
    "test_cors_env_ignores_invalid_entries",
    "test_same_origin_rpc_does_not_depend_on_cors_allow_list",
)

# Baseline collection was 146 when the suite was introduced; the floor
# leaves headroom for growth but catches any large silent loss.
SUITE_FLOOR = 130


def _count_test_functions(path: Path) -> int:
    """Count ``test_*`` functions (top level or in classes) via AST."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            if node.name.startswith("test_"):
                count += 1
    return count


class TestSuiteFloors:
    def test_domain_files_exist_and_meet_baseline(self) -> None:
        for name, floor in DOMAIN_FILE_FLOORS:
            path = TESTS_DIR / name
            assert path.exists(), f"security domain file vanished: {name}"
            actual = _count_test_functions(path)
            assert actual >= floor, (
                f"{name} dropped to {actual} tests (floor {floor}) — "
                "security coverage was silently lost; update the floor in "
                "test_security.py only when the loss is intentional"
            )

    def test_http_server_security_functions_are_marked(self) -> None:
        path = TESTS_DIR / "test_http_server.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        marked: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                for dec in node.decorator_list:
                    if (
                        isinstance(dec, ast.Attribute)
                        and dec.attr == "security"
                    ):
                        marked.add(node.name)
        for name in HTTP_SERVER_SECURITY_TESTS:
            assert name in marked, (
                f"{name} lost its @pytest.mark.security decorator — it no "
                "longer runs as part of `pytest -m security`"
            )

    def test_marker_collects_at_least_floor(self) -> None:
        """`pytest -m security` must select >= SUITE_FLOOR tests.

        Runs a real --collect-only in a subprocess (agent dir as cwd) and
        parses the ``<selected>/<total> tests collected`` summary line.
        """
        agent_dir = TESTS_DIR.parent
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-m",
                "security",
                "--collect-only",
                "-q",
            ],
            cwd=agent_dir,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert proc.returncode in (0, 5), (
            f"collection crashed (rc={proc.returncode}): {proc.stderr[-2000:]}"
        )
        m = re.search(r"(\d+)/\d+ tests collected", proc.stdout)
        if m is None:
            # full-selection shape: "N tests collected"
            m2 = re.search(r"(\d+) tests collected", proc.stdout)
            assert m2 is not None, f"unparseable summary: {proc.stdout[-500:]}"
            selected = int(m2.group(1))
        else:
            selected = int(m.group(1))
        assert selected >= SUITE_FLOOR, (
            f"security suite collected only {selected} tests "
            f"(floor {SUITE_FLOOR})"
        )


class TestCrossCutSmoke:
    """Invariants that span more than one security file."""

    async def test_factory_default_gates_exec_and_deny_overrides(self, tmp_path: Path) -> None:
        """R18 chain: fresh DB → exec tools gated behind ask (not denied);
        an explicit user deny wins; deleting it falls back to the default.
        """
        db = AsyncDatabase(make_temp_database_path(tmp_path))
        await db.connect()
        try:
            await db.migrate()
            store = PermissionStore(PermissionRuleDAO(db))
            await store.warm()

            # Factory default: ask gates but does not block outright.
            rule = store.lookup("exec_python")
            assert rule is not None and rule["action"] == "ask"
            assert store.is_allowed("exec_python") is True
            assert store.is_denied("exec_python") is False

            # A user deny shadows the factory default.
            await store.upsert(tool_pattern="exec_*", action="deny")
            assert store.is_denied("exec_python") is True

            # Deleting the user rule restores the factory default.
            await store.delete("exec_*")
            rule = store.lookup("exec_python")
            assert rule is not None and rule["action"] == "ask"
        finally:
            await db.close()

    def test_redact_value_scrubs_every_credential_shape(self) -> None:
        """R15/R17 chain: every secret shape the redactor advertises is
        actually scrubbed, including when nested inside structures that
        RPC error payloads and log args produce (dicts / lists / tuples).

        Note: URL userinfo is only scrubbed on *URL-shaped* strings
        (``scheme://netloc/...``) — a URL embedded in free text is left
        alone by design (conservative, avoids clobbering prose). The
        URL case below is therefore asserted on the standalone form.
        """
        secrets = [
            "sk-abcdefghijklmnop1234",                     # API key prefix
            "Bearer abcdefghijklmnop",                     # auth header
            "api_key=abcdefghijklmnop",                   # assignment
            '"password": "supersecret123"',                # JSON shape
            "AKIAABCDEFGHIJKLMNOP",                       # AWS access key
            "ghp_" + "a" * 36,                             # GitHub classic
            "github_pat_" + "b" * 40,                      # GitHub fine-grained
            "xoxb-" + "c" * 12,                            # Slack bot token
            "AIza" + "d" * 35,                             # Google API key
            "eyJabcdefghij.eyJklmnopqrst.eyJuvwxyz12",    # JWT
        ]
        for secret in secrets:
            scrubbed = redact_value(f"prefix {secret} suffix")
            assert secret not in scrubbed, f"leaked credential shape: {secret}"

        # URL-shaped strings are reduced to scheme://host[:port] — the
        # userinfo *and* the path are dropped (url_origin's documented
        # contract).
        scrubbed_url = redact_value("https://alice:s3cr3t@collector/v1")
        assert "alice:s3cr3t@" not in scrubbed_url
        assert scrubbed_url == "https://collector"

        # Nested structures are walked, and the input is never mutated.
        payload = {
            "params": ["call with sk-abcdefghijklmnop1234"],
            ("nested",): ("token=zzzzzzzzzzzzzzzz",),
        }
        out = redact_value(payload)
        flat = str(out)
        assert "sk-abcdefghijklmnop1234" not in flat
        assert "zzzzzzzzzzzzzzzz" not in flat
        assert "sk-abcdefghijklmnop1234" in str(payload)  # input untouched
