"""Bounded in-process stream snapshots; not a restartable journal."""
from __future__ import annotations

from dataclasses import dataclass, field

MAX_BYTES = 2 * 1024 * 1024
MAX_CHUNKS = 20000
MAX_SEGMENTS = 256


@dataclass
class _Segment:
    message_id: str
    seq: int = 0
    chunks: list[str] = field(default_factory=list)
    done: bool = False


class LiveStream:
    def __init__(self) -> None:
        self._segments: dict[str, _Segment] = {}
        self._bytes = 0
        self._chunks = 0
        self.event_seq = 0
        self.complete = True

    def append(self, message_id: str, delta: str, done: bool) -> int | None:
        self.event_seq += 1
        if not self.complete:
            return None
        item = self._segments.get(message_id)
        size = len(delta.encode("utf-8"))
        if ((item is None and len(self._segments) >= MAX_SEGMENTS)
                or self._bytes + size > MAX_BYTES or self._chunks >= MAX_CHUNKS):
            self.complete = False
            self._segments.clear()
            return None
        if item is None:
            item = self._segments[message_id] = _Segment(message_id)
        item.seq += 1
        item.chunks.append(delta)
        item.done = done
        self._bytes += size
        self._chunks += 1
        return item.seq

    def snapshot(self) -> dict:
        return {"complete": self.complete, "messages": [
            {"message_id": item.message_id, "seq": item.seq,
             "text": "".join(item.chunks), "done": item.done}
            for item in self._segments.values()
        ]}
