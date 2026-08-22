"""R42 — docs/ipc-contract.md ↔ handler registry reconciliation.

Keeps the IPC contract document from drifting away from the code by
asserting, bidirectionally, that:

1. every method registered via ``register_app_handlers`` appears in the
   doc (a new handler without a doc line fails CI), and
2. every namespace-scoped dotted token the doc mentions resolves to
   either a registered method, a known push event, or an explicitly
   whitelisted internal symbol (a removed/renamed handler whose doc
   mention lingers on fails CI), and
3. the event allow-list used here stays in lockstep with the frontend
   ``StreamEvent`` enum in ``web/src/types/ipc.ts``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from minimax_code.app import register_app_handlers
from minimax_code.config import Config
from minimax_code.ipc.server import IPCServer

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_PATH = REPO_ROOT / "docs" / "ipc-contract.md"
FRONTEND_TYPES = REPO_ROOT / "web" / "src" / "types" / "ipc.ts"

# Server-push event names (WebSocket frames). The frontend ``StreamEvent``
# enum is the authoritative consumer-side list; the two protocol-level
# handshake/keepalive frames (``agent.ready``, ``agent.ping``) precede the
# typed stream and are asserted separately below.
KNOWN_EVENTS = {
    "agent.message_chunk",
    "agent.status",
    "agent.tool_call",
    "agent.tool_result",
    "agent.ask_user",
    "permission.request",
    "permission.resolved",
    "task.progress",
    "agent.subagent_progress",
    "notification.new",
    "notification.read",
    "agent.team_progress",
    "run.created",
    "run.step.started",
    "run.step.completed",
    "run.completed",
}
PROTOCOL_FRAMES = {"agent.ready", "agent.ping"}

# Doc mentions that are deliberately *not* IPC methods: ``crash.install``
# is the internal boot-time recovery hook described as context in the
# ``crash.*`` section (the read half is what's exposed over RPC).
DOC_NON_METHODS = {"crash.install"}


def _registered_methods() -> set[str]:
    server = IPCServer(config=Config.from_env(), stdin=None, stdout=None)
    register_app_handlers(server)
    return set(server._handlers)


def _doc_tokens(namespaces: set[str]) -> set[str]:
    """Namespace-scoped dotted tokens as they appear in the doc.

    Multi-segment tokens are captured whole (``run.step.started`` must not
    be truncated to ``run.step``), and file-name false positives such as
    ``app.py`` / ``package.json`` are dropped by suffix.
    """
    doc = DOC_PATH.read_text(encoding="utf-8")
    alt = "|".join(sorted(namespaces))
    # Segments may be camelCase (session.batchArchive), so [A-Za-z_0-9].
    pat = re.compile(rf"\b({alt})(?:\.[A-Za-z_0-9]+)+\b")
    junk_suffixes = (".py", ".json", ".mjs", ".yaml", ".yml", ".md", ".ts", ".tsx")
    return {
        token
        for token in (m.group(0) for m in pat.finditer(doc))
        if not token.endswith(junk_suffixes)
    }


def test_doc_exists() -> None:
    assert DOC_PATH.is_file(), "docs/ipc-contract.md must exist"


def test_every_registered_method_is_documented() -> None:
    registered = _registered_methods()
    tokens = _doc_tokens({m.split(".")[0] for m in registered})
    # Dot-less built-ins live in the §5 sections, not the ns-scoped table.
    builtins = {"ping", "status", "shutdown"}
    missing = sorted(registered - tokens - builtins)
    assert not missing, (
        "IPC methods registered in code but absent from docs/ipc-contract.md "
        f"(add them to the Appendix A method table): {missing}"
    )


def test_documented_tokens_resolve() -> None:
    registered = _registered_methods()
    tokens = _doc_tokens({m.split(".")[0] for m in registered})
    stale = sorted(
        tokens - registered - KNOWN_EVENTS - PROTOCOL_FRAMES - DOC_NON_METHODS
    )
    assert not stale, (
        "docs/ipc-contract.md mentions namespace-scoped tokens that are "
        f"neither registered methods nor known events: {stale}"
    )


def test_known_events_match_frontend_stream_event_enum() -> None:
    """The event allow-list here must equal web/src/types/ipc.ts."""
    src = FRONTEND_TYPES.read_text(encoding="utf-8")
    start = src.index("export const StreamEvent = {")
    end = src.index("} as const;", start)
    enum_values = set(re.findall(r':\s*"([a-z_.]+)"', src[start:end]))
    assert enum_values, "failed to parse StreamEvent enum from ipc.ts"
    assert KNOWN_EVENTS == enum_values, (
        f"frontend StreamEvent drifted from the agent-side allow-list: "
        f"agent-only={sorted(KNOWN_EVENTS - enum_values)} "
        f"frontend-only={sorted(enum_values - KNOWN_EVENTS)}"
    )


@pytest.mark.parametrize("frame", sorted(PROTOCOL_FRAMES))
def test_protocol_frames_are_documented(frame: str) -> None:
    """Handshake/keepalive frames must keep their doc sections."""
    assert frame in DOC_PATH.read_text(encoding="utf-8")
