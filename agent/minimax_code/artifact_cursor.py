"""Bind filtered page cursors to the exact metadata query and service."""
from __future__ import annotations

import base64
import json
import re


def encode_cursor(kind, project, service, run, query, row_id):
    data = json.dumps([kind, project, service, run, query, row_id], ensure_ascii=True,
                      separators=(",", ":")).encode("ascii")
    return "q_" + base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def decode_cursor(cursor, kind, project, service, run, query):
    if cursor is None:
        return None
    if query is None:
        if not re.fullmatch(r"(?:art|att)_[0-9a-f]{32}", cursor):
            raise ValueError("Invalid cursor for this scope")
        return cursor
    try:
        if not cursor.startswith("q_"):
            raise ValueError
        token = cursor[2:]
        data = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        if (not isinstance(data, list) or len(data) != 6
                or data[:5] != [kind, project, service, run, query]
                or not isinstance(data[5], str)
                or not re.fullmatch(rf"{kind}_[0-9a-f]{{32}}", data[5])):
            raise ValueError
        return data[5]
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        raise ValueError("Invalid cursor for this scope") from None
