"""Build a synthetic DS image in memory.

Nothing copyrighted is needed to exercise the parsers: the header layout is
documented, so a fixture can be assembled from scratch.
"""

from __future__ import annotations

import struct

from reatk._util import crc16_modbus

HEADER_SIZE = 0x200
ARM9_OFF, ARM9_SIZE = 0x4000, 0x200
ARM7_OFF, ARM7_SIZE = 0x4200, 0x100
FNT_OFF = 0x4400
FAT_OFF = 0x4600
BANNER_OFF = 0x5000
FILES_OFF = 0x6000

README = b"synthetic homebrew fixture\n" * 4
BLOB = bytes(range(256)) * 2


def _banner() -> bytes:
    banner = bytearray(0xA00)
    struct.pack_into("<H", banner, 0x00, 1)  # version
    # A diagonal line across the icon, enough to prove de-tiling works.
    for tile in range(16):
        for i in range(32):
            banner[0x20 + tile * 32 + i] = 0x21 if i % 9 == 0 else 0x00
    for index in range(16):
        struct.pack_into("<H", banner, 0x220 + index * 2, index * 0x0421)
    for index, title in enumerate(["テスト\nFixture", "Fixture Title\nREAToolkit"]):
        encoded = title.encode("utf-16-le")[:0xFE]
        banner[0x240 + index * 0x100 : 0x240 + index * 0x100 + len(encoded)] = encoded
    struct.pack_into("<H", banner, 0x02, crc16_modbus(bytes(banner[0x20:0x840])))
    return bytes(banner)


def _filesystem_tables() -> tuple[bytes, bytes, bytes]:
    """Return (fnt, fat, file_data) for /readme.txt and /data/a.bin."""
    records = bytearray()
    root_sub = 2 * 8  # both directory records come first
    data_sub = root_sub + (1 + 10) + (1 + 4 + 2) + 1
    records += struct.pack("<IHH", root_sub, 0, 2)          # root: 2 directories
    records += struct.pack("<IHH", data_sub, 1, 0xF000)     # /data, parent root

    names = bytearray()
    names += bytes([10]) + b"readme.txt"
    names += bytes([0x80 | 4]) + b"data" + struct.pack("<H", 0xF001)
    names += b"\x00"
    names += bytes([5]) + b"a.bin"
    names += b"\x00"

    fat = struct.pack("<II", FILES_OFF, FILES_OFF + len(README))
    second = FILES_OFF + len(README)
    fat += struct.pack("<II", second, second + len(BLOB))
    return bytes(records + names), fat, README + BLOB


def make_nds(game_code: str = "ATST", title: str = "FIXTURE") -> bytes:
    fnt, fat, files = _filesystem_tables()
    banner = _banner()
    total = FILES_OFF + len(files)

    image = bytearray(total)
    header = bytearray(HEADER_SIZE)
    header[0x00:0x0C] = title.encode("ascii")[:12].ljust(12, b"\x00")
    header[0x0C:0x10] = game_code.encode("ascii")[:4].ljust(4, b"\x00")
    header[0x10:0x12] = b"01"
    header[0x12] = 0x00  # NDS
    header[0x14] = 0x09  # 0x20000 << 9 == 64 MiB cartridge
    header[0x1E] = 1
    struct.pack_into("<IIII", header, 0x20, ARM9_OFF, 0x2000000, 0x2000000, ARM9_SIZE)
    struct.pack_into("<IIII", header, 0x30, ARM7_OFF, 0x2380000, 0x2380000, ARM7_SIZE)
    struct.pack_into("<II", header, 0x40, FNT_OFF, len(fnt))
    struct.pack_into("<II", header, 0x48, FAT_OFF, len(fat))
    struct.pack_into("<I", header, 0x68, BANNER_OFF)
    struct.pack_into("<I", header, 0x80, total)
    struct.pack_into("<I", header, 0x84, HEADER_SIZE)
    struct.pack_into("<H", header, 0x15E, crc16_modbus(bytes(header[:0x15E])))

    image[0:HEADER_SIZE] = header
    image[ARM9_OFF : ARM9_OFF + ARM9_SIZE] = (b"NITRO-SDK arm9 code\x00" * 32)[:ARM9_SIZE]
    image[ARM7_OFF : ARM7_OFF + ARM7_SIZE] = (b"arm7 code\x00" * 32)[:ARM7_SIZE]
    image[FNT_OFF : FNT_OFF + len(fnt)] = fnt
    image[FAT_OFF : FAT_OFF + len(fat)] = fat
    image[BANNER_OFF : BANNER_OFF + len(banner)] = banner
    image[FILES_OFF : FILES_OFF + len(files)] = files
    return bytes(image)
