"""Minimal PNG writer, so the toolkit stays dependency-free."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


def _chunk(tag: bytes, payload: bytes) -> bytes:
    body = tag + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))


def write_png(path: str | Path, rgba: bytes, width: int, height: int) -> Path:
    """Write an 8-bit RGBA PNG. *rgba* is row-major, 4 bytes per pixel."""
    if len(rgba) != width * height * 4:
        raise ValueError(f"expected {width * height * 4} bytes, got {len(rgba)}")
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)  # filter type 0 (none)
        raw += rgba[y * stride : (y + 1) * stride]
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _chunk(b"IEND", b"")
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    return path


def scale_rgba(rgba: bytes, width: int, height: int, factor: int) -> bytes:
    """Nearest-neighbour upscale; app icons need far more than 32x32."""
    if factor < 1:
        raise ValueError("factor must be >= 1")
    out = bytearray(width * factor * height * factor * 4)
    new_width = width * factor
    for y in range(height * factor):
        src_row = (y // factor) * width * 4
        for x in range(new_width):
            src = src_row + (x // factor) * 4
            dest = (y * new_width + x) * 4
            out[dest : dest + 4] = rgba[src : src + 4]
    return bytes(out)
