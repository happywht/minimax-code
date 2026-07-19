"""Workspace error type + IoKind mirror (R67).

Fusion of grok-build's ``xai-grok-workspace-types::error`` — the
adjacent-tagged :class:`WorkspaceError` enum returned by every workspace
RPC, plus :class:`IoKind` (a serialisable mirror of ``std::io::ErrorKind``)
and the transitive :class:`~minimax_code.workspace_types.chunk_kind.ChunkKind`
dependency.

``WorkspaceError`` is adjacent-tagged (``{type, data}``) because its 13
variants mix struct / newtype / unit shapes — internal tagging would
reject the newtype (``Vcs(String)``) and unit (``Cancelled``) variants.
Each variant's ``data`` payload matches its Rust shape:

* **struct variant**  → ``data`` is a dict (e.g. ``{"message": ..., "kind": ...}``)
* **newtype variant** → ``data`` is the bare inner value (a string)
* **unit variant**    → ``data`` is ``None``

The Display templates (``__str__``) reproduce thiserror's
``#[error("...")]`` attributes exactly, and :meth:`WorkspaceError.is_retryable`
/ :meth:`WorkspaceError.is_cancelled` reproduce the Rust predicates.
:meth:`WorkspaceError.from_io` mirrors ``From<io::Error>`` — but takes a
``(message, kind)`` pair since Python has no ``std::io::Error``; the
``IoKind`` enum is the wire-stable replacement.
"""

from __future__ import annotations

from enum import StrEnum

from minimax_code.workspace_types._tagged import AdjacentTagged
from minimax_code.workspace_types.chunk_kind import ChunkKind

__all__ = ["IoKind", "WorkspaceError"]

#: The 12 ``IoKind`` values the workspace treats as transient / retryable
#: (mirrors ``IoKind::is_transient``). Stored as wire strings so the
#: predicate works against both ``IoKind`` members and raw ``data["kind"]``
#: payloads read off the wire.
_TRANSIENT_IO_KINDS = frozenset(
    {
        "broken_pipe",
        "connection_reset",
        "connection_aborted",
        "connection_refused",
        "timed_out",
        "interrupted",
        "would_block",
        "host_unreachable",
        "network_unreachable",
        "network_down",
        "resource_busy",
        "deadlock",
    }
)


class IoKind(StrEnum):
    """Serialisable mirror of ``std::io::ErrorKind`` (39 variants).

    Tracks every currently-stable variant of ``std::io::ErrorKind`` as of
    Rust 1.83+. Member *name* is PascalCase; *value* is the snake_case
    wire string emitted by ``#[serde(rename_all = "snake_case")]``.
    ``std::io::ErrorKind`` itself is not serialisable, so the workspace
    wire replaces it with this enum; the ``From<std::io::ErrorKind>`` impl
    in Rust folds future-stable variants into ``Other`` (Python callers
    pick the matching member directly).
    """

    # Declaration order mirrors ``ALL_IO_KINDS`` in the source tests.
    ConnectionRefused = "connection_refused"
    ConnectionReset = "connection_reset"
    HostUnreachable = "host_unreachable"
    NetworkUnreachable = "network_unreachable"
    ConnectionAborted = "connection_aborted"
    NotConnected = "not_connected"
    AddrInUse = "addr_in_use"
    AddrNotAvailable = "addr_not_available"
    NetworkDown = "network_down"
    BrokenPipe = "broken_pipe"
    AlreadyExists = "already_exists"
    WouldBlock = "would_block"
    NotADirectory = "not_a_directory"
    IsADirectory = "is_a_directory"
    DirectoryNotEmpty = "directory_not_empty"
    ReadOnlyFilesystem = "readonly_filesystem"
    StaleNetworkFileHandle = "stale_network_file_handle"
    InvalidInput = "invalid_input"
    InvalidData = "invalid_data"
    TimedOut = "timed_out"
    WriteZero = "write_zero"
    StorageFull = "storage_full"
    NotSeekable = "not_seekable"
    QuotaExceeded = "quota_exceeded"
    FileTooLarge = "file_too_large"
    ResourceBusy = "resource_busy"
    ExecutableFileBusy = "executable_file_busy"
    Deadlock = "deadlock"
    CrossesDevices = "crosses_devices"
    TooManyLinks = "too_many_links"
    InvalidFilename = "invalid_filename"
    ArgumentListTooLong = "argument_list_too_long"
    Interrupted = "interrupted"
    UnexpectedEof = "unexpected_eof"
    Unsupported = "unsupported"
    OutOfMemory = "out_of_memory"
    NotFound = "not_found"
    PermissionDenied = "permission_denied"
    Other = "other"

    def is_transient(self) -> bool:
        """True for the 12 kinds the workspace treats as retryable.

        Mirrors ``IoKind::is_transient``: ``BrokenPipe``,
        ``ConnectionReset``, ``ConnectionAborted``, ``ConnectionRefused``,
        ``TimedOut``, ``Interrupted``, ``WouldBlock``,
        ``HostUnreachable``, ``NetworkUnreachable``, ``NetworkDown``,
        ``ResourceBusy``, ``Deadlock``.
        """
        return self.value in _TRANSIENT_IO_KINDS


class WorkspaceError(AdjacentTagged):
    """All errors surfaced by a workspace transport (adjacent-tagged).

    Wire shape: ``{"type": "<variant>", "data": <payload>}``. Mirrors the
    Rust ``WorkspaceError`` enum exactly. Display templates (``__str__``)
    reproduce the thiserror ``#[error("...")]`` attributes, and
    :meth:`is_retryable` / :meth:`is_cancelled` reproduce the predicates.
    """

    _VARIANTS = (
        "io",
        "vcs",
        "permission",
        "not_found",
        "cancelled",
        "timeout",
        "session_not_found",
        "tool",
        "remote",
        "protocol_mismatch",
        "protocol_violation",
        "empty_stream",
        "internal",
    )

    # -- factory methods (one per variant) ---------------------------------

    @classmethod
    def io(cls, message: str, kind: IoKind) -> WorkspaceError:
        """Filesystem I/O failure (``Io { message, kind }``)."""
        kind_val = kind.value if isinstance(kind, IoKind) else str(kind)
        return cls("io", {"message": message, "kind": kind_val})

    @classmethod
    def vcs(cls, message: str) -> WorkspaceError:
        """Version-control (git/jj) failure (``Vcs(String)``)."""
        return cls("vcs", message)

    @classmethod
    def permission(cls, reason: str) -> WorkspaceError:
        """Permission denied at the workspace policy layer."""
        return cls("permission", {"reason": reason})

    @classmethod
    def not_found(cls, what: str) -> WorkspaceError:
        """Resource not found (``NotFound(String)``)."""
        return cls("not_found", what)

    @classmethod
    def cancelled(cls) -> WorkspaceError:
        """Operation cancelled (unit variant)."""
        return cls("cancelled", None)

    @classmethod
    def timeout(cls, elapsed_ms: int) -> WorkspaceError:
        """Operation exceeded its deadline (``Timeout { elapsed_ms }``)."""
        return cls("timeout", {"elapsed_ms": int(elapsed_ms)})

    @classmethod
    def session_not_found(cls, session_id: str) -> WorkspaceError:
        """Session id was not registered (``SessionNotFound(SessionId)``)."""
        return cls("session_not_found", str(session_id))

    @classmethod
    def tool(cls, code: str, message: str) -> WorkspaceError:
        """A tool returned an error (``Tool { code, message }``)."""
        return cls("tool", {"code": code, "message": message})

    @classmethod
    def remote(cls, message: str) -> WorkspaceError:
        """Generic transport-layer failure (``Remote(String)``)."""
        return cls("remote", message)

    @classmethod
    def protocol_mismatch(cls, expected: str, got: ChunkKind) -> WorkspaceError:
        """Wrong chunk kind arrived on the stream.

        ``got`` is stored as its snake_case wire value (serialisation
        fidelity); :meth:`__str__` re-expands it to the PascalCase
        ``Display`` form via :meth:`ChunkKind.as_str`.
        """
        got_val = got.value if isinstance(got, ChunkKind) else str(got)
        return cls("protocol_mismatch", {"expected": expected, "got": got_val})

    @classmethod
    def protocol_violation(cls, message: str) -> WorkspaceError:
        """Stream produced something inconsistent with the contract."""
        return cls("protocol_violation", message)

    @classmethod
    def empty_stream(cls) -> WorkspaceError:
        """Stream closed before yielding any chunk (unit variant)."""
        return cls("empty_stream", None)

    @classmethod
    def internal(cls, message: str) -> WorkspaceError:
        """Catch-all for unexpected internal failures (``Internal(String)``)."""
        return cls("internal", message)

    # -- From<io::Error> ---------------------------------------------------

    @classmethod
    def from_io(cls, message: str, kind: IoKind) -> WorkspaceError:
        """Mirror ``WorkspaceError::from_io(io::Error)``.

        Python has no ``std::io::Error``, so callers pass the
        ``(message, kind)`` pair directly — the runtime boundary extracts
        these from the native I/O exception. Identical to :meth:`io`.
        """
        return cls.io(message, kind)

    # -- predicates --------------------------------------------------------

    def is_retryable(self) -> bool:
        """Whether the operation is safe to retry.

        Mirrors ``WorkspaceError::is_retryable``: ``Timeout`` and
        ``Remote`` are always retryable; ``Io`` is retryable iff its
        ``kind`` is transient; everything else is not.
        """
        if self.kind in ("timeout", "remote"):
            return True
        if self.kind == "io" and isinstance(self.payload, dict):
            return self.payload.get("kind") in _TRANSIENT_IO_KINDS
        return False

    def is_cancelled(self) -> bool:
        """Whether this is a cancellation (mirrors ``is_cancelled``)."""
        return self.kind == "cancelled"

    # -- Display (thiserror #[error(...)]) ---------------------------------

    def __str__(self) -> str:
        p = self.payload
        if self.kind == "io":
            return f"io: {p['message']}"
        if self.kind == "vcs":
            return f"vcs: {p}"
        if self.kind == "permission":
            return f"permission denied: {p['reason']}"
        if self.kind == "not_found":
            return f"not found: {p}"
        if self.kind == "cancelled":
            return "cancelled"
        if self.kind == "timeout":
            return f"deadline exceeded after {p['elapsed_ms']}ms"
        if self.kind == "session_not_found":
            return f"session not found: {p}"
        if self.kind == "tool":
            return f"tool error [{p['code']}]: {p['message']}"
        if self.kind == "remote":
            return f"transport: {p}"
        if self.kind == "protocol_mismatch":
            # {got} uses ChunkKind's Display (PascalCase), not the wire
            # snake_case value — matches the Rust test expectation
            # "expected GitStatus, got Ack".
            got = ChunkKind(p["got"]).as_str()
            return f"protocol mismatch: expected {p['expected']}, got {got}"
        if self.kind == "protocol_violation":
            return f"protocol violation: {p}"
        if self.kind == "empty_stream":
            return "empty stream (expected at least one chunk)"
        if self.kind == "internal":
            return f"internal: {p}"
        return f"{self.kind}: {p}"

    def __repr__(self) -> str:
        return f"WorkspaceError.{self.kind}({self.payload!r})"
