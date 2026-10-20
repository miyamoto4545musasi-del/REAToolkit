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


def _filesystem_tables(data_files: dict[str, bytes]) -> tuple[bytes, bytes, bytes]:
    """Tables for /readme.txt plus *data_files* inside /data."""
    root_sub = 2 * 8  # both directory records come first
    root_names = bytearray()
    root_names += bytes([10]) + b"readme.txt"
    root_names += bytes([0x80 | 4]) + b"data" + struct.pack("<H", 0xF001)
    root_names += b"\x00"
    data_sub = root_sub + len(root_names)

    data_names = bytearray()
    for name in data_files:
        encoded = name.encode("shift_jis")
        data_names += bytes([len(encoded)]) + encoded
    data_names += b"\x00"

    records = struct.pack("<IHH", root_sub, 0, 2)        # root: 2 directories
    records += struct.pack("<IHH", data_sub, 1, 0xF000)  # /data, parent root

    fat = bytearray()
    payload = bytearray()
    for blob in [README] + list(data_files.values()):
        start = FILES_OFF + len(payload)
        fat += struct.pack("<II", start, start + len(blob))
        payload += blob
        payload += b"\x00" * (-len(payload) % 4)
    return bytes(records + root_names + data_names), bytes(fat), bytes(payload)


def make_nds(
    game_code: str = "ATST",
    title: str = "FIXTURE",
    data_files: dict[str, bytes] | None = None,
) -> bytes:
    """A minimal image: /readme.txt and /data/a.bin, plus any *data_files*."""
    files_in_data = {"a.bin": BLOB}
    files_in_data.update(data_files or {})
    fnt, fat, files = _filesystem_tables(files_in_data)
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


# -- resource fixtures ---------------------------------------------------


def flat_fnt(names: list[str]) -> bytes:
    """A name table holding one root directory with *names* in it."""
    records = struct.pack("<IHH", 8, 0, 1)  # root record, one directory
    table = bytearray()
    for name in names:
        encoded = name.encode("shift_jis")
        table += bytes([len(encoded)]) + encoded
    table += b"\x00"
    return records + bytes(table)


def make_narc(files: dict[str, bytes], named: bool = True) -> bytes:
    """Build a NARC archive from ``{name: content}``."""
    from reatk.formats.nitro import write_header, write_section

    names = list(files)
    blobs = [files[name] for name in names]

    fat_payload = struct.pack("<HH", len(blobs), 0)
    cursor = 0
    for blob in blobs:
        fat_payload += struct.pack("<II", cursor, cursor + len(blob))
        cursor += len(blob)
        cursor += -cursor % 4  # NARC pads every file to a word boundary

    image = bytearray()
    for blob in blobs:
        image += blob
        image += b"\x00" * (-len(image) % 4)

    if named:
        fnt_payload = flat_fnt(names)
    else:
        fnt_payload = struct.pack("<IHH", 4, 0, 1)  # the "no names" table

    return write_header(
        "NARC",
        [
            write_section("BTAF", fat_payload),
            write_section("BTNF", fnt_payload),
            write_section("GMIF", bytes(image)),
        ],
    )


def make_nclr(colours: list[int] | None = None) -> bytes:
    """A 16-colour NCLR palette; *colours* are raw BGR555 values."""
    from reatk.formats.nitro import write_header, write_section

    values = colours or [index * 0x0421 for index in range(16)]
    payload = struct.pack("<IIII", 3, 0, len(values) * 2, 16)
    payload += b"\x00" * 4  # data offset field, unused by readers
    payload += b"".join(struct.pack("<H", value) for value in values)
    return write_header("RLCN", [write_section("TTLP", payload)])


def make_ncgr(width_tiles: int = 2, height_tiles: int = 1) -> bytes:
    """A 4bpp NCGR sheet whose pixels encode their own index."""
    from reatk.formats.nitro import write_header, write_section

    tiles = bytearray()
    for tile in range(width_tiles * height_tiles):
        for pixel in range(32):
            low = (tile + pixel) % 16
            high = (tile + pixel + 1) % 16
            tiles.append(low | high << 4)
    payload = struct.pack("<HH", height_tiles, width_tiles)
    payload += struct.pack("<I", 3)              # 4bpp
    payload += struct.pack("<II", 0, 0)          # padding
    payload += struct.pack("<I", len(tiles))     # data size
    payload += struct.pack("<I", 0x18)           # data offset
    payload += bytes(tiles)
    return write_header("RGCN", [write_section("RAHC", payload)])


def make_sdat(sequences=(("BGM_TITLE", b"SSEQ body"),), waves=(("WAVE_A", b"SWAR body"),)) -> bytes:
    """A small SDAT with named sequences and wave archives."""
    files = [blob for _, blob in sequences] + [blob for _, blob in waves]

    def _record_block(magic: str, tables: dict[int, bytes]) -> bytes:
        """Assemble a block whose 8 record pointers sit at 0x08."""
        header_len = 0x40
        body = bytearray()
        pointers = [0] * 8
        for type_index, table in tables.items():
            pointers[type_index] = header_len + len(body)
            body += table
        size = header_len + len(body)
        block = bytearray(header_len)
        block[0:4] = magic.encode("ascii")
        struct.pack_into("<I", block, 4, size)
        for index, pointer in enumerate(pointers):
            struct.pack_into("<I", block, 8 + index * 4, pointer)
        return bytes(block) + bytes(body)

    # SYMB holds name strings; its tables point at them, relative to the block.
    names = [name for name, _ in sequences] + [name for name, _ in waves]
    strings = bytearray()
    string_offsets = []
    for name in names:
        string_offsets.append(len(strings))
        strings += name.encode("ascii") + b"\x00"

    symb_header = 0x40
    seq_table_len = 4 + 4 * len(sequences)
    wav_table_len = 4 + 4 * len(waves)
    strings_at = symb_header + seq_table_len + wav_table_len

    seq_names = struct.pack("<I", len(sequences)) + b"".join(
        struct.pack("<I", strings_at + string_offsets[i]) for i in range(len(sequences))
    )
    wav_names = struct.pack("<I", len(waves)) + b"".join(
        struct.pack("<I", strings_at + string_offsets[len(sequences) + i])
        for i in range(len(waves))
    )
    symb = _record_block("SYMB", {0: seq_names, 3: wav_names}) + bytes(strings)
    symb += b"\x00" * (-len(symb) % 4)

    # INFO records: a file id plus padding, pointed at the same way.
    def _info_table(file_ids: list[int], base: int) -> tuple[bytes, bytes]:
        records = b"".join(struct.pack("<HHII", fid, 0, 0, 0) for fid in file_ids)
        table = struct.pack("<I", len(file_ids)) + b"".join(
            struct.pack("<I", base + index * 12) for index in range(len(file_ids))
        )
        return table, records

    info_header = 0x40
    seq_ids = list(range(len(sequences)))
    wav_ids = [len(sequences) + index for index in range(len(waves))]
    seq_table_size = 4 + 4 * len(seq_ids)
    wav_table_size = 4 + 4 * len(wav_ids)
    records_at = info_header + seq_table_size + wav_table_size
    seq_table, seq_records = _info_table(seq_ids, records_at)
    wav_table, wav_records = _info_table(wav_ids, records_at + len(seq_records))
    info = _record_block("INFO", {0: seq_table, 3: wav_table}) + seq_records + wav_records
    info += b"\x00" * (-len(info) % 4)

    # FAT offsets are absolute, so the layout has to be known up front.
    fat_size = 12 + 16 * len(files)
    file_block_header = 0x10
    symb_at = 0x40
    info_at = symb_at + len(symb)
    fat_at = info_at + len(info)
    file_at = fat_at + fat_size

    fat = bytearray(b"FAT " + struct.pack("<II", fat_size, len(files)))
    payload = bytearray()
    for blob in files:
        start = file_at + file_block_header + len(payload)
        fat += struct.pack("<II", start, len(blob)) + b"\x00" * 8
        payload += blob
        payload += b"\x00" * (-len(payload) % 4)

    file_block = bytearray(b"FILE" + struct.pack("<II", file_block_header + len(payload), len(files)))
    file_block += b"\x00" * (file_block_header - len(file_block))
    file_block += payload

    total = file_at + len(file_block)
    head = bytearray(0x40)
    head[0:4] = b"SDAT"
    struct.pack_into("<HH", head, 4, 0xFEFF, 0x0100)
    struct.pack_into("<I", head, 8, total)
    struct.pack_into("<HH", head, 12, 0x40, 4)
    struct.pack_into("<II", head, 0x10, symb_at, len(symb))
    struct.pack_into("<II", head, 0x18, info_at, len(info))
    struct.pack_into("<II", head, 0x20, fat_at, fat_size)
    struct.pack_into("<II", head, 0x28, file_at, len(file_block))
    return bytes(head) + bytes(symb) + bytes(info) + bytes(fat) + bytes(file_block)


def make_rich_nds() -> bytes:
    """An image whose /data holds one of everything the toolkit can open.

    Used to exercise the extraction pipeline end to end: a compressed file, a
    nested archive, a sound archive and a graphics pair.
    """
    from reatk.formats.compression import compress_lz77

    return make_nds(
        data_files={
            "a.bin": BLOB,
            "text.bin": compress_lz77(b"compressed payload " * 16),
            "pack.narc": make_narc({"inner.txt": b"inside the archive", "blob.bin": BLOB}),
            "sound.sdat": make_sdat(),
            "gfx.nclr": make_nclr(),
            "gfx.ncgr": make_ncgr(width_tiles=4, height_tiles=2),
        }
    )
