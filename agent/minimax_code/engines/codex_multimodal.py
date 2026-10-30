"""Bound and translate user-selected images for the pinned Codex protocol."""
from __future__ import annotations

import base64
import binascii
from typing import Any

MAX_IMAGE_BYTES = 4 * 1024 * 1024


def codex_input(content: Any) -> tuple[list[dict[str, Any]], str]:
    """Return App Server input and a text-only history label; reject other input types."""
    if isinstance(content, str):
        if not content.strip():
            raise ValueError("Codex input must not be empty")
        return [{"type": "text", "text": content}], content
    if not isinstance(content, list) or not 1 <= len(content) <= 6:
        raise ValueError("Codex accepts text and up to five images")
    wire: list[dict[str, Any]] = []
    texts: list[str] = []
    image_count = total = 0
    for part in content:
        if not isinstance(part, dict):
            raise ValueError("Invalid Codex input part")
        if part.get("type") == "text" and set(part) == {"type", "text"} and isinstance(part["text"], str):
            texts.append(part["text"])
            wire.append({"type": "text", "text": part["text"]})
            continue
        if part.get("type") != "image" or set(part) != {"type", "media_type", "data"}:
            raise ValueError("Codex accepts only text and inline images")
        media, data = part["media_type"], part["data"]
        if not isinstance(media, str) or media not in {"image/png", "image/jpeg", "image/gif", "image/webp"} or not isinstance(data, str):
            raise ValueError("Codex images must be PNG, JPEG, GIF or WebP")
        if len(data) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
            raise ValueError("Codex images exceed the 4 MiB total limit")
        try:
            decoded = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Invalid image base64") from exc
        total += len(decoded)
        image_count += 1
        if total > MAX_IMAGE_BYTES or image_count > 5 or not decoded:
            raise ValueError("Codex accepts up to five images totalling 4 MiB")
        valid = (media == "image/png" and decoded.startswith(b"\x89PNG\r\n\x1a\n")
                 or media == "image/jpeg" and decoded.startswith(b"\xff\xd8\xff")
                 or media == "image/gif" and decoded.startswith((b"GIF87a", b"GIF89a"))
                 or media == "image/webp" and decoded[:4] == b"RIFF" and decoded[8:12] == b"WEBP")
        if not valid:
            raise ValueError("Image data does not match its media type")
        wire.append({"type": "image", "url": f"data:{media};base64,{data}"})
    text = "\n".join(texts)
    if not text.strip() and not image_count:
        raise ValueError("Codex input must not be empty")
    label = text + (f" [{image_count} image(s)]" if image_count else "")
    return wire, label.strip()
