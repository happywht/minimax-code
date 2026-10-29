"""Bounded, opt-in local OCR inside the attachment parser worker.

No executable, model download, URL, or user-supplied path is accepted. Resource
limits are consumption controls, not a filesystem or network sandbox.
"""
from __future__ import annotations

import io
import math
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
    return _recognize_loaded(image, languages[language], tesserocr)


def _recognize_loaded(image, language, tesserocr):
    try:
        # TESSDATA_PREFIX is operator configuration, passed explicitly by the
        # launcher; the user request cannot select a path or executable.
        options = {"lang": language}
        if os.environ.get("TESSDATA_PREFIX"):
            options["path"] = os.environ["TESSDATA_PREFIX"]
        with tesserocr.PyTessBaseAPI(**options) as api:
            api.SetImage(image)
            text = api.GetUTF8Text() or ""
    except RuntimeError:
        return {"ok": False, "reason": "missing_language"}
    return {"ok": True, "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT}


def recognize_pdf_page(data: bytes, page_number: int, language: int) -> dict:
    """Render only the requested physical page, under the worker resource gate."""
    languages = {1: "eng", 2: "chi_sim", 3: "eng+chi_sim"}
    if language not in languages or not 1 <= page_number <= 200 or not data.startswith(b"%PDF-"):
        return {"ok": False, "reason": "invalid_pdf"}
    try:
        import pymupdf
        from PIL import Image
        import tesserocr
    except ImportError:
        return {"ok": False, "reason": "missing_dependency"}
    try:
        with pymupdf.open(stream=data, filetype="pdf") as document:
            if document.needs_pass or document.page_count < 1 or document.page_count > 200:
                return {"ok": False, "reason": "pdf_limit"}
            if page_number > document.page_count:
                return {"ok": False, "reason": "page_out_of_range"}
            page = document.load_page(page_number - 1)
            rect = page.rect
            width, height = rect.width, rect.height
            if (not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0
                    or width > 14400 or height > 14400):
                return {"ok": False, "reason": "pdf_limit"}
            # 108 DPI; ceil plus one accounts for renderer bounding-box rounding.
            pixel_width, pixel_height = math.ceil(width * 1.5) + 1, math.ceil(height * 1.5) + 1
            if pixel_width * pixel_height > MAX_PIXELS:
                return {"ok": False, "reason": "pdf_limit"}
            pix = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), colorspace=pymupdf.csRGB, alpha=False, annots=False)
            if pix.width < 1 or pix.height < 1 or pix.width * pix.height > MAX_PIXELS:
                return {"ok": False, "reason": "pdf_limit"}
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            return _recognize_loaded(image, languages[language], tesserocr)
    except (ValueError, RuntimeError, OSError, MemoryError):
        return {"ok": False, "reason": "invalid_pdf"}
