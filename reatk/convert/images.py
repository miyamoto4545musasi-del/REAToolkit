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


class PngError(ValueError):
    """Raised for a PNG this reader cannot handle."""


def _unfilter(raw: bytes, width: int, height: int, channels: int) -> bytearray:
    """Reverse the per-scanline filters (PNG spec, section 9)."""
    stride = width * channels
    out = bytearray(stride * height)
    previous = bytearray(stride)
    pos = 0
    for y in range(height):
        filter_type = raw[pos]
        pos += 1
        line = bytearray(raw[pos : pos + stride])
        pos += stride
        if filter_type == 1:  # Sub
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filter_type == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filter_type == 3:  # Average
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:  # Paeth
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                up = previous[i]
                up_left = previous[i - channels] if i >= channels else 0
                estimate = left + up - up_left
                pa, pb, pc = (
                    abs(estimate - left),
                    abs(estimate - up),
                    abs(estimate - up_left),
                )
                nearest = left if pa <= pb and pa <= pc else (up if pb <= pc else up_left)
                line[i] = (line[i] + nearest) & 0xFF
        elif filter_type != 0:
            raise PngError(f"unknown scanline filter {filter_type}")
        out[y * stride : (y + 1) * stride] = line
        previous = line
    return out


def read_png(path: str | Path) -> tuple[bytes, int, int]:
    """Read a PNG into ``(rgba, width, height)``.

    Supports the 8-bit colour types, which is everything an image editor
    produces by default.  16-bit samples and interlacing are rejected rather
    than silently mangled.
    """
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise PngError(f"{path}: not a PNG file")

    pos = 8
    header: tuple[int, int, int, int] | None = None
    palette: list[tuple[int, int, int]] = []
    alpha: list[int] = []
    idat = bytearray()
    while pos + 8 <= len(data):
        length = struct.unpack_from(">I", data, pos)[0]
        tag = data[pos + 4 : pos + 8]
        payload = data[pos + 8 : pos + 8 + length]
        pos += 12 + length  # 4 length + 4 tag + payload + 4 CRC
        if tag == b"IHDR":
            width, height, depth, colour_type, _, _, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            if depth != 8:
                raise PngError(f"{path}: only 8-bit samples are supported (got {depth})")
            if interlace:
                raise PngError(f"{path}: interlaced PNGs are not supported")
            header = (width, height, depth, colour_type)
        elif tag == b"PLTE":
            palette = [
                tuple(payload[i : i + 3]) for i in range(0, len(payload), 3)
            ]  # type: ignore[misc]
        elif tag == b"tRNS":
            alpha = list(payload)
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            break

    if header is None:
        raise PngError(f"{path}: no IHDR chunk")
    width, height, _, colour_type = header
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(colour_type)
    if channels is None:
        raise PngError(f"{path}: unsupported colour type {colour_type}")

    pixels = _unfilter(zlib.decompress(bytes(idat)), width, height, channels)

    rgba = bytearray(width * height * 4)
    for index in range(width * height):
        source = index * channels
        if colour_type == 6:
            rgba[index * 4 : index * 4 + 4] = pixels[source : source + 4]
            continue
        if colour_type == 2:
            red, green, blue = pixels[source : source + 3]
            opacity = 255
        elif colour_type == 0:
            red = green = blue = pixels[source]
            opacity = 255
        elif colour_type == 4:
            grey = pixels[source]
            red = green = blue = grey
            opacity = pixels[source + 1]
        else:  # indexed
            entry = pixels[source]
            if entry >= len(palette):
                raise PngError(f"{path}: palette index {entry} is out of range")
            red, green, blue = palette[entry]
            opacity = alpha[entry] if entry < len(alpha) else 255
        rgba[index * 4 : index * 4 + 4] = bytes((red, green, blue, opacity))
    return bytes(rgba), width, height
