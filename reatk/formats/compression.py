"""Nintendo BIOS compression formats.

DS titles keep most of their data compressed with the four routines the BIOS
exposes, so a NitroFS dump is largely unreadable until these are undone.  Each
stream starts with a 32-bit header: the low byte identifies the format, the
upper three bytes hold the decompressed size.

    0x10  LZ77   (12-bit displacement, 4-bit length)
    0x11  LZ11   (extended lengths, used mostly on DSi titles)
    0x24  Huffman, 4-bit symbols
    0x28  Huffman, 8-bit symbols
    0x30  run-length

The ARM9 binary itself often uses BLZ ("backwards LZ"), which is a different
format applied by the linker rather than the BIOS; it is not handled here.
"""

from __future__ import annotations

from .._util import ParseError

LZ77 = 0x10
LZ11 = 0x11
HUFFMAN_4 = 0x24
HUFFMAN_8 = 0x28
RLE = 0x30

FORMAT_NAMES = {
    LZ77: "lz77",
    LZ11: "lz11",
    HUFFMAN_4: "huffman4",
    HUFFMAN_8: "huffman8",
    RLE: "rle",
}


def header(data: bytes) -> tuple[int, int]:
    """Return ``(format_byte, decompressed_size)`` from a stream header."""
    if len(data) < 4:
        raise ParseError("stream is too short to hold a compression header")
    return data[0], int.from_bytes(data[1:4], "little")


def detect(data: bytes) -> str | None:
    """Name the compression format, or ``None`` when the stream is not one.

    The check is deliberately strict: a header byte alone matches far too much
    ordinary data, so the declared size has to be plausible as well.
    """
    if len(data) < 8:
        return None
    kind, size = header(data)
    if kind not in FORMAT_NAMES:
        return None
    if size == 0 or size > 0x400000 or size < len(data) // 32:
        return None
    return FORMAT_NAMES[kind]


# -- decoders ------------------------------------------------------------


def _decompress_lz77(data: bytes, size: int) -> bytes:
    out = bytearray()
    pos = 4
    while len(out) < size:
        if pos >= len(data):
            raise ParseError("LZ77 stream ended early")
        flags = data[pos]
        pos += 1
        for bit in range(7, -1, -1):
            if len(out) >= size:
                break
            if not (flags >> bit) & 1:
                out.append(data[pos])
                pos += 1
                continue
            if pos + 1 >= len(data):
                raise ParseError("LZ77 back-reference is truncated")
            length = (data[pos] >> 4) + 3
            displacement = ((data[pos] & 0x0F) << 8 | data[pos + 1]) + 1
            pos += 2
            if displacement > len(out):
                raise ParseError("LZ77 back-reference points before the output")
            for _ in range(length):
                out.append(out[-displacement])
    return bytes(out[:size])


def _decompress_lz11(data: bytes, size: int) -> bytes:
    out = bytearray()
    pos = 4
    while len(out) < size:
        if pos >= len(data):
            raise ParseError("LZ11 stream ended early")
        flags = data[pos]
        pos += 1
        for bit in range(7, -1, -1):
            if len(out) >= size:
                break
            if not (flags >> bit) & 1:
                out.append(data[pos])
                pos += 1
                continue
            indicator = data[pos] >> 4
            if indicator == 0:  # 8-bit length, offset in the next 1.5 bytes
                length = ((data[pos] & 0x0F) << 4 | data[pos + 1] >> 4) + 0x11
                displacement = ((data[pos + 1] & 0x0F) << 8 | data[pos + 2]) + 1
                pos += 3
            elif indicator == 1:  # 16-bit length
                length = (
                    (data[pos] & 0x0F) << 12 | data[pos + 1] << 4 | data[pos + 2] >> 4
                ) + 0x111
                displacement = ((data[pos + 2] & 0x0F) << 8 | data[pos + 3]) + 1
                pos += 4
            else:
                length = indicator + 1
                displacement = ((data[pos] & 0x0F) << 8 | data[pos + 1]) + 1
                pos += 2
            if displacement > len(out):
                raise ParseError("LZ11 back-reference points before the output")
            for _ in range(length):
                out.append(out[-displacement])
    return bytes(out[:size])


def _decompress_rle(data: bytes, size: int) -> bytes:
    out = bytearray()
    pos = 4
    while len(out) < size:
        if pos >= len(data):
            raise ParseError("RLE stream ended early")
        flag = data[pos]
        pos += 1
        if flag & 0x80:
            run = (flag & 0x7F) + 3
            out += bytes([data[pos]]) * run
            pos += 1
        else:
            run = (flag & 0x7F) + 1
            out += data[pos : pos + run]
            pos += run
    return bytes(out[:size])


def _decompress_huffman(data: bytes, size: int, symbol_bits: int) -> bytes:
    tree_size = (data[4] + 1) * 2
    tree = data[4 : 4 + tree_size]
    pos = 4 + tree_size
    if pos % 4:  # the bitstream is word aligned
        pos += 4 - (pos % 4)

    out = bytearray()
    nibble_low: int | None = None
    node = 1  # root sits right after the tree-size byte
    node_is_leaf = False

    while len(out) < size:
        if pos + 4 > len(data):
            raise ParseError("Huffman bitstream ended early")
        word = int.from_bytes(data[pos : pos + 4], "little")
        pos += 4
        for bit in range(31, -1, -1):
            if len(out) >= size:
                break
            direction = (word >> bit) & 1
            if node >= len(tree):
                raise ParseError("Huffman tree traversal left the table")
            current = tree[node]
            offset = ((node & ~1) + (current & 0x3F) * 2 + 2) + direction
            node_is_leaf = bool(current & (0x80 if direction else 0x40))
            node = offset
            if not node_is_leaf:
                continue
            if node >= len(tree):
                raise ParseError("Huffman leaf is outside the table")
            value = tree[node]
            if symbol_bits == 8:
                out.append(value)
            else:
                value &= 0x0F
                if nibble_low is None:
                    nibble_low = value
                else:
                    out.append(nibble_low | value << 4)
                    nibble_low = None
            node = 1
    return bytes(out[:size])


_DECODERS = {
    LZ77: lambda data, size: _decompress_lz77(data, size),
    LZ11: lambda data, size: _decompress_lz11(data, size),
    RLE: lambda data, size: _decompress_rle(data, size),
    HUFFMAN_4: lambda data, size: _decompress_huffman(data, size, 4),
    HUFFMAN_8: lambda data, size: _decompress_huffman(data, size, 8),
}


def decompress(data: bytes) -> bytes:
    """Decompress a BIOS-compressed stream."""
    kind, size = header(data)
    try:
        decoder = _DECODERS[kind]
    except KeyError:
        raise ParseError(f"unsupported compression type 0x{kind:02X}") from None
    try:
        return decoder(data, size)
    except IndexError:
        raise ParseError(f"{FORMAT_NAMES[kind]} stream is truncated") from None


def maybe_decompress(data: bytes) -> tuple[bytes, str | None]:
    """Decompress when the stream looks compressed, else return it unchanged.

    Returns ``(payload, format_name_or_None)``.  A stream that advertises a
    known format but fails to decode is returned as-is: a false positive on
    ordinary data must not lose that data.
    """
    name = detect(data)
    if name is None:
        return data, None
    try:
        return decompress(data), name
    except ParseError:
        return data, None


# -- encoder -------------------------------------------------------------


def compress_lz77(data: bytes, min_match: int = 3) -> bytes:
    """Compress to LZ77 (type 0x10), the format every DS title can read.

    A greedy longest-match search, which is what the official tools did too.
    Useful for putting a modified asset back into an image that expects the
    file to be compressed.
    """
    if len(data) > 0xFFFFFF:
        raise ValueError("LZ77 headers only carry a 24-bit size")
    out = bytearray(bytes([LZ77]) + len(data).to_bytes(3, "little"))
    pos = 0
    while pos < len(data):
        flags_index = len(out)
        out.append(0)
        flags = 0
        for bit in range(7, -1, -1):
            if pos >= len(data):
                break
            window_start = max(0, pos - 0x1000)
            best_length, best_distance = 0, 0
            # Longest match wins; ties keep the nearest one, which codes smaller.
            for candidate in range(pos - 1, window_start - 1, -1):
                length = 0
                while (
                    length < 18
                    and pos + length < len(data)
                    and data[candidate + length] == data[pos + length]
                ):
                    length += 1
                if length > best_length:
                    best_length, best_distance = length, pos - candidate
                    if length == 18:
                        break
            if best_length >= min_match:
                flags |= 1 << bit
                out.append((best_length - 3) << 4 | (best_distance - 1) >> 8)
                out.append((best_distance - 1) & 0xFF)
                pos += best_length
            else:
                out.append(data[pos])
                pos += 1
        out[flags_index] = flags
    return bytes(out)
