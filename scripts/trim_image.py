#!/usr/bin/env python3
"""
Auto-trim external whitespace from an image and optionally add uniform padding.

Usage:
  python3 scripts/trim_image.py INPUT_PATH --out OUTPUT_PATH [--pad 24] [--bg white] [--tol 12]

Requires Pillow (PIL).
"""

import argparse
from typing import Tuple, Optional

from PIL import Image, ImageChops


def parse_color(color: str) -> Tuple[int, int, int]:
    color = color.strip().lstrip('#')
    if color.lower() in {"white", "#fff", "fff"}:
        return (255, 255, 255)
    if color.lower() in {"black", "#000", "000"}:
        return (0, 0, 0)
    if len(color) == 3:
        r, g, b = (int(c * 2, 16) for c in color)
        return (r, g, b)
    if len(color) == 6:
        r = int(color[0:2], 16)
        g = int(color[2:4], 16)
        b = int(color[4:6], 16)
        return (r, g, b)
    raise ValueError(f"Unsupported color: {color}")


def median_color(colors):
    rs = sorted(c[0] for c in colors)
    gs = sorted(c[1] for c in colors)
    bs = sorted(c[2] for c in colors)
    mid = len(colors) // 2
    return (rs[mid], gs[mid], bs[mid])


def find_content_bbox(im: Image.Image, tol: int, fallback_bg: Tuple[int, int, int]) -> Optional[Tuple[int, int, int, int]]:
    """Return bbox of non-background content. If image has alpha, use it. Otherwise
    estimate background from corners and trim areas close to that color within tolerance.
    """
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA")

    if im.mode == "RGBA":
        alpha = im.split()[3]
        bbox = alpha.getbbox()
        if bbox:
            return bbox
        # No visible pixels; fall back to color-based
        base = im.convert("RGB")
    else:
        base = im

    w, h = base.size
    # Sample corners and a few edge points
    samples = [
        base.getpixel((0, 0)),
        base.getpixel((w - 1, 0)),
        base.getpixel((0, h - 1)),
        base.getpixel((w - 1, h - 1)),
        base.getpixel((w // 2, 0)),
        base.getpixel((w // 2, h - 1)),
        base.getpixel((0, h // 2)),
        base.getpixel((w - 1, h // 2)),
    ]
    bg = median_color(samples) if samples else fallback_bg

    # Difference from solid background color
    bg_image = Image.new("RGB", base.size, bg)
    diff = ImageChops.difference(base, bg_image)
    # Convert to grayscale and threshold by tolerance
    gray = diff.convert("L")
    mask = gray.point(lambda p, t=tol: 255 if p > t else 0)
    bbox = mask.getbbox()
    return bbox


def trim_and_pad(input_path: str, output_path: str, pad: int, bg_color: Tuple[int, int, int], tol: int) -> None:
    im = Image.open(input_path)
    bbox = find_content_bbox(im, tol=tol, fallback_bg=bg_color)

    if bbox:
        im = im.crop(bbox)

    if pad > 0:
        w, h = im.size
        new_w, new_h = w + pad * 2, h + pad * 2
        canvas_mode = "RGBA" if im.mode == "RGBA" else "RGB"
        if canvas_mode == "RGBA":
            # Add opaque background in requested color
            bg = Image.new("RGB", (new_w, new_h), bg_color)
            bg.paste(im, (pad, pad), im)
            out = bg
        else:
            out = Image.new(canvas_mode, (new_w, new_h), bg_color)
            out.paste(im, (pad, pad))
    else:
        out = im

    out.save(output_path)


def main():
    ap = argparse.ArgumentParser(description="Auto-trim whitespace and add padding")
    ap.add_argument("input", help="Input image path")
    ap.add_argument("--out", required=True, help="Output image path")
    ap.add_argument("--pad", type=int, default=24, help="Padding (px) to add after trim")
    ap.add_argument("--bg", default="white", help="Background color for padding (name or hex)")
    ap.add_argument("--tol", type=int, default=12, help="Tolerance for background detection (0-255)")
    args = ap.parse_args()

    bg = parse_color(args.bg)
    trim_and_pad(args.input, args.out, pad=args.pad, bg_color=bg, tol=args.tol)


if __name__ == "__main__":
    main()

