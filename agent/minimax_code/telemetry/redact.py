"""Payload redaction (R11).

Port of grok-build's ``redact_common`` philosophy (secret-shape scrub +
user-path scrub + URL-origin reduction) into MiniMax. This is the single
privacy chokepoint for the in-memory telemetry pipeline: every byte that
enters the ring buffer or metrics passes through :func:`redact_value`
first.

Three scrubbers, ordered cheapest-first:

1. :func:`redact_secrets` — regex shapes for common credential formats
   (``sk-…`` keys, ``Bearer …`` tokens, ``api_key=…`` / ``password=…``
   assignments). Conservative: only matches shapes that are almost
   certainly secrets, never bare words.
2. :func:`redact_paths` — collapses the user home directory to ``~`` so
   a payload like ``{"path": "/Users/alice/proj"}`` becomes
   ``{"path": "~/proj"}``.
3. :func:`url_origin` — reduces a URL to ``scheme://host[:port]`` so the
   path/query (which may carry user content or tokens) never leaks.

:func:`redact_value` walks dicts / lists / strings recursively so nested
tool arguments are scrubbed uniformly.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_REDACTED = "[REDACTED]"

# Secret shapes — kept conservative to avoid clobbering ordinary text.
# Each pattern matches a credential *shape*, not a bare keyword.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # OpenAI / MiniMax / Anthropic style key prefixes: sk-..., sk-ant-...
    re.compile(r"sk-(?:[A-Za-z0-9_\-]{8,})"),
    # Bearer tokens in Authorization headers / inline.
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.=]{8,}"),
    # Explicit secret assignments: api_key=..., password: ..., "token": "..."
    # The ["']* between the keyword and the separator lets us catch JSON
    # shapes like `"password": "supersecret"` where the key is quoted.
    re.compile(
        r"(?i)(api[_-]?key|secret|password|passwd|token|authorization)"
        r"[\"']*\s*[:=]\s*['\"]?[A-Za-z0-9_\-\.=]{8,}"
    ),
)

_HOME_CACHE: str | None = None


def _home() -> str:
    """Return the current user's home path (cached), or '' if unset."""
    global _HOME_CACHE
    if _HOME_CACHE is None:
        try:
            _HOME_CACHE = str(Path.home())
        except Exception:
            _HOME_CACHE = ""
    return _HOME_CACHE


def redact_secrets(text: str) -> str:
    """Replace credential-shaped substrings with ``[REDACTED]``."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    return text


def redact_paths(text: str, home: str | None = None) -> str:
    """Collapse the user home directory prefix to ``~``.

    No-op when the home directory is empty or the text does not start
    with it — call sites rely on a cheap, never-mangling fallback.
    Handles *both* ``/`` and ``\\`` separators so a Unix-style path
    (``/home/alice/...``) is scrubbed even when the process runs on
    Windows, and vice-versa; payloads carry paths from many sources.
    """
    home_path = home if home is not None else _home()
    if not home_path:
        return text
    home_norm = home_path.rstrip("/\\")
    if not home_norm or text == home_path or text == home_norm:
        return "~"
    # Match the home prefix followed by either separator, case-insensitively
    # (Windows drive-letter casing varies between C:\ and c:\).
    for sep in ("/", "\\"):
        prefix = home_norm + sep
        if text.startswith(prefix) or text.lower().startswith(prefix.lower()):
            return "~" + sep + text[len(prefix):]
    return text


def url_origin(url: str) -> str:
    """Reduce a URL to ``scheme://host[:port]``; passthrough if unparseable."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.scheme or not parts.netloc:
        return url
    return f"{parts.scheme}://{parts.netloc}"


def redact_value(value: Any) -> Any:
    """Recursively scrub secrets / paths / URL origins from a value.

    Returns a *new* structure; the input is never mutated. Dicts and
    lists are walked depth-first; strings get all three scrubbers in
    sequence; everything else passes through untouched.
    """
    if isinstance(value, str):
        scrubbed = redact_secrets(value)
        scrubbed = redact_paths(scrubbed)
        # Only reduce URL-shaped strings (must look like scheme://host).
        if "://" in scrubbed:
            scrubbed = url_origin(scrubbed)
        return scrubbed
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_value(v) for v in value)
    return value


__all__ = ["redact_secrets", "redact_paths", "url_origin", "redact_value"]
