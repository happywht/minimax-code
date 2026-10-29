from minimax_code import live_stream
from minimax_code.live_stream import LiveStream


def test_stream_snapshots_are_segmented_and_monotonic():
    stream = LiveStream()
    assert stream.append("a", "你", False) == 1
    assert stream.append("a", "好", True) == 2
    assert stream.append("b", "next", False) == 1
    assert stream.snapshot() == {"complete": True, "messages": [
        {"message_id": "a", "seq": 2, "text": "你好", "done": True},
        {"message_id": "b", "seq": 1, "text": "next", "done": False},
    ]}


def test_stream_fails_closed_when_memory_limit_is_exceeded(monkeypatch):
    monkeypatch.setattr(live_stream, "MAX_BYTES", 3)
    stream = LiveStream()
    assert stream.append("a", "你", False) == 1
    assert stream.append("a", "好", False) is None
    assert stream.event_seq == 2
    assert stream.append("a", "x", False) is None
    assert stream.event_seq == 3
    assert stream.snapshot() == {"complete": False, "messages": []}
