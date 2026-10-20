"""Game Boy Advance cartridge header.

Included so a toolkit run over a mixed dump directory does not stop at the
first .gba file; the packaging stage treats it as another libretro target.
"""

from __future__ import annotations

from pathlib import Path

from .._util import ParseError, ascii_field

NINTENDO_LOGO_CRC_RANGE = (0x04, 0xA0)
HEADER_SIZE = 0xC0


class GbaRom:
    def __init__(self, data: bytes, source: Path | None = None):
        if len(data) < HEADER_SIZE:
            raise ParseError("image is shorter than a GBA cartridge header")
        self.data = data
        self.source = source
        self.title = ascii_field(data, 0xA0, 12)
        self.game_code = ascii_field(data, 0xAC, 4)
        self.maker_code = ascii_field(data, 0xB0, 2)
        self.unit_code = data[0xB3]
        self.software_version = data[0xBC]
        self.stored_checksum = data[0xBD]

    @classmethod
    def from_file(cls, path: str | Path) -> "GbaRom":
        path = Path(path)
        return cls(path.read_bytes(), source=path)

    @staticmethod
    def looks_like(head: bytes) -> bool:
        # 0x96 at 0xB2 is the fixed value every licensed cartridge carries.
        return len(head) >= 0xC0 and head[0xB2] == 0x96

    @property
    def platform(self) -> str:
        return "GBA"

    @property
    def header_checksum_ok(self) -> bool:
        total = -0x19
        for byte in self.data[0xA0:0xBD]:
            total -= byte
        return (total & 0xFF) == self.stored_checksum
