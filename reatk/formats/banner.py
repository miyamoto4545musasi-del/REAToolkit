"""The DS icon/title banner.

The banner is a fixed 0xA00-byte block: a 32x32 4bpp tiled icon, a 16-entry
BGR555 palette, and one 128-character UTF-16LE title per language.
"""

from __future__ import annotations

from .._util import ParseError, crc16_modbus, u16

LANGUAGES = ("japanese", "english", "french", "german", "italian", "spanish")


class Banner:
    SIZE = 0xA00
    ICON_OFFSET = 0x020
    ICON_SIZE = 0x200
    PALETTE_OFFSET = 0x220
    TITLE_OFFSET = 0x240
    TITLE_STRIDE = 0x100

    def __init__(self, data: bytes, offset: int):
        if offset + self.SIZE > len(data):
            raise ParseError(f"banner at 0x{offset:X} is truncated")
        self.offset = offset
        self.raw = data[offset : offset + self.SIZE]
        self.version = u16(self.raw, 0x00)
        self.stored_crc = u16(self.raw, 0x02)

    @property
    def crc_ok(self) -> bool:
        return crc16_modbus(self.raw[0x20:0x840]) == self.stored_crc

    def titles(self) -> dict[str, str]:
        """Title strings per language, blank languages dropped."""
        out: dict[str, str] = {}
        for index, language in enumerate(LANGUAGES):
            start = self.TITLE_OFFSET + index * self.TITLE_STRIDE
            text = self.raw[start : start + self.TITLE_STRIDE]
            value = text.decode("utf-16-le", "replace").split("\x00", 1)[0].strip()
            if value:
                out[language] = value
        return out

    def palette(self) -> list[tuple[int, int, int, int]]:
        """16 RGBA colours; entry 0 is the transparent key."""
        colours = []
        for index in range(16):
            value = u16(self.raw, self.PALETTE_OFFSET + index * 2)
            red = (value & 0x1F) * 255 // 31
            green = ((value >> 5) & 0x1F) * 255 // 31
            blue = ((value >> 10) & 0x1F) * 255 // 31
            colours.append((red, green, blue, 0 if index == 0 else 255))
        return colours

    def icon_rgba(self) -> bytes:
        """De-tile the icon into a 32x32 RGBA buffer, row-major."""
        palette = self.palette()
        pixels = bytearray(32 * 32 * 4)
        tiles = self.raw[self.ICON_OFFSET : self.ICON_OFFSET + self.ICON_SIZE]
        for tile in range(16):  # 4x4 grid of 8x8 tiles
            tile_x, tile_y = (tile % 4) * 8, (tile // 4) * 8
            base = tile * 32
            for i in range(32):  # two 4bpp pixels per byte
                byte = tiles[base + i]
                for half in range(2):
                    index = (byte >> (4 * half)) & 0x0F
                    px = tile_x + ((i * 2 + half) % 8)
                    py = tile_y + ((i * 2 + half) // 8)
                    dest = (py * 32 + px) * 4
                    pixels[dest : dest + 4] = bytes(palette[index])
        return bytes(pixels)
