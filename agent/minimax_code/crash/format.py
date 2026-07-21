"""JSON crash blob format -- fusion of grok's ``xai-crash-handler`` ``format.rs``
(R226, pure-serialization leaf).

grok's ``format.rs`` defines a custom binary blob format ("GCRX") for the
crash record persisted by the signal handler: a fixed little-endian header
(magic + version + signal + si_code + si_addr + pid + timestamp + n_frames +
null-padded app_version) followed by an array of 8-byte frame pointers. The
binary format exists because a POSIX signal handler is async-signal-unsafe
to allocate -- grok writes it via raw ``libc::write`` into a pre-allocated
static buffer.

Python crash capture is allocation-safe (``faulthandler`` dumps a pre-formatted
traceback from its own dedicated handler, not a bare ``sigaction`` trampoline),
so the persisted crash record is a plain JSON object. This module keeps grok's
``CrashBlob`` value type (the parsed crash payload) and its parse/serialize
round-trip contract, but rebuilds the serialization on JSON:
:meth:`CrashBlob.from_payload` / :meth:`CrashBlob.as_payload` replace grok's
``CrashBlob::parse`` / ``writer`` module.

Migrated (this round)
---------------------

* :data:`MAGIC` -- grok ``MAGIC`` (``b"GCRX"``), as a JSON string tag.
* :data:`VERSION` -- grok ``VERSION`` (1): persisted-record schema version.
* :data:`MAX_FRAMES` -- grok ``MAX_FRAMES`` (64): backtrace frame cap.
* :class:`CrashBlob` -- grok ``CrashBlob``: the parsed crash payload (signal +
  si_code + si_addr + pid + timestamp + frame pointers + app_version), with
  ``from_payload`` / ``as_payload`` JSON round-trip.

YAGNI / dropped (binary-only)
-----------------------------

* ``VERSION_STRING_LEN`` (32), ``HEADER_SIZE``, ``MAX_FILE_SIZE`` -- grok's
  binary layout constants (null-padded field width, fixed header size,
  worst-case file size). JSON is variable-length, so they have no equivalent.
* ``writer::write_header`` / ``writer::write_frame`` -- grok's unsafe
  signal-handler byte writers operate on a pre-allocated static buffer with
  no Python equivalent (``faulthandler`` is the capture mechanism). The
  ``as_payload`` method replaces both.

Purification decisions
----------------------

grok's ``CrashBlob::parse`` validates only structure (length, magic, version,
frame count); the field types are enforced by Rust's ``u8`` / ``i32`` /
``u64`` decoders at parse time. Python has no compile-time types, so
:meth:`CrashBlob.from_payload` adds ``isinstance`` guards via the :func:`_is_int`
helper and returns ``None`` on any type mismatch -- the same refuse-and-return-None
contract grok uses for structural errors. ``bool`` is rejected for integer
fields (``isinstance(True, int)`` is True in Python, but a crash signal / pid
is never a boolean). Frame pointers are validated element-by-element to mirror
grok's per-frame ``u64`` decode.

Product-fusion note
-------------------

This is the persisted-crash half of recovery: the future ``handler`` leaf
(``faulthandler`` + ``excepthook`` installation) will build a ``CrashBlob``
from a live crash and ``as_payload`` it to ``last-crash.json``; the future
``check_previous_crash`` orchestration will ``from_payload`` it back, hand the
frame pointers to ``resolve_frames`` (symbolication), and build a
:class:`~minimax_code.crash.types.CrashReport`. The ``crash.*`` IPC namespace
and frontend recovery prompt consume the resulting ``CrashReport``, not this
raw blob.
"""

from __future__ import annotations

from dataclasses import dataclass

#: grok ``MAGIC`` (``b"GCRX"``) -- persisted as a JSON string tag so a reader
#: can sanity-check that a ``last-crash.json`` was written by this handler.
MAGIC: str = "GCRX"

#: grok ``VERSION`` (1) -- persisted-record schema version. Bumped if the
#: JSON shape changes; :meth:`CrashBlob.from_payload` refuses mismatched
#: versions (mirrors grok's ``data[4] != VERSION`` guard).
VERSION: int = 1

#: grok ``MAX_FRAMES`` (64) -- maximum backtrace frames captured. A payload
#: with more frames is rejected by :meth:`CrashBlob.from_payload` (mirrors
#: grok's ``n_frames > MAX_FRAMES`` guard).
MAX_FRAMES: int = 64


def _is_int(value: object) -> bool:
    """Return True for a real integer (bool excluded).

    ``isinstance(True, int)`` is True in Python; a crash signal / pid / frame
    pointer is never a boolean, so bool is rejected to keep the parsed blob
    honest and to mirror grok's distinct ``u8`` / ``u32`` / ``u64`` decoders.
    """
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True, slots=True)
class CrashBlob:
    """Parsed crash payload persisted across restarts (grok ``CrashBlob``).

    The raw, un-symbolicated crash data written by the handler at crash time
    and read back at next startup. ``frames`` are raw instruction pointers
    (addresses); the future ``resolve_frames`` leaf turns them into
    :class:`~minimax_code.crash.types.ResolvedFrame` entries with symbol /
    source info. Contrast with
    :class:`~minimax_code.crash.types.CrashReport`, which is the
    symbolicated, human-readable surface.
    """

    signal: int
    si_code: int
    si_addr: int
    pid: int
    timestamp: int
    frames: tuple[int, ...]
    app_version: str

    @classmethod
    def from_payload(cls, payload: object) -> CrashBlob | None:
        """Parse a JSON-compatible dict into a ``CrashBlob`` (grok
        ``CrashBlob::parse``).

        Returns ``None`` for any malformed payload: non-dict input, bad magic
        or version tag, missing fields, integer fields that are not real ints
        (bool rejected), ``frames`` that is not a list of ints, or a frame
        count over :data:`MAX_FRAMES`. Mirrors grok's refuse-and-return-None
        contract for structural parse errors.
        """
        if not isinstance(payload, dict):
            return None
        if payload.get("magic") != MAGIC:
            return None
        if payload.get("version") != VERSION:
            return None

        signal = payload.get("signal")
        si_code = payload.get("si_code")
        si_addr = payload.get("si_addr")
        pid = payload.get("pid")
        timestamp = payload.get("timestamp")
        app_version = payload.get("app_version")
        frames_raw = payload.get("frames")

        if not (_is_int(signal) and _is_int(si_code) and _is_int(si_addr)):
            return None
        if not (_is_int(pid) and _is_int(timestamp)):
            return None
        if not isinstance(app_version, str):
            return None
        if not isinstance(frames_raw, list):
            return None
        if len(frames_raw) > MAX_FRAMES:
            return None

        frames: list[int] = []
        for frame in frames_raw:
            if not _is_int(frame):
                return None
            frames.append(frame)

        return cls(
            signal=signal,  # type: ignore[arg-type]
            si_code=si_code,  # type: ignore[arg-type]
            si_addr=si_addr,  # type: ignore[arg-type]
            pid=pid,  # type: ignore[arg-type]
            timestamp=timestamp,  # type: ignore[arg-type]
            frames=tuple(frames),
            app_version=app_version,
        )

    def as_payload(self) -> dict[str, object]:
        """Serialize to a JSON-compatible dict stamped with magic + version
        (grok ``writer`` module).

        The inverse of :meth:`from_payload`:
        ``CrashBlob.from_payload(blob.as_payload()) == blob`` always holds for
        a valid blob. ``frames`` is emitted as a list (JSON has no tuple
        literal). Stamps ``magic`` + ``version`` so a future schema change is
        detectable by the reader.
        """
        return {
            "magic": MAGIC,
            "version": VERSION,
            "signal": self.signal,
            "si_code": self.si_code,
            "si_addr": self.si_addr,
            "pid": self.pid,
            "timestamp": self.timestamp,
            "frames": list(self.frames),
            "app_version": self.app_version,
        }


__all__ = [
    "MAGIC",
    "MAX_FRAMES",
    "VERSION",
    "CrashBlob",
]
