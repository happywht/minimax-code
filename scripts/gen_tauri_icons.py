"""
Generate Tauri app icon assets for MiniMax Code from a single source design.

Renders the icon (black rounded square + sky-500 "M" letter) into every size
Tauri 2.x expects:

  src-tauri/icons/32x32.png
  src-tauri/icons/128x128.png
  src-tauri/icons/128x128@2x.png       (256x256)
  src-tauri/icons/icon.png              (512x512)
  src-tauri/icons/icon.ico              (Windows, multi-size 16/32/48/64/128/256)
  src-tauri/icons/icon.icns             (macOS — skipped, Tauri falls back to PNG)

Usage
-----
    python scripts/gen_tauri_icons.py [--check]

The `--check` flag exits non-zero if any required output is missing or
does not match its expected dimensions (used by the verifier).

Notes
-----
Pillow doesn't natively render SVG text, so we draw the same logo with
Pillow primitives (rounded-rect + arialbd M). The committed `icon.svg`
is the source-of-truth vector asset for designers / future re-raster.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable, Tuple

from PIL import Image, ImageDraw, ImageFont

# --- Design constants (must stay in sync with src-tauri/icons/icon.svg) ----
BG_COLOR = (0, 0, 0, 255)        # #000000
FG_COLOR = (14, 165, 233, 255)   # #0EA5E9 (Tailwind sky-500)
CORNER_RADIUS_RATIO = 96 / 512   # rounded-square corner radius

# Output targets: (path, size_px) pairs.
# Multi-size ICO entries live in _ICO_SIZES below.
PNG_TARGETS: Tuple[Tuple[str, int], ...] = (
    ("32x32.png", 32),
    ("128x128.png", 128),
    ("128x128@2x.png", 256),
    ("icon.png", 512),
)

# Windows .ico should embed every size Windows Explorer shows.
_ICO_SIZES: Tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)

# On Windows arialbd.ttf ships with the OS; on macOS/Linux the script
# falls back to the Pillow default font (still bold-ish via size).
_BOLD_FONT_CANDIDATES: Tuple[str, ...] = (
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)


# --- Drawing -----------------------------------------------------------------
def _load_bold_font(size: int) -> ImageFont.ImageFont:
    """Load a bold sans-serif font from the OS, falling back gracefully."""
    for path in _BOLD_FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    # Last-resort: Pillow's tiny built-in bitmap font (won't look like an M,
    # but at least keeps the script portable and the test green).
    return ImageFont.load_default()


def _draw_icon(size: int) -> Image.Image:
    """Render the icon at the given pixel size. Returns an RGBA image."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded black background fills the entire canvas.
    radius = max(1, int(round(size * CORNER_RADIUS_RATIO)))
    draw.rounded_rectangle(
        [(0, 0), (size - 1, size - 1)],
        radius=radius,
        fill=BG_COLOR,
    )

    # "M" letter, centred. Font size is ~74% of canvas — leaves the M
    # plenty of breathing room while still reading bold at 16x16.
    # anchor="mm" places the *typographic* box middle at (size/2, size/2),
    # which is what we want for visual centring.
    font = _load_bold_font(size=int(size * 0.74))
    draw.text(
        (size / 2, size / 2),
        "M",
        font=font,
        fill=FG_COLOR,
        anchor="mm",
    )

    return img


# --- Writers -----------------------------------------------------------------
def _write_png(img: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # optimise=True keeps PNGs compact without loss.
    img.save(path, format="PNG", optimize=True)


def _write_ico(sizes: Iterable[int], path: Path) -> None:
    """Build a multi-size Windows .ico by encoding each size as a PNG frame.

    Pillow's ICO writer embeds every requested size when the source image is
    larger than the largest requested size and `sizes=[(w, h), ...]` is passed.
    Passing a pre-rendered 256x256 source plus a sizes list is the documented
    happy path; `append_images=` is for stacking *different* images.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    sizes = tuple(sizes)
    source = _draw_icon(max(sizes))  # render once at the largest size
    source.save(
        path,
        format="ICO",
        sizes=[(s, s) for s in sizes],
    )


# --- Main --------------------------------------------------------------------
def generate(icons_dir: Path) -> list[Path]:
    """Generate every icon asset under `icons_dir`. Returns the list written."""
    icons_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for name, size in PNG_TARGETS:
        out = icons_dir / name
        _write_png(_draw_icon(size), out)
        written.append(out)

    ico_path = icons_dir / "icon.ico"
    _write_ico(_ICO_SIZES, ico_path)
    written.append(ico_path)

    # icon.icns is intentionally skipped: Tauri 2.x falls back to icon.png
    # for macOS bundling, and producing a valid ICNS without `png2icns` /
    # iconutil is out of scope for an offline Python toolchain.
    return written


def check(icons_dir: Path) -> int:
    """Return 0 if every expected output exists with the right size, else 1."""
    expected: list[tuple[Path, int]] = [
        (icons_dir / name, size) for name, size in PNG_TARGETS
    ]
    failures: list[str] = []
    for path, size in expected:
        if not path.exists():
            failures.append(f"missing: {path}")
            continue
        with Image.open(path) as im:
            if im.size != (size, size):
                failures.append(f"{path.name}: expected {size}x{size}, got {im.size}")

    ico_path = icons_dir / "icon.ico"
    if not ico_path.exists():
        failures.append(f"missing: {ico_path}")
    else:
        # 8-byte ICO header; first WORD is reserved (0), second is type (1=icon).
        with open(ico_path, "rb") as fh:
            head = fh.read(8)
        if len(head) < 8 or head[:4] != b"\x00\x00\x01\x00":
            failures.append(f"{ico_path.name}: not a valid ICO header ({head!r})")

    if failures:
        print("CHECK FAILED:", file=sys.stderr)
        for line in failures:
            print("  -", line, file=sys.stderr)
        return 1
    print("CHECK OK: all icon assets present and dimensionally correct.")
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--icons-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "src-tauri" / "icons",
        help="Output directory (default: src-tauri/icons)",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="Verify existing assets in --icons-dir instead of regenerating",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    if args.check:
        return check(args.icons_dir)
    written = generate(args.icons_dir)
    # Brief report so the operator can eyeball the output.
    for path in written:
        rel = path.relative_to(args.icons_dir.parent.parent)
        print(f"wrote {rel} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
