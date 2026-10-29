"""Local daily budget policy, separate from portable database bundles."""
from __future__ import annotations

import asyncio
import json
import os
import stat
import tempfile
from pathlib import Path

from .budget import daily_budget_limit

# Shared by policy updates and governed reservations within this service process.
policy_lock = asyncio.Lock()
_FILENAME = "daily-budget-settings.json"
_MAX = 2**53 - 1


def get_config(directory: Path, *, fallback: int | None = None) -> dict:
    path = directory / _FILENAME
    try:
        info = path.lstat()
    except FileNotFoundError:
        saved = None
    else:
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_size > 4096:
            raise ValueError("Daily budget settings file is invalid")
        with path.open("rb") as stream:
            actual = os.fstat(stream.fileno())
            if not stat.S_ISREG(actual.st_mode) or (info.st_dev, info.st_ino) != (actual.st_dev, actual.st_ino):
                raise ValueError("Daily budget settings changed during read")
            data = stream.read(4097)
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Daily budget settings file is invalid") from exc
        if len(data) > 4096 or type(value) is not dict or set(value) != {"limit_tokens"}:
            raise ValueError("Daily budget settings file is invalid")
        saved = value["limit_tokens"]
        if saved is not None and (type(saved) is not int or not 0 < saved <= _MAX):
            raise ValueError("Daily budget settings file is invalid")
    env = fallback if fallback is not None else daily_budget_limit()
    return {"saved_limit": saved, "effective_limit": saved if saved is not None else env,
            "source": "settings" if saved is not None else "environment" if env is not None else "unconfigured"}


def set_config(directory: Path, limit: int | None) -> dict:
    if limit is not None and (type(limit) is not int or not 0 < limit <= _MAX):
        raise ValueError("Daily limit must be a positive safe integer or null")
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".daily-budget-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"limit_tokens": limit}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / _FILENAME)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return get_config(directory)
