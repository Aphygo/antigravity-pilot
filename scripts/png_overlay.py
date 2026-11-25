#!/usr/bin/env python3
"""Zero-dependency pure Python 3.9+ PNG decoder/encoder and visual overlay engine.

Provides deterministic, cross-platform (Windows, macOS, Linux) PNG I/O,
alpha-blended drawing primitives, a crisp 5x7 bitmap font, logical coordinate
grid overlays, and Set-of-Mark (SoM) interactive element annotations.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

Color = Union[Tuple[int, int, int], Tuple[int, int, int, int]]
PathLike = Union[str, Path]

# High-contrast cycling palette (blue, emerald, amber, magenta, cyan, coral)
SOM_PALETTE: Tuple[Tuple[int, int, int, int], ...] = (
    (37, 99, 235, 240),    # Blue
    (5, 150, 105, 240),    # Emerald
    (217, 119, 6, 240),    # Amber
    (219, 39, 119, 240),   # Magenta
    (8, 145, 178, 240),    # Cyan
    (239, 68, 68, 240),    # Coral
)

# 5x7 bitmap font (each glyph is 7 rows of 5 pixels)
_FONT_5X7_RAW: Dict[str, Tuple[str, ...]] = {
    " ": (
        ".....",
        ".....",
        ".....",
        ".....",
        ".....",
        ".....",
        ".....",
    ),
    "0": (
        ".###.",
        "#...#",
        "#..##",
        "#.#.#",
        "##..#",
        "#...#",
        ".###.",
    ),
    "1": (
        "..#..",
        ".##..",
        "..#..",
        "..#..",
        "..#..",
        "..#..",
        ".###.",
    ),
    "2": (
        ".###.",
        "#...#",
        "....#",
        "..##.",
        ".#...",
        "#....",
        "#####",
    ),
    "3": (
        ".###.",
        "#...#",
        "....#",
        "..##.",
        "....#",
        "#...#",
        ".###.",
    ),
    "4": (
        "...#.",
        "..##.",
        ".#.#.",
        "#..#.",
        "#####",
        "...#.",
        "...#.",
    ),
    "5": (
        "#####",
        "#....",
        "####.",
        "....#",
        "....#",
        "#...#",
        ".###.",
    ),
    "6": (
        ".###.",
        "#....",
        "####.",
        "#...#",
        "#...#",
        "#...#",
        ".###.",
    ),
    "7": (
        "#####",
        "....#",
        "...#.",
        "..#..",
        ".#...",
        ".#...",
        ".#...",
    ),
    "8": (
        ".###.",
        "#...#",
        "#...#",
        ".###.",
        "#...#",
        "#...#",
        ".###.",
    ),
    "9": (
        ".###.",
        "#...#",
        "#...#",
        ".####",
        "....#",
        "...#.",
        ".##..",
    ),
    "A": (
        ".###.",
        "#...#",
        "#...#",
        "#####",
        "#...#",
        "#...#",
        "#...#",
    ),
    "B": (
        "####.",
        "#...#",
        "#...#",
        "####.",
        "#...#",
        "#...#",
        "####.",
    ),
    "C": (
        ".###.",
        "#...#",
        "#....",
        "#....",
        "#....",
        "#...#",
        ".###.",
    ),
    "D": (
        "####.",
        "#...#",
        "#...#",
        "#...#",
        "#...#",
        "#...#",
        "####.",
    ),
    "E": (
        "#####",
        "#....",
        "#....",
        "####.",
        "#....",
        "#....",
        "#####",
    ),
    "F": (
        "#####",
        "#....",
        "#....",
        "####.",
        "#....",
        "#....",
        "#....",
    ),
    "G": (
        ".###.",
        "#...#",
        "#....",
        "#.###",
        "#...#",
        "#...#",
        ".###.",
    ),
    "H": (
        "#...#",
        "#...#",
        "#...#",
        "#####",
        "#...#",
        "#...#",
        "#...#",
    ),
    "I": (
        ".###.",
        "..#..",
        "..#..",
        "..#..",
        "..#..",
        "..#..",
        ".###.",
    ),
    "J": (
        "..###",
        "...#.",
        "...#.",
        "...#.",
        "#..#.",
        "#..#.",
        ".##..",
    ),
    "K": (
        "#...#",
        "#..#.",
        "#.#..",
        "##...",
        "#.#..",
        "#..#.",
        "#...#",
    ),
    "L": (
        "#....",
        "#....",
        "#....",
        "#....",
        "#....",
        "#....",
        "#####",
    ),
    "M": (
        "#...#",
        "##.##",
        "#.#.#",
        "#.#.#",
        "#...#",
        "#...#",
        "#...#",
    ),
    "N": (
        "#...#",
        "##..#",
        "#.#.#",
        "#..##",
        "#...#",
        "#...#",
        "#...#",
    ),
    "O": (
        ".###.",
        "#...#",
        "#...#",
        "#...#",
        "#...#",
        "#...#",
        ".###.",
    ),
    "P": (
        "####.",
        "#...#",
        "#...#",
        "####.",
        "#....",
        "#....",
        "#....",
    ),
    "Q": (
        ".###.",
        "#...#",
        "#...#",
        "#...#",
        "#.#.#",
        "#..#.",
        ".##.#",
    ),
    "R": (
        "####.",
        "#...#",
        "#...#",
        "####.",
        "#.#..",
        "#..#.",
        "#...#",
    ),
    "S": (
        ".###.",
        "#...#",
        "#....",
        ".###.",
        "....#",
        "#...#",
        ".###.",
    ),
    "T": (
        "#####",
        "..#..",
        "..#..",
        "..#..",
        "..#..",
        "..#..",
        "..#..",
    ),
    "U": (
        "#...#",
        "#...#",
        "#...#",
        "#...#",
        "#...#",
        "#...#",
        ".###.",
    ),
    "V": (
        "#...#",
        "#...#",
        "#...#",
        "#...#",
        "#...#",
        ".#.#.",
        "..#..",
    ),
    "W": (
        "#...#",
        "#...#",
        "#...#",
        "#.#.#",
        "#.#.#",
        "##.##",
        "#...#",
    ),
    "X": (
        "#...#",
        "#...#",
        ".#.#.",
        "..#..",
        ".#.#.",
        "#...#",
        "#...#",
    ),
    "Y": (
        "#...#",
        "#...#",
        ".#.#.",
        "..#..",
        "..#..",
        "..#..",
        "..#..",
    ),
    "Z": (
        "#####",
        "....#",
        "...#.",
        "..#..",
        ".#...",
        "#....",
        "#####",
    ),
    "a": (
        ".....",
        ".....",
        ".###.",
        "....#",
        ".####",
        "#...#",
        ".####",
    ),
    "b": (
        "#....",
        "#....",
        "####.",
        "#...#",
        "#...#",
        "#...#",
        "####.",
    ),
    "c": (
        ".....",
        ".....",
        ".###.",
        "#....",
        "#....",
        "#...#",
        ".###.",
    ),
    "d": (
        "....#",
        "....#",
        ".####",
        "#...#",
        "#...#",
        "#...#",
        ".####",
    ),
    "e": (
        ".....",
        ".....",
        ".###.",
        "#...#",
        "#####",
        "#....",
        ".###.",
    ),
    "f": (
        "..##.",
        ".#..#",
        ".#...",
        "###..",
        ".#...",
        ".#...",
        ".#...",
    ),
    "g": (
        ".....",
        ".####",
        "#...#",
        "#...#",
        ".####",
        "....#",
        ".###.",
    ),
    "h": (
        "#....",
        "#....",
        "####.",
        "#...#",
        "#...#",
        "#...#",
        "#...#",
    ),
    "i": (
        "..#..",
        ".....",
        ".##..",
        "..#..",
        "..#..",
        "..#..",
        ".###.",
    ),
    "j": (
        "...#.",
        ".....",
        "..##.",
        "...#.",
        "...#.",
        "#..#.",
        ".##..",
    ),
    "k": (
        "#....",
        "#....",
        "#..#.",
        "#.#..",
        "##...",
        "#.#..",
        "#..#.",
    ),
    "l": (
        ".##..",
        "..#..",
        "..#..",
        "..#..",
        "..#..",
        "..#..",
        ".###.",
    ),
    "m": (
        ".....",
        ".....",
        "##.#.",
        "#.#.#",
        "#.#.#",
        "#.#.#",
        "#...#",
    ),
    "n": (
        ".....",
        ".....",
        "####.",
        "#...#",
        "#...#",
        "#...#",
        "#...#",
    ),
    "o": (
        ".....",
        ".....",
        ".###.",
        "#...#",
        "#...#",
        "#...#",
        ".###.",
    ),
    "p": (
        ".....",
        "####.",
        "#...#",
        "#...#",
        "####.",
        "#....",
        "#....",
    ),
    "q": (
        ".....",
        ".####",
        "#...#",
        "#...#",
        ".####",
        "....#",
        "....#",
    ),
    "r": (
        ".....",
        ".....",
        "#.##.",
        "##..#",
        "#....",
        "#....",
        "#....",
    ),
    "s": (
        ".....",
        ".....",
        ".####",
        "#....",
        ".###.",
        "....#",
        "####.",
    ),
    "t": (
        ".#...",
        ".#...",
        "####.",
        ".#...",
        ".#...",
        ".#..#",
        "..##.",
    ),
    "u": (
        ".....",
        ".....",
        "#...#",
        "#...#",
        "#...#",
        "#..##",
        ".##.#",
    ),
    "v": (
        ".....",
        ".....",
        "#...#",
        "#...#",
        "#...#",
        ".#.#.",
        "..#..",
    ),
    "w": (
        ".....",
        ".....",
        "#...#",
        "#...#",
        "#.#.#",
        "#.#.#",
        ".#.#.",
    ),
    "x": (
        ".....",
        ".....",
        "#...#",
        ".#.#.",
        "..#..",
        ".#.#.",
        "#...#",
    ),
    "y": (
        ".....",
        "#...#",
        "#...#",
        "#...#",
        ".####",
        "....#",
        ".###.",
    ),
    "z": (
        ".....",
        ".....",
        "#####",
        "...#.",
        "..#..",
        ".#...",
        "#####",
    ),
    "-": (
        ".....",
        ".....",
        ".....",
        "#####",
        ".....",
        ".....",
        ".....",
    ),
    "_": (
        ".....",
        ".....",
        ".....",
        ".....",
        ".....",
        ".....",
        "#####",
    ),
    ",": (
        ".....",
        ".....",
        ".....",
        ".....",
        "..##.",
        "..#..",
        ".#...",
    ),
    ".": (
        ".....",
        ".....",
        ".....",
        ".....",
        ".....",
        ".##..",
        ".##..",
    ),
    ":": (
        ".....",
        "..##.",
        "..##.",
        ".....",
        "..##.",
        "..##.",
        ".....",
    ),
    "[": (
        ".###.",
        ".#...",
        ".#...",
        ".#...",
        ".#...",
        ".#...",
        ".###.",
    ),
    "]": (
        ".###.",
        "...#.",
        "...#.",
        "...#.",
        "...#.",
        "...#.",
        ".###.",
    ),
    "(": (
        "...#.",
        "..#..",
        ".#...",
        ".#...",
        ".#...",
        "..#..",
        "...#.",
    ),
    ")": (
        ".#...",
        "..#..",
        "...#.",
        "...#.",
        "...#.",
        "..#..",
        ".#...",
    ),
    "/": (
        "....#",
        "...#.",
        "...#.",
        "..#..",
        ".#...",
        ".#...",
        "#....",
    ),
    "+": (
        ".....",
        "..#..",
        "..#..",
        "#####",
        "..#..",
        "..#..",
        ".....",
    ),
    "@": (
        ".###.",
        "#...#",
        "#.###",
        "#.#.#",
        "#.###",
        "#....",
        ".###.",
    ),
    "#": (
        ".#.#.",
        ".#.#.",
        "#####",
        ".#.#.",
        "#####",
        ".#.#.",
        ".#.#.",
    ),
    "!": (
        "..#..",
        "..#..",
        "..#..",
        "..#..",
        "..#..",
        ".....",
        "..#..",
    ),
    "?": (
        ".###.",
        "#...#",
        "....#",
        "..##.",
        "..#..",
        ".....",
        "..#..",
    ),
    "=": (
        ".....",
        ".....",
        "#####",
        ".....",
        "#####",
        ".....",
        ".....",
    ),
    "*": (
        ".....",
        "#.#.#",
        ".###.",
        "#####",
        ".###.",
        "#.#.#",
        ".....",
    ),
    "%": (
        "##..#",
        "##.#.",
        "..#..",
        ".#...",
        "#..##",
        "...##",
        ".....",
    ),
    ";": (
        ".....",
        "..##.",
        "..##.",
        ".....",
        "..##.",
        "..#..",
        ".#...",
    ),
    "<": (
        "...#.",
        "..#..",
        ".#...",
        "#....",
        ".#...",
        "..#..",
        "...#.",
    ),
    ">": (
        ".#...",
        "..#..",
        "...#.",
        "....#",
        "...#.",
        "..#..",
        ".#...",
    ),
    "'": (
        "..#..",
        "..#..",
        ".#...",
        ".....",
        ".....",
        ".....",
        ".....",
    ),
    '"': (
        ".#.#.",
        ".#.#.",
        ".....",
        ".....",
        ".....",
        ".....",
        ".....",
    ),
    "\\": (
        "#....",
        ".#...",
        ".#...",
        "..#..",
        "...#.",
        "...#.",
        "....#",
    ),
    "|": (
        "..#..",
        "..#..",
        "..#..",
        "..#..",
        "..#..",
        "..#..",
        "..#..",
    ),
}

# Precompile each glyph into a tuple of 7 lists of active column indices (0..4)
_FONT_5X7: Dict[str, Tuple[Tuple[int, ...], ...]] = {
    ch: tuple(
        tuple(col for col, px in enumerate(row) if px == "#")
        for row in rows
    )
    for ch, rows in _FONT_5X7_RAW.items()
}
_FALLBACK_GLYPH = _FONT_5X7["?"]


def _normalize_color(color: Color) -> Tuple[int, int, int, int]:
    if len(color) == 3:
        r, g, b = color
        a = 255
    elif len(color) == 4:
        r, g, b, a = color
    else:
        raise ValueError(f"Color must be a 3-tuple (R,G,B) or 4-tuple (R,G,B,A), got {color!r}")
    return (
        max(0, min(255, int(r))),
        max(0, min(255, int(g))),
        max(0, min(255, int(b))),
        max(0, min(255, int(a))),
    )


def _blend_luts(r: int, g: int, b: int, a: int) -> Tuple[bytes, bytes, bytes, bytes]:
    inv_a = 255 - a
    lut_r = bytes((r * a + d * inv_a + 127) // 255 for d in range(256))
    lut_g = bytes((g * a + d * inv_a + 127) // 255 for d in range(256))
    lut_b = bytes((b * a + d * inv_a + 127) // 255 for d in range(256))
    lut_a = bytes(min(255, a + (d * inv_a + 127) // 255) for d in range(256))
    return lut_r, lut_g, lut_b, lut_a


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type)
    crc = zlib.crc32(data, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def read_png_dimensions(path: PathLike) -> Tuple[int, int]:
    """Read only (width, height) from a PNG file's IHDR header."""
    with open(path, "rb") as f:
        header = f.read(24)
    if len(header) < 24 or header[:8] != PNG_SIGNATURE or header[12:16] != b"IHDR":
        raise ValueError(f"Not a valid PNG file: {path}")
    w, h = struct.unpack(">II", header[16:24])
    return int(w), int(h)


def crop_rgba(
    buf: Union[bytearray, bytes, memoryview],
    w: int,
    h: int,
    x: int,
    y: int,
    cw: int,
    ch: int,
) -> Tuple[int, int, bytearray]:
    """Crop a rectangular region from an RGBA buffer, clamped to image bounds."""
    if w <= 0 or h <= 0:
        raise ValueError(f"Invalid source image dimensions: {w}x{h}")
    x0 = max(0, min(w, int(x)))
    y0 = max(0, min(h, int(y)))
    x1 = max(x0, min(w, int(x) + int(cw)))
    y1 = max(y0, min(h, int(y) + int(ch)))
    out_w = x1 - x0
    out_h = y1 - y0
    if out_w <= 0 or out_h <= 0:
        raise ValueError(
            f"Empty crop region ({x},{y},{cw},{ch}) for {w}x{h} image"
        )
    src_stride = w * 4
    dst_stride = out_w * 4
    out = bytearray(dst_stride * out_h)
    mv_src = memoryview(buf)
    mv_dst = memoryview(out)
    for row in range(out_h):
        src_off = (y0 + row) * src_stride + x0 * 4
        dst_off = row * dst_stride
        mv_dst[dst_off : dst_off + dst_stride] = mv_src[src_off : src_off + dst_stride]
    return out_w, out_h, out


def detect_opaque_bounds(
    buf: Union[bytearray, bytes, memoryview],
    w: int,
    h: int,
    alpha_threshold: int = 250,
) -> Tuple[int, int, int, int]:
    """Return (x, y, width, height) of the opaque content region inside an RGBA buffer.

    Fast O(1) exit when all four corners are opaque (fullscreen / shadowless captures).
    When a window screenshot includes transparent compositor drop-shadow padding
    (e.g. macOS ScreenCaptureKit or Windows DWM shadows), accurately detects the
    opaque window bounds so Set-of-Mark overlays align pixel-for-pixel.
    """
    if w <= 0 or h <= 0:
        return 0, 0, w, h

    # Fast path: if all 4 corners are opaque, there is no transparent border padding.
    if (
        buf[3] >= alpha_threshold
        and buf[(w - 1) * 4 + 3] >= alpha_threshold
        and buf[((h - 1) * w) * 4 + 3] >= alpha_threshold
        and buf[(h * w - 1) * 4 + 3] >= alpha_threshold
    ):
        return 0, 0, w, h

    row_stride = w * 4

    def row_opaque_span(y: int) -> Optional[Tuple[int, int]]:
        off = y * row_stride
        alphas = buf[off + 3 : off + row_stride : 4]
        if max(alphas) < alpha_threshold:
            return None
        left = 0
        while left < w and alphas[left] < alpha_threshold:
            left += 1
        right = w - 1
        while right >= left and alphas[right] < alpha_threshold:
            right -= 1
        return left, right

    min_y = 0
    while min_y < h and row_opaque_span(min_y) is None:
        min_y += 1
    if min_y >= h:
        return 0, 0, w, h

    max_y = h - 1
    while max_y > min_y and row_opaque_span(max_y) is None:
        max_y -= 1

    # Sample horizontal spans across the window body (avoiding rounded corners)
    sample_ys = sorted(
        {
            min_y + (max_y - min_y) // 4,
            (min_y + max_y) // 2,
            min_y + (3 * (max_y - min_y)) // 4,
        }
    )
    min_x = w
    max_x = -1
    for sy in sample_ys:
        span = row_opaque_span(sy)
        if span is not None:
            if span[0] < min_x:
                min_x = span[0]
            if span[1] > max_x:
                max_x = span[1]

    if min_x > max_x:
        return 0, 0, w, h
    return min_x, min_y, max_x - min_x + 1, max_y - min_y + 1


def _unfilter_scanlines(
    raw: bytes,
    width: int,
    height: int,
    bpp: int,
    stride: int,
) -> bytearray:
    """Reconstruct raw PNG scanlines for all 5 PNG filter types (0..4).

    Uses exact SWAR (arbitrary-precision integer) vectorization for Filter 1 (Sub)
    and Filter 2 (Up), giving C-level speed on Retina screenshots in pure stdlib.
    """
    expected = (stride + 1) * height
    if len(raw) < expected:
        raise ValueError(
            f"Truncated PNG image data: expected {expected} bytes, got {len(raw)}"
        )

    out = bytearray(stride * height)
    mv_raw = memoryview(raw)
    mv_out = memoryview(out)

    # Precompute SWAR masks lazily only if Filter 1 or Filter 2 appears
    mask_lo = 0
    mask_hi = 0
    full_mask = 0
    swar_ready = False

    def ensure_swar() -> None:
        nonlocal mask_lo, mask_hi, full_mask, swar_ready
        if not swar_ready:
            mask_lo = int.from_bytes(b"\x7f" * stride, "little")
            mask_hi = int.from_bytes(b"\x80" * stride, "little")
            full_mask = (1 << (stride * 8)) - 1
            swar_ready = True

    prev_bytes: bytes = bytes(stride)
    src_stride = stride + 1

    for y in range(height):
        src_start = y * src_stride
        flt = raw[src_start]
        row_slice = mv_raw[src_start + 1 : src_start + src_stride]
        dst_start = y * stride
        dst_end = dst_start + stride

        if flt == 0:
            # None
            mv_out[dst_start:dst_end] = row_slice
            prev_bytes = row_slice.tobytes()

        elif flt == 1:
            # Sub: Recon(x) = Filt(x) + Recon(x - bpp) mod 256
            # Parallel prefix sum by doubling using exact bytewise SWAR add.
            ensure_swar()
            acc = int.from_bytes(row_slice, "little")
            step = bpp
            while step < stride:
                shifted = (acc << (step * 8)) & full_mask
                acc = ((acc & mask_lo) + (shifted & mask_lo)) ^ ((acc ^ shifted) & mask_hi)
                step <<= 1
            prev_bytes = acc.to_bytes(stride, "little")
            mv_out[dst_start:dst_end] = prev_bytes

        elif flt == 2:
            # Up: Recon(x) = Filt(x) + Prior(x) mod 256
            if y == 0:
                mv_out[dst_start:dst_end] = row_slice
                prev_bytes = row_slice.tobytes()
            else:
                ensure_swar()
                a_int = int.from_bytes(row_slice, "little")
                b_int = int.from_bytes(prev_bytes, "little")
                res_int = ((a_int & mask_lo) + (b_int & mask_lo)) ^ ((a_int ^ b_int) & mask_hi)
                prev_bytes = res_int.to_bytes(stride, "little")
                mv_out[dst_start:dst_end] = prev_bytes

        elif flt == 3:
            # Average: Recon(x) = Filt(x) + floor((Recon(x - bpp) + Prior(x)) / 2) mod 256
            cur = bytearray(row_slice)
            prev = prev_bytes
            for i in range(min(bpp, stride)):
                cur[i] = (cur[i] + (prev[i] >> 1)) & 0xFF
            for i in range(bpp, stride):
                cur[i] = (cur[i] + ((cur[i - bpp] + prev[i]) >> 1)) & 0xFF
            mv_out[dst_start:dst_end] = cur
            prev_bytes = bytes(cur)

        elif flt == 4:
            # Paeth: Recon(x) = Filt(x) + PaethPredictor(a, b, c) mod 256
            cur = bytearray(row_slice)
            prev = prev_bytes
            for i in range(min(bpp, stride)):
                cur[i] = (cur[i] + prev[i]) & 0xFF
            for i in range(bpp, stride):
                a = cur[i - bpp]
                b = prev[i]
                c = prev[i - bpp]
                p = a + b - c
                pa = p - a
                if pa < 0:
                    pa = -pa
                pb = p - b
                if pb < 0:
                    pb = -pb
                pc = p - c
                if pc < 0:
                    pc = -pc
                if pa <= pb and pa <= pc:
                    pr = a
                elif pb <= pc:
                    pr = b
                else:
                    pr = c
                cur[i] = (cur[i] + pr) & 0xFF
            mv_out[dst_start:dst_end] = cur
            prev_bytes = bytes(cur)

        else:
            raise ValueError(f"Unsupported PNG filter type {flt} on scanline {y}")

    return out


def read_png(path: PathLike) -> Tuple[int, int, bytearray]:
    """Decode a PNG image into (width, height, rgba_bytearray).

    Supports 8-bit (and 16-bit downsampled to 8-bit) non-interlaced PNGs across
    all 5 PNG color types:
      - 0: Grayscale (+ optional tRNS)
      - 2: Truecolor RGB (+ optional tRNS)
      - 3: Indexed / Palette (PLTE + optional tRNS)
      - 4: Grayscale + Alpha
      - 6: Truecolor RGBA
    Handles all 5 PNG scanline filters (0..4) and concatenated IDAT chunks.
    """
    with open(path, "rb") as f:
        data = f.read()

    if len(data) < 8 or data[:8] != PNG_SIGNATURE:
        raise ValueError(f"Invalid PNG signature in {path}")

    pos = 8
    width = height = 0
    bit_depth = color_type = compression = filter_method = interlace = 0
    seen_ihdr = False
    palette: Optional[bytes] = None
    trns: Optional[bytes] = None
    idat_chunks: List[bytes] = []

    data_len = len(data)
    while pos + 12 <= data_len:
        chunk_len = struct.unpack(">I", data[pos : pos + 4])[0]
        chunk_type = data[pos + 4 : pos + 8]
        body_start = pos + 8
        body_end = body_start + chunk_len
        if body_end + 4 > data_len:
            raise ValueError(f"Truncated PNG chunk {chunk_type!r} in {path}")
        body = data[body_start:body_end]
        stored_crc = struct.unpack(">I", data[body_end : body_end + 4])[0]
        calc_crc = zlib.crc32(data[pos + 4 : body_end]) & 0xFFFFFFFF
        if stored_crc != calc_crc:
            raise ValueError(
                f"CRC mismatch in PNG chunk {chunk_type!r}: "
                f"expected 0x{calc_crc:08x}, got 0x{stored_crc:08x}"
            )
        pos = body_end + 4

        if chunk_type == b"IHDR":
            if chunk_len != 13:
                raise ValueError(f"Invalid IHDR chunk length: {chunk_len}")
            (
                width,
                height,
                bit_depth,
                color_type,
                compression,
                filter_method,
                interlace,
            ) = struct.unpack(">IIBBBBB", body)
            seen_ihdr = True
        elif chunk_type == b"PLTE":
            palette = body
        elif chunk_type == b"tRNS":
            trns = body
        elif chunk_type == b"IDAT":
            idat_chunks.append(body)
        elif chunk_type == b"IEND":
            break

    if not seen_ihdr or width <= 0 or height <= 0:
        raise ValueError(f"Missing or invalid IHDR in {path}")
    if compression != 0 or filter_method != 0:
        raise ValueError(
            f"Unsupported PNG compression/filter method ({compression}/{filter_method})"
        )
    if interlace != 0:
        raise ValueError(f"Interlaced PNGs (interlace={interlace}) are not supported")
    if color_type not in (0, 2, 3, 4, 6):
        raise ValueError(f"Unsupported PNG color type: {color_type}")
    if bit_depth not in (8, 16) or (color_type == 3 and bit_depth != 8):
        raise ValueError(
            f"Unsupported PNG bit depth {bit_depth} for color type {color_type}"
        )

    channels_map = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
    channels = channels_map[color_type]
    bytes_per_sample = bit_depth // 8
    bpp = channels * bytes_per_sample
    stride = width * bpp

    raw = zlib.decompress(b"".join(idat_chunks))
    unfiltered = _unfilter_scanlines(raw, width, height, bpp, stride)

    if bit_depth == 16:
        # Downsample 16-bit big-endian channels to 8-bit by taking high bytes
        unfiltered = bytearray(unfiltered[0::2])

    pixel_count = width * height

    if color_type == 6:
        return width, height, unfiltered

    if color_type == 2:
        rgba = bytearray(pixel_count * 4)
        rgba[0::4] = unfiltered[0::3]
        rgba[1::4] = unfiltered[1::3]
        rgba[2::4] = unfiltered[2::3]
        rgba[3::4] = b"\xff" * pixel_count
        if trns and len(trns) >= 6:
            tr, tg, tb = struct.unpack(">HHH", trns[:6])
            if bit_depth == 16:
                tr, tg, tb = tr >> 8, tg >> 8, tb >> 8
            for idx in range(pixel_count):
                off = idx * 4
                if rgba[off] == tr and rgba[off + 1] == tg and rgba[off + 2] == tb:
                    rgba[off + 3] = 0
        return width, height, rgba

    if color_type == 0:
        rgba = bytearray(pixel_count * 4)
        rgba[0::4] = unfiltered
        rgba[1::4] = unfiltered
        rgba[2::4] = unfiltered
        rgba[3::4] = b"\xff" * pixel_count
        if trns and len(trns) >= 2:
            tgray = struct.unpack(">H", trns[:2])[0]
            if bit_depth == 16:
                tgray >>= 8
            for idx in range(pixel_count):
                if unfiltered[idx] == tgray:
                    rgba[idx * 4 + 3] = 0
        return width, height, rgba

    if color_type == 4:
        gray = unfiltered[0::2]
        alpha = unfiltered[1::2]
        rgba = bytearray(pixel_count * 4)
        rgba[0::4] = gray
        rgba[1::4] = gray
        rgba[2::4] = gray
        rgba[3::4] = alpha
        return width, height, rgba

    # color_type == 3 (Indexed / Palette)
    if not palette or len(palette) % 3 != 0:
        raise ValueError(f"Palette PNG missing valid PLTE chunk: {path}")
    num_entries = len(palette) // 3
    lut: List[bytes] = []
    for i in range(256):
        if i < num_entries:
            pr = palette[i * 3]
            pg = palette[i * 3 + 1]
            pb = palette[i * 3 + 2]
            pa = trns[i] if (trns and i < len(trns)) else 255
            lut.append(bytes((pr, pg, pb, pa)))
        else:
            lut.append(b"\x00\x00\x00\xff")
    rgba = bytearray().join(lut[idx] for idx in unfiltered)
    return width, height, rgba


def write_png(path: PathLike, w: int, h: int, rgba: Union[bytearray, bytes, memoryview]) -> None:
    """Encode an 8-bit RGBA buffer into a valid PNG file."""
    if w <= 0 or h <= 0:
        raise ValueError(f"Invalid PNG dimensions: {w}x{h}")
    expected = w * h * 4
    if len(rgba) != expected:
        raise ValueError(
            f"RGBA buffer size mismatch: expected {expected} bytes for {w}x{h}, got {len(rgba)}"
        )

    stride = w * 4
    dst_stride = stride + 1
    raw = bytearray(dst_stride * h)
    mv_src = memoryview(rgba)
    mv_dst = memoryview(raw)

    # Filter type 0 (None): leading byte per scanline is already 0x00.
    for y in range(h):
        dst_off = y * dst_stride
        src_off = y * stride
        mv_dst[dst_off + 1 : dst_off + dst_stride] = mv_src[src_off : src_off + stride]

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    compressed = zlib.compress(raw, level=6)

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(PNG_SIGNATURE)
        f.write(_png_chunk(b"IHDR", ihdr))
        f.write(_png_chunk(b"IDAT", compressed))
        f.write(_png_chunk(b"IEND", b""))


def fill_rect(
    buf: bytearray,
    w: int,
    h: int,
    x: int,
    y: int,
    rw: int,
    rh: int,
    color: Color,
) -> None:
    """Fill a rectangle on an RGBA buffer with source-over alpha blending."""
    if rw <= 0 or rh <= 0 or w <= 0 or h <= 0:
        return
    r, g, b, a = _normalize_color(color)
    if a == 0:
        return

    x0 = max(0, int(x))
    y0 = max(0, int(y))
    x1 = min(w, int(x) + int(rw))
    y1 = min(h, int(y) + int(rh))
    if x0 >= x1 or y0 >= y1:
        return

    span_w = x1 - x0
    row_stride = w * 4

    if a == 255:
        solid_row = bytes((r, g, b, 255)) * span_w
        span_bytes = span_w * 4
        for row in range(y0, y1):
            off = row * row_stride + x0 * 4
            buf[off : off + span_bytes] = solid_row
        return

    lut_r, lut_g, lut_b, lut_a = _blend_luts(r, g, b, a)
    span_bytes = span_w * 4
    for row in range(y0, y1):
        s = row * row_stride + x0 * 4
        e = s + span_bytes
        buf[s:e:4] = bytes(buf[s:e:4]).translate(lut_r)
        buf[s + 1 : e : 4] = bytes(buf[s + 1 : e : 4]).translate(lut_g)
        buf[s + 2 : e : 4] = bytes(buf[s + 2 : e : 4]).translate(lut_b)
        buf[s + 3 : e : 4] = bytes(buf[s + 3 : e : 4]).translate(lut_a)


def draw_hline(
    buf: bytearray,
    w: int,
    h: int,
    x: int,
    y: int,
    length: int,
    color: Color,
) -> None:
    """Draw a 1-pixel horizontal line with alpha blending."""
    fill_rect(buf, w, h, x, y, length, 1, color)


def draw_vline(
    buf: bytearray,
    w: int,
    h: int,
    x: int,
    y: int,
    length: int,
    color: Color,
) -> None:
    """Draw a 1-pixel vertical line with alpha blending."""
    if length <= 0 or w <= 0 or h <= 0:
        return
    ix = int(x)
    if ix < 0 or ix >= w:
        return
    r, g, b, a = _normalize_color(color)
    if a == 0:
        return

    y0 = max(0, int(y))
    y1 = min(h, int(y) + int(length))
    if y0 >= y1:
        return

    stride = w * 4
    count = y1 - y0
    s = (y0 * w + ix) * 4
    e = (y1 * w + ix) * 4

    if a == 255:
        buf[s:e:stride] = bytes((r,)) * count
        buf[s + 1 : e : stride] = bytes((g,)) * count
        buf[s + 2 : e : stride] = bytes((b,)) * count
        buf[s + 3 : e : stride] = bytes((255,)) * count
        return

    lut_r, lut_g, lut_b, lut_a = _blend_luts(r, g, b, a)
    buf[s:e:stride] = bytes(buf[s:e:stride]).translate(lut_r)
    buf[s + 1 : e : stride] = bytes(buf[s + 1 : e : stride]).translate(lut_g)
    buf[s + 2 : e : stride] = bytes(buf[s + 2 : e : stride]).translate(lut_b)
    buf[s + 3 : e : stride] = bytes(buf[s + 3 : e : stride]).translate(lut_a)


def draw_rect_outline(
    buf: bytearray,
    w: int,
    h: int,
    x: int,
    y: int,
    rw: int,
    rh: int,
    color: Color,
    thickness: int = 2,
) -> None:
    """Draw a rectangle outline without double-blending corners."""
    if rw <= 0 or rh <= 0 or thickness <= 0:
        return
    t = int(thickness)
    ix, iy, irw, irh = int(x), int(y), int(rw), int(rh)
    if irw <= 2 * t or irh <= 2 * t:
        fill_rect(buf, w, h, ix, iy, irw, irh, color)
        return
    # Top
    fill_rect(buf, w, h, ix, iy, irw, t, color)
    # Bottom
    fill_rect(buf, w, h, ix, iy + irh - t, irw, t, color)
    # Left (between top and bottom)
    fill_rect(buf, w, h, ix, iy + t, t, irh - 2 * t, color)
    # Right (between top and bottom)
    fill_rect(buf, w, h, ix + irw - t, iy + t, t, irh - 2 * t, color)


def measure_text(text: str, scale: int = 2) -> Tuple[int, int]:
    """Return (width, height) in pixels for text rendered with the 5x7 font."""
    s = max(1, int(scale))
    if not text:
        return 0, 7 * s
    return (len(text) * 6 - 1) * s, 7 * s


def draw_text(
    buf: bytearray,
    w: int,
    h: int,
    x: int,
    y: int,
    text: str,
    color: Color,
    scale: int = 2,
) -> None:
    """Render text onto the RGBA buffer using the built-in 5x7 bitmap font."""
    if not text:
        return
    s = max(1, int(scale))
    rgba = _normalize_color(color)
    if rgba[3] == 0:
        return

    cur_x = int(x)
    base_y = int(y)
    advance = 6 * s

    for ch in text:
        glyph = _FONT_5X7.get(ch, _FALLBACK_GLYPH)
        for row_idx, cols in enumerate(glyph):
            if not cols:
                continue
            py = base_y + row_idx * s
            for col_idx in cols:
                fill_rect(buf, w, h, cur_x + col_idx * s, py, s, s, rgba)
        cur_x += advance


def render_grid(
    buf: bytearray,
    w: int,
    h: int,
    step: int = 100,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
) -> None:
    """Overlay a semi-transparent logical coordinate grid with intersection badges.

    Grid lines are spaced at `step` logical units (`step * scale_x` / `step * scale_y`
    image pixels). Dark high-contrast coordinate badges (`100,200`, etc.) are placed
    at grid intersections.
    """
    if step <= 0 or w <= 0 or h <= 0:
        return
    sx = float(scale_x) if scale_x > 0 else 1.0
    sy = float(scale_y) if scale_y > 0 else 1.0
    px_step_x = step * sx
    px_step_y = step * sy
    if px_step_x < 4 or px_step_y < 4:
        return

    line_shadow = (0, 0, 0, 70)
    line_color = (0, 200, 255, 130)
    badge_bg = (15, 23, 42, 205)
    badge_border = (56, 189, 248, 175)
    badge_fg = (248, 250, 252, 255)

    xs: List[Tuple[int, int]] = []
    logical_x = step
    while True:
        px = int(round(logical_x * sx))
        if px >= w:
            break
        xs.append((logical_x, px))
        draw_vline(buf, w, h, px - 1, 0, h, line_shadow)
        draw_vline(buf, w, h, px, 0, h, line_color)
        logical_x += step

    ys: List[Tuple[int, int]] = []
    logical_y = step
    while True:
        py = int(round(logical_y * sy))
        if py >= h:
            break
        ys.append((logical_y, py))
        draw_hline(buf, w, h, 0, py - 1, w, line_shadow)
        draw_hline(buf, w, h, 0, py, w, line_color)
        logical_y += step

    text_scale = 2 if min(sx, sy) >= 1.5 else 1
    pad_x = 3 * text_scale
    pad_y = 2 * text_scale

    for logical_y, py in ys:
        for logical_x, px in xs:
            label = f"{logical_x},{logical_y}"
            tw, th = measure_text(label, scale=text_scale)
            bw = tw + pad_x * 2
            bh = th + pad_y * 2
            bx = px + 3
            by = py + 3
            if bx + bw > w:
                bx = max(0, px - bw - 3)
            if by + bh > h:
                by = max(0, py - bh - 3)
            fill_rect(buf, w, h, bx, by, bw, bh, badge_bg)
            draw_rect_outline(buf, w, h, bx, by, bw, bh, badge_border, thickness=1)
            draw_text(buf, w, h, bx + pad_x, by + pad_y, label, badge_fg, scale=text_scale)


def render_som_annotations(
    buf: bytearray,
    w: int,
    h: int,
    elements: Sequence[Dict[str, Any]],
    origin_x: float = 0.0,
    origin_y: float = 0.0,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
) -> int:
    """Draw Set-of-Mark (SoM) color-coded bounding boxes and label badges.

    Each element dict should provide:
      - `label` (or `short_id`, fallback `e{i+1}`)
      - `bounds`: `{"x": float, "y": float, "width": float, "height": float}`

    Returns the number of visible elements rendered.
    """
    if not elements or w <= 0 or h <= 0:
        return 0

    sx = float(scale_x) if scale_x > 0 else 1.0
    sy = float(scale_y) if scale_y > 0 else 1.0
    hidpi = max(sx, sy) >= 1.5
    thickness = 3 if hidpi else 2
    text_scale = 2 if hidpi else 1
    pad_x = 3 * text_scale
    pad_y = 2 * text_scale

    # Two-pass rendering: draw all outlines first, then badges on top so labels
    # are never obscured by neighboring bounding-box borders.
    draw_items: List[Tuple[int, int, int, int, str, Tuple[int, int, int, int]]] = []

    for idx, el in enumerate(elements):
        bounds = el.get("bounds")
        if not isinstance(bounds, dict):
            continue
        try:
            bx = float(bounds.get("x", 0.0))
            by = float(bounds.get("y", 0.0))
            bw = float(bounds.get("width", 0.0))
            bh = float(bounds.get("height", 0.0))
        except (TypeError, ValueError):
            continue
        if bw <= 0.0 or bh <= 0.0:
            continue

        px0 = int(round((bx - origin_x) * sx))
        py0 = int(round((by - origin_y) * sy))
        px1 = int(round((bx + bw - origin_x) * sx))
        py1 = int(round((by + bh - origin_y) * sy))

        # Clip to image bounds and skip completely invisible or degenerate rects
        cx0 = max(0, px0)
        cy0 = max(0, py0)
        cx1 = min(w, px1)
        cy1 = min(h, py1)
        if cx1 - cx0 < 2 or cy1 - cy0 < 2:
            continue

        label = str(el.get("label") or el.get("short_id") or f"e{idx + 1}")
        color = SOM_PALETTE[len(draw_items) % len(SOM_PALETTE)]
        draw_items.append((cx0, cy0, cx1 - cx0, cy1 - cy0, label, color))

    for rx, ry, rw, rh, _label, color in draw_items:
        # Subtle white outer halo helps dark colors stand out on dark backgrounds
        draw_rect_outline(buf, w, h, rx - 1, ry - 1, rw + 2, rh + 2, (255, 255, 255, 110), thickness=1)
        draw_rect_outline(buf, w, h, rx, ry, rw, rh, color, thickness=thickness)

    for rx, ry, _rw, _rh, label, color in draw_items:
        tw, th = measure_text(label, scale=text_scale)
        badge_w = tw + pad_x * 2
        badge_h = th + pad_y * 2

        badge_x = rx
        badge_y = ry - badge_h
        if badge_y < 0:
            # Shift inward when box is near top edge
            badge_y = ry
        if badge_x + badge_w > w:
            badge_x = max(0, w - badge_w)
        if badge_y + badge_h > h:
            badge_y = max(0, h - badge_h)

        badge_bg = (color[0], color[1], color[2], 245)
        fill_rect(buf, w, h, badge_x, badge_y, badge_w, badge_h, badge_bg)
        draw_rect_outline(buf, w, h, badge_x, badge_y, badge_w, badge_h, (255, 255, 255, 210), thickness=1)
        draw_text(
            buf,
            w,
            h,
            badge_x + pad_x,
            badge_y + pad_y,
            label,
            (255, 255, 255, 255),
            scale=text_scale,
        )

    return len(draw_items)


def annotate_png(
    in_path: PathLike,
    out_path: PathLike,
    elements: Optional[Sequence[Dict[str, Any]]] = None,
    draw_grid: bool = False,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
) -> Dict[str, Any]:
    """Read a PNG, apply optional grid and/or SoM annotations, and write output PNG."""
    w, h, buf = read_png(in_path)
    if draw_grid:
        render_grid(buf, w, h, step=100, scale_x=scale_x, scale_y=scale_y)
    rendered = 0
    if elements:
        rendered = render_som_annotations(
            buf,
            w,
            h,
            elements,
            origin_x=origin_x,
            origin_y=origin_y,
            scale_x=scale_x,
            scale_y=scale_y,
        )
    write_png(out_path, w, h, buf)
    return {
        "width": w,
        "height": h,
        "grid": bool(draw_grid),
        "rendered_elements": rendered,
        "output": str(out_path),
    }
