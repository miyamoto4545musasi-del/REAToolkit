"""Small helpers shared by the parsers."""

from __future__ import annotations

import struct


class ParseError(ValueError):
    """Raised when a ROM image does not match the format it claims to be."""


def u8(buf: bytes, off: int) -> int:
    return buf[off]


def u16(buf: bytes, off: int) -> int:
    return struct.unpack_from("<H", buf, off)[0]


def u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def crc16_modbus(data: bytes, seed: int = 0xFFFF) -> int:
    """CRC-16/MODBUS, the variant Nintendo used for DS header and banner sums."""
    crc = seed
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def ascii_field(buf: bytes, off: int, length: int) -> str:
    raw = buf[off : off + length]
    return raw.split(b"\x00", 1)[0].decode("ascii", "replace").strip()


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024.0 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{n} B"
