"""Nintendo DS / DSi (.nds, .srl) container parser.

Layout follows the cartridge header documented by GBATEK.  Only the fields the
toolkit actually needs are promoted to attributes; the raw header stays
available on :attr:`NdsRom.header_bytes` for plugins that want more.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from .._util import ParseError, ascii_field, crc16_modbus, u16, u32
from .banner import Banner
from .nitrofs import NitroFs

HEADER_SIZE = 0x200

# Known maker codes, enough to label the common cases in reports.
MAKER_CODES = {
    "01": "Nintendo",
    "08": "Capcom",
    "13": "Electronic Arts",
    "18": "Hudson Soft",
    "41": "Ubisoft",
    "4F": "Eidos",
    "52": "Activision",
    "5D": "Midway",
    "69": "Electronic Arts",
    "70": "Atari",
    "99": "Pack-In-Video",
    "A4": "Konami",
    "AF": "Namco",
    "B2": "Bandai",
    "EB": "Square Enix",
}

UNIT_CODES = {0x00: "NDS", 0x02: "NDS+DSi", 0x03: "DSi"}


@dataclasses.dataclass(frozen=True)
class Region:
    """A named byte range inside the image."""

    name: str
    offset: int
    size: int

    @property
    def end(self) -> int:
        return self.offset + self.size


class NdsRom:
    """A parsed DS cartridge image."""

    def __init__(self, data: bytes, source: Path | None = None):
        if len(data) < HEADER_SIZE:
            raise ParseError("image is shorter than a DS cartridge header")
        self.data = data
        self.source = source
        h = data[:HEADER_SIZE]
        self.header_bytes = h

        self.title = ascii_field(h, 0x00, 12)
        self.game_code = ascii_field(h, 0x0C, 4)
        self.maker_code = ascii_field(h, 0x10, 2)
        self.unit_code = h[0x12]
        self.encryption_seed = h[0x13]
        self.device_capacity = h[0x14]
        self.rom_version = h[0x1E]
        self.autostart = h[0x1F]

        self.arm9 = Region("arm9", u32(h, 0x20), u32(h, 0x2C))
        self.arm9_entry = u32(h, 0x24)
        self.arm9_ram_address = u32(h, 0x28)
        self.arm7 = Region("arm7", u32(h, 0x30), u32(h, 0x3C))
        self.arm7_entry = u32(h, 0x34)
        self.arm7_ram_address = u32(h, 0x38)

        self.fnt = Region("fnt", u32(h, 0x40), u32(h, 0x44))
        self.fat = Region("fat", u32(h, 0x48), u32(h, 0x4C))
        self.arm9_overlay = Region("arm9_overlay", u32(h, 0x50), u32(h, 0x54))
        self.arm7_overlay = Region("arm7_overlay", u32(h, 0x58), u32(h, 0x5C))

        self.banner_offset = u32(h, 0x68)
        self.secure_area_crc = u16(h, 0x6C)
        self.total_used_size = u32(h, 0x80)
        self.header_size = u32(h, 0x84)
        self.stored_header_crc = u16(h, 0x15E)

    # -- construction ----------------------------------------------------

    @classmethod
    def from_file(cls, path: str | Path) -> "NdsRom":
        path = Path(path)
        return cls(path.read_bytes(), source=path)

    @staticmethod
    def looks_like(head: bytes) -> bool:
        """Heuristic sniff on the first 0xC0 bytes.

        A DS image is identified by the Nintendo logo CRC at 0x15C, but that
        field sits past 0xC0, so fall back on structural checks: the ARM9/ARM7
        offsets must be sane and the game code printable.
        """
        if len(head) < 0x70:
            return False
        arm9_off, arm7_off = u32(head, 0x20), u32(head, 0x30)
        if not (0x200 <= arm9_off < 0x8000000) or not (0x200 <= arm7_off < 0x8000000):
            return False
        code = head[0x0C:0x10]
        return all(32 <= b < 127 or b == 0 for b in code)

    # -- derived data ----------------------------------------------------

    @property
    def capacity_bytes(self) -> int:
        """Physical cartridge capacity encoded in the header (not image size)."""
        return 0x20000 << self.device_capacity

    @property
    def maker(self) -> str:
        return MAKER_CODES.get(self.maker_code.upper(), "unknown")

    @property
    def platform(self) -> str:
        return UNIT_CODES.get(self.unit_code, f"0x{self.unit_code:02X}")

    @property
    def region_letter(self) -> str:
        return self.game_code[3] if len(self.game_code) == 4 else "?"

    @property
    def header_crc_ok(self) -> bool:
        return crc16_modbus(self.header_bytes[:0x15E]) == self.stored_header_crc

    def regions(self) -> list[Region]:
        """Every populated top-level region, in image order."""
        candidates = [
            Region("header", 0, self.header_size or HEADER_SIZE),
            self.arm9,
            self.arm7,
            self.arm9_overlay,
            self.arm7_overlay,
            self.fnt,
            self.fat,
        ]
        if self.banner_offset:
            candidates.append(Region("banner", self.banner_offset, Banner.SIZE))
        return sorted((r for r in candidates if r.size), key=lambda r: r.offset)

    def read_region(self, region: Region) -> bytes:
        if region.end > len(self.data):
            raise ParseError(
                f"region {region.name} ends at 0x{region.end:X}, past the "
                f"{len(self.data)}-byte image"
            )
        return self.data[region.offset : region.end]

    def banner(self) -> Banner | None:
        """The icon/title banner, or ``None`` when the title carries none."""
        if not self.banner_offset:
            return None
        return Banner(self.data, self.banner_offset)

    def try_banner(self) -> Banner | None:
        """Like :meth:`banner`, but ``None`` instead of raising on a bad image.

        Callers that are reporting on a possibly-damaged dump want the rest of
        the findings rather than an exception.
        """
        try:
            return self.banner()
        except ParseError:
            return None

    def filesystem(self) -> NitroFs:
        """The NitroFS tree built from the file name table and allocation table."""
        return NitroFs(self.data, self.fnt, self.fat)

    def slack_ranges(self) -> list[Region]:
        """Byte ranges not claimed by any known region.

        Padding usually, but leftover development data shows up here too, which
        makes it the first place worth looking at during an audit.
        """
        gaps: list[Region] = []
        cursor = 0
        for region in self.regions():
            if region.offset > cursor:
                gaps.append(Region("slack", cursor, region.offset - cursor))
            cursor = max(cursor, region.end)
        if cursor < len(self.data):
            gaps.append(Region("slack", cursor, len(self.data) - cursor))
        return gaps

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<NdsRom {self.game_code} {self.title!r} {len(self.data)} bytes>"
