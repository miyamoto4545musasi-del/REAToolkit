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
