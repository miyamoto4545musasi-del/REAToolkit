"""Nitro graphics resources: NCLR palettes and NCGR character data.

Both are Nitro containers.  Together they are enough to turn a title's
tilesets and sprite sheets into PNGs, which is usually the first thing you
want after dumping the filesystem.

Screen data (NSCR) and cell banks (NCER) describe how these tiles are
*arranged*; they are not handled here, so a sheet comes out as its raw tile
grid rather than as the composed screen.
"""

from __future__ import annotations

import dataclasses

from .._util import ParseError, u16, u32
from . import nitro

BPP_4 = 3
BPP_8 = 4
TILE = 8


def bgr555_to_rgba(value: int, transparent: bool = False) -> tuple[int, int, int, int]:
    red = (value & 0x1F) * 255 // 31
    green = ((value >> 5) & 0x1F) * 255 // 31
    blue = ((value >> 10) & 0x1F) * 255 // 31
    return red, green, blue, 0 if transparent else 255


@dataclasses.dataclass
class Palette:
    """One or more 16- or 256-colour palettes from an NCLR file."""

    colours: list[tuple[int, int, int, int]]
    bit_depth: int

    @property
    def per_palette(self) -> int:
        return 16 if self.bit_depth == BPP_4 else 256

    def slot(self, index: int) -> list[tuple[int, int, int, int]]:
        """Palette *index*, padded when the file holds fewer colours."""
        start = index * self.per_palette
        chunk = self.colours[start : start + self.per_palette]
        if not chunk:
            raise ParseError(f"palette {index} is not present in this file")
        return chunk + [(0, 0, 0, 255)] * (self.per_palette - len(chunk))


def read_nclr(data: bytes) -> Palette:
    container = nitro.read_header(data)
    if container.magic not in ("RLCN", "NCLR"):
        raise ParseError(f"expected an NCLR palette, got {container.magic!r}")
    section = container.section("PLTT", "TTLP")
    base = section.payload_offset
    bit_depth = u32(data, base)
    count = section.payload_size - 0x10
    start = base + 0x10
    colours = []
    for index in range(count // 2):
        value = u16(data, start + index * 2)
        colours.append(bgr555_to_rgba(value, transparent=index % 16 == 0))
    return Palette(colours, BPP_4 if bit_depth == BPP_4 else BPP_8)


@dataclasses.dataclass
class CharacterData:
    """Tile pixels from an NCGR file, as palette indices."""

    width_tiles: int
    height_tiles: int
    bit_depth: int
    tiles: bytes  # raw, still tiled

    @property
    def width(self) -> int:
        return self.width_tiles * TILE

    @property
    def height(self) -> int:
        return self.height_tiles * TILE

    def indices(self) -> bytes:
        """De-tile into a row-major buffer of one palette index per pixel."""
        bytes_per_tile = 32 if self.bit_depth == BPP_4 else 64
        out = bytearray(self.width * self.height)
        for tile_index in range(self.width_tiles * self.height_tiles):
            base = tile_index * bytes_per_tile
            if base + bytes_per_tile > len(self.tiles):
                break  # a sheet may declare more tiles than it stores
            origin_x = (tile_index % self.width_tiles) * TILE
            origin_y = (tile_index // self.width_tiles) * TILE
            for pixel in range(TILE * TILE):
                if self.bit_depth == BPP_4:
                    byte = self.tiles[base + pixel // 2]
                    value = (byte & 0x0F) if pixel % 2 == 0 else (byte >> 4)
                else:
                    value = self.tiles[base + pixel]
                x = origin_x + pixel % TILE
                y = origin_y + pixel // TILE
                out[y * self.width + x] = value
        return bytes(out)

    def to_rgba(self, palette: Palette, slot: int = 0) -> bytes:
        colours = palette.slot(slot)
        out = bytearray(self.width * self.height * 4)
        for position, index in enumerate(self.indices()):
            out[position * 4 : position * 4 + 4] = bytes(colours[index % len(colours)])
        return bytes(out)


def read_ncgr(data: bytes) -> CharacterData:
    container = nitro.read_header(data)
    if container.magic not in ("RGCN", "NCGR"):
        raise ParseError(f"expected an NCGR sheet, got {container.magic!r}")
    section = container.section("CHAR", "RAHC")
    base = section.payload_offset
    height_tiles = u16(data, base)
    width_tiles = u16(data, base + 2)
    bit_depth = u32(data, base + 4)
    data_size = u32(data, base + 0x10)
    tiles_offset = base + 0x18
    tiles = data[tiles_offset : tiles_offset + data_size]

    if width_tiles in (0, 0xFFFF) or height_tiles in (0, 0xFFFF):
        # Sheets that do not declare their shape are laid out 8 tiles wide,
        # which is what the official viewers assumed too.
        bytes_per_tile = 32 if bit_depth == BPP_4 else 64
        total = max(1, len(tiles) // bytes_per_tile)
        width_tiles = min(8, total)
        height_tiles = (total + width_tiles - 1) // width_tiles
    return CharacterData(width_tiles, height_tiles, bit_depth, tiles)


def render(ncgr: bytes, nclr: bytes, slot: int = 0) -> tuple[bytes, int, int]:
    """``(rgba, width, height)`` for a sheet/palette pair."""
    character = read_ncgr(ncgr)
    palette = read_nclr(nclr)
    return character.to_rgba(palette, slot), character.width, character.height


# -- encoding ------------------------------------------------------------


def rgba_to_bgr555(colour: tuple[int, int, int, int]) -> int:
    red, green, blue = colour[0] >> 3, colour[1] >> 3, colour[2] >> 3
    return red | green << 5 | blue << 10


def _nearest(colour: tuple[int, int, int, int], palette: list[tuple[int, int, int, int]]) -> int:
    """Index of the closest palette entry, squared distance in RGB."""
    if colour[3] == 0:
        return 0  # index 0 is the transparent slot by convention
    best_index, best_distance = 0, None
    for index, candidate in enumerate(palette):
        if index == 0:
            continue  # never map an opaque pixel onto the transparent entry
        distance = sum(
            (int(colour[channel]) - int(candidate[channel])) ** 2 for channel in range(3)
        )
        if best_distance is None or distance < best_distance:
            best_index, best_distance = index, distance
    return best_index


def _median_cut(
    counts: dict[tuple[int, int, int, int], int], slots: int
) -> list[tuple[int, int, int, int]]:
    """Reduce *counts* to at most *slots* colours by repeated splitting."""
    buckets = [list(counts)]
    while len(buckets) < slots:
        # Split the bucket with the widest spread along its widest channel.
        target, channel, spread = None, 0, -1
        for bucket in buckets:
            if len(bucket) < 2:
                continue
            for candidate_channel in range(3):
                values = [colour[candidate_channel] for colour in bucket]
                candidate_spread = max(values) - min(values)
                if candidate_spread > spread:
                    target, channel, spread = bucket, candidate_channel, candidate_spread
        if target is None or spread <= 0:
            break
        target.sort(key=lambda colour: colour[channel])
        middle = len(target) // 2
        buckets.remove(target)
        buckets += [target[:middle], target[middle:]]

    palette = []
    for bucket in buckets:
        weight = sum(counts[colour] for colour in bucket) or 1
        palette.append(
            tuple(
                sum(colour[channel] * counts[colour] for colour in bucket) // weight
                for channel in range(3)
            )
            + (255,)
        )
    return palette  # type: ignore[return-value]


def quantize(
    rgba: bytes,
    max_colours: int = 16,
    palette: list[tuple[int, int, int, int]] | None = None,
) -> tuple[bytes, list[tuple[int, int, int, int]]]:
    """Turn an RGBA buffer into palette indices.

    With *palette* given, every pixel is mapped to its nearest entry and the
    palette comes back unchanged - that is what you want when replacing a
    sheet inside a ROM, because the game may use that palette elsewhere.
    Without one, an exact palette is built when the image is already within
    *max_colours*, otherwise median cut reduces it.
    """
    pixels = [tuple(rgba[i : i + 4]) for i in range(0, len(rgba), 4)]
    if palette is None:
        counts: dict[tuple[int, int, int, int], int] = {}
        has_transparency = False
        for pixel in pixels:
            if pixel[3] == 0:
                has_transparency = True
                continue
            opaque = (pixel[0], pixel[1], pixel[2], 255)
            counts[opaque] = counts.get(opaque, 0) + 1

        reserved = 1 if has_transparency else 0
        slots = max_colours - reserved
        distinct = list(counts)
        chosen = distinct if len(distinct) <= slots else _median_cut(counts, slots)
        palette = ([(0, 0, 0, 0)] if reserved else []) + chosen
        palette += [(0, 0, 0, 255)] * (max_colours - len(palette))

    # Cache lookups: tiles repeat colours heavily, so this pays for itself.
    cache: dict[tuple[int, ...], int] = {}
    indices = bytearray(len(pixels))
    for position, pixel in enumerate(pixels):
        if pixel not in cache:
            cache[pixel] = _nearest(pixel, palette)  # type: ignore[arg-type]
        indices[position] = cache[pixel]
    return bytes(indices), palette


def encode_nclr(palette: list[tuple[int, int, int, int]], bit_depth: int = BPP_4) -> bytes:
    """Build an NCLR palette file."""
    import struct

    from . import nitro

    payload = struct.pack("<IIII", bit_depth, 0, len(palette) * 2, 16)
    payload += b"".join(struct.pack("<H", rgba_to_bgr555(colour)) for colour in palette)
    return nitro.write_header("RLCN", [nitro.write_section("TTLP", payload)])


def encode_ncgr(
    indices: bytes, width: int, height: int, bit_depth: int = BPP_4
) -> bytes:
    """Build an NCGR sheet from row-major palette indices.

    *width* and *height* are in pixels and must be multiples of 8, since the
    format stores 8x8 tiles.
    """
    import struct

    from . import nitro

    if width % TILE or height % TILE:
        raise ValueError(f"{width}x{height} is not a whole number of 8x8 tiles")
    if len(indices) != width * height:
        raise ValueError(f"expected {width * height} indices, got {len(indices)}")
    limit = 16 if bit_depth == BPP_4 else 256
    if any(index >= limit for index in indices):
        raise ValueError(f"index out of range for {limit}-colour data")

    width_tiles, height_tiles = width // TILE, height // TILE
    tiles = bytearray()
    for tile_index in range(width_tiles * height_tiles):
        origin_x = (tile_index % width_tiles) * TILE
        origin_y = (tile_index // width_tiles) * TILE
        for pixel in range(TILE * TILE):
            x = origin_x + pixel % TILE
            y = origin_y + pixel // TILE
            value = indices[y * width + x]
            if bit_depth == BPP_8:
                tiles.append(value)
            elif pixel % 2 == 0:
                tiles.append(value & 0x0F)
            else:
                tiles[-1] |= (value & 0x0F) << 4

    payload = struct.pack("<HH", height_tiles, width_tiles)
    payload += struct.pack("<I", bit_depth)
    payload += struct.pack("<II", 0, 0)
    payload += struct.pack("<I", len(tiles))
    payload += struct.pack("<I", 0x18)
    return nitro.write_header("RGCN", [nitro.write_section("RAHC", bytes(payload) + bytes(tiles))])


def encode_sheet(
    rgba: bytes,
    width: int,
    height: int,
    bit_depth: int = BPP_4,
    palette: Palette | None = None,
    slot: int = 0,
) -> tuple[bytes, bytes]:
    """``(ncgr, nclr)`` for an image, reusing *palette* when one is given."""
    existing = palette.slot(slot) if palette is not None else None
    max_colours = 16 if bit_depth == BPP_4 else 256
    indices, colours = quantize(rgba, max_colours, existing)
    return (
        encode_ncgr(indices, width, height, bit_depth),
        encode_nclr(colours, bit_depth),
    )
