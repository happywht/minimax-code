"""Local non-secret search endpoint setting, separate from portable data bundles."""
from __future__ import annotations

import ipaddress
import json
import os
import stat
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

_LOCK = threading.RLock()
_FILE = "research-settings.json"


def validate_endpoint(value: str) -> str:
    # Import lazily to keep storage independent of tool registration.
    from .agent.tools.public_research import _url

    if not isinstance(value, str) or value != value.strip() or len(value) > 2048:
        raise ValueError("Invalid search endpoint")
    url, host, _ = _url(value)
    parsed = urlsplit(value)
    if (parsed.query or parsed.fragment or parsed.path not in {"/search", "/search/"}):
        raise ValueError("Use a credential-free HTTPS SearXNG /search endpoint without query or fragment")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (not ip.is_global or (ip.version == 6 and ip.ipv4_mapped is not None)):
        raise ValueError("Search endpoint must not be a private address")
    return url


def _read_settings(directory: Path) -> dict:
    path = directory / _FILE
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"provider": "searxng", "search_url": None}
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_size > 4096:
        raise ValueError("Search settings file is invalid")
    with path.open("rb") as stream:
        actual = os.fstat(stream.fileno())
        if (not stat.S_ISREG(actual.st_mode) or (info.st_dev, info.st_ino) != (actual.st_dev, actual.st_ino)):
            raise ValueError("Search settings file changed during read")
        data = stream.read(4097)
    if len(data) > 4096:
        raise ValueError("Search settings file is invalid")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Search settings file is invalid") from exc
    if type(value) is not dict or set(value) not in ({"search_url"}, {"search_url", "provider"}):
        raise ValueError("Search settings file is invalid")
    provider, url = value.get("provider", "searxng"), value.get("search_url")
    if provider not in {"searxng", "brave"} or (url is not None and not isinstance(url, str)):
        raise ValueError("Search settings file is invalid")
    return {"provider": provider, "search_url": validate_endpoint(url) if url else None}


def _load(directory: Path) -> str | None:
    return _read_settings(directory)["search_url"]


def resolve_provider(directory: Path) -> str:
    with _LOCK:
        return _read_settings(directory)["provider"]


def _environment_endpoint() -> str | None:
    from .agent.tools.public_research import _url

    value = os.environ.get("MINIMAX_CODE_SEARCH_URL", "")
    if not value:
        return None
    url, _, _ = _url(value)
    if urlsplit(url).query:
        raise ValueError("Search endpoint must not contain query parameters")
    return url


def resolve_endpoint(directory: Path) -> str | None:
    with _LOCK:
        return _load(directory) or _environment_endpoint()


def get_config(directory: Path) -> dict:
    with _LOCK:
        settings = _read_settings(directory)
        saved = settings["search_url"]
        from .secrets import has_provider_key
        try:
            key_configured = has_provider_key("research-brave")
        except Exception:
            key_configured = False
        extra = {"provider": settings["provider"], "brave_key_configured": key_configured}
        if saved:
            return {"saved_url": saved, "effective_url": saved, "source": "settings", **extra}
        try:
            environment = _environment_endpoint()
        except ValueError:
            return {"saved_url": None, "effective_url": None, "source": "environment_invalid", **extra}
        if environment:
            # Do not echo an environment URL which may contain sensitive path material.
            return {"saved_url": None, "effective_url": None, "source": "environment", **extra}
        return {"saved_url": None, "effective_url": None, "source": "unconfigured", **extra}


def set_config(directory: Path, value: str | None, provider: str = "searxng") -> dict:
    if provider not in {"searxng", "brave"}:
        raise ValueError("Unknown research provider")
    normalized = validate_endpoint(value) if value is not None else None
    with _LOCK:
        directory.mkdir(parents=True, exist_ok=True)
        if normalized is None and provider == "searxng":
            # Replacing with a validated absence, rather than following the target.
            (directory / _FILE).unlink(missing_ok=True)
        else:
            fd, temporary = tempfile.mkstemp(prefix=".research-", dir=directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump({"search_url": normalized, "provider": provider}, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, directory / _FILE)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return get_config(directory)
