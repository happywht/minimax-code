"""Bounded, opt-in local OCR inside the attachment parser worker.

No executable, model download, URL, or user-supplied path is accepted. Resource
limits are consumption controls, not a filesystem or network sandbox.
"""
from __future__ import annotations

import io
import os
import warnings

MAX_PIXELS = 12_000_000
MAX_TEXT = 16_000


def recognize_image(data: bytes, kind: int, language: int) -> dict:
    formats = {1: "PNG", 2: "JPEG"}
    languages = {1: "eng", 2: "chi_sim", 3: "eng+chi_sim"}
    if kind not in formats or language not in languages:
        return {"ok": False, "reason": "invalid_image"}
    try:
        from PIL import Image
        import tesserocr
    except ImportError:
        return {"ok": False, "reason": "missing_dependency"}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=[formats[kind]]) as image:
                if (image.format != formats[kind] or image.width < 1 or image.height < 1
                        or image.width * image.height > MAX_PIXELS
                        or getattr(image, "n_frames", 1) != 1):
                    return {"ok": False, "reason": "image_limit"}
                image.load()
                image = image.convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return {"ok": False, "reason": "invalid_image"}
    try:
        # TESSDATA_PREFIX is operator configuration, passed explicitly by the
        # launcher; the user request cannot select a path or executable.
        options = {"lang": languages[language]}
        if os.environ.get("TESSDATA_PREFIX"):
            options["path"] = os.environ["TESSDATA_PREFIX"]
        with tesserocr.PyTessBaseAPI(**options) as api:
            api.SetImage(image)
            text = api.GetUTF8Text() or ""
    except RuntimeError:
        return {"ok": False, "reason": "missing_language"}
    return {"ok": True, "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT}
