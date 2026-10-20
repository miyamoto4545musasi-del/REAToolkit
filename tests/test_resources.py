import json
import tempfile
import unittest
from pathlib import Path

from reatk._util import ParseError
from reatk.convert import ExtractOptions, extract_filesystem
from reatk.formats import graphics, nitro
from reatk.formats.compression import (
    compress_lz77,
    decompress,
    detect,
    header,
    maybe_decompress,
)
from reatk.formats.narc import Narc
from reatk.formats.nds import NdsRom
from reatk.formats.sdat import Sdat
from tests.fixtures import make_narc, make_nclr, make_ncgr, make_rich_nds, make_sdat


class TestCompression(unittest.TestCase):
    def test_lz77_round_trip(self):
        for payload in (
            b"",
            b"A",
            b"ABCABCABCABC" * 40,
            bytes(range(256)) * 4,
            b"\x00" * 4096,
        ):
            packed = compress_lz77(payload)
            self.assertEqual(header(packed), (0x10, len(payload)))
            self.assertEqual(decompress(packed), payload)

    def test_lz77_actually_compresses_repetitive_input(self):
        payload = b"abcdefgh" * 256
        self.assertLess(len(compress_lz77(payload)), len(payload) // 4)

    def test_lz11_extended_length_token(self):
        # literals "abc", then a 0x11-byte copy from three bytes back.
        body = bytes([0b00010000]) + b"abc" + bytes([0x00, 0x00, 0x02])
        stream = bytes([0x11]) + (3 + 0x11).to_bytes(3, "little") + body
        self.assertEqual(decompress(stream), b"abcabcabcabcabcabcab")

    def test_rle(self):
        stream = (
            bytes([0x30])
            + (9).to_bytes(3, "little")
            + bytes([0x03])
            + b"ABCD"
            + bytes([0x82, ord("Z")])
        )
        self.assertEqual(decompress(stream), b"ABCDZZZZZ")

    def test_huffman_8bit(self):
        tree = bytes([0x01, 0xC0, ord("A"), ord("B")])
        bitstream = (0b0110 << 28).to_bytes(4, "little")
        stream = bytes([0x28]) + (4).to_bytes(3, "little") + tree + bitstream
        self.assertEqual(decompress(stream), b"ABBA")

    def test_huffman_4bit_packs_low_nibble_first(self):
        tree = bytes([0x01, 0xC0, 0x00, 0x01])
        bitstream = (0b1001 << 28).to_bytes(4, "little")
        stream = bytes([0x24]) + (2).to_bytes(3, "little") + tree + bitstream
        self.assertEqual(decompress(stream), bytes([0x01, 0x10]))

    def test_unsupported_type_is_reported(self):
        with self.assertRaises(ParseError):
            decompress(bytes([0x40]) + (4).to_bytes(3, "little") + b"data")

    def test_truncated_stream_is_reported(self):
        packed = compress_lz77(b"abcdefgh" * 64)
        with self.assertRaises(ParseError):
            decompress(packed[:12])

    def test_back_reference_before_output_is_rejected(self):
        stream = bytes([0x10]) + (8).to_bytes(3, "little") + bytes([0x80, 0x00, 0x00])
        with self.assertRaises(ParseError):
            decompress(stream)

    def test_detect_ignores_ordinary_data(self):
        self.assertIsNone(detect(b"\x10\x00\x00\x00"))        # zero size
        self.assertIsNone(detect(b"plain text payload"))
        self.assertIsNone(detect(b"\x10"))
        self.assertEqual(detect(compress_lz77(b"x" * 500)), "lz77")

    def test_maybe_decompress_passes_through_unknown_data(self):
        self.assertEqual(maybe_decompress(b"not compressed"), (b"not compressed", None))
        payload = b"y" * 500
        self.assertEqual(maybe_decompress(compress_lz77(payload)), (payload, "lz77"))

    def test_maybe_decompress_keeps_a_false_positive_intact(self):
        # A header byte and a plausible size, but the body is not a stream.
        fake = bytes([0x10]) + (4096).to_bytes(3, "little") + b"\xff" * 64
        self.assertEqual(maybe_decompress(fake), (fake, None))


class TestNitroContainer(unittest.TestCase):
    def test_round_trip(self):
        blob = nitro.write_header("TEST", [nitro.write_section("AAAA", b"payload")])
        parsed = nitro.read_header(blob)
        self.assertEqual(parsed.magic, "TEST")
        self.assertEqual(parsed.size, len(blob))
        self.assertEqual(parsed.section("AAAA").payload_size, 7)

    def test_section_accepts_the_reversed_spelling(self):
        blob = nitro.write_header("TEST", [nitro.write_section("BTAF", b"xx")])
        self.assertTrue(nitro.read_header(blob).has("FATB"))

    def test_missing_section(self):
        blob = nitro.write_header("TEST", [nitro.write_section("AAAA", b"x")])
        with self.assertRaises(ParseError):
            nitro.read_header(blob).section("ZZZZ")

    def test_bad_byte_order_mark(self):
        blob = bytearray(nitro.write_header("TEST", [nitro.write_section("AAAA", b"x")]))
        blob[4:6] = b"\x00\x00"
        with self.assertRaises(ParseError):
            nitro.read_header(bytes(blob))


class TestNarc(unittest.TestCase):
    def test_named_archive(self):
        archive = Narc(make_narc({"readme.txt": b"hello narc", "data.bin": b"\x01\x02"}))
        entries = {str(e.path): archive.read(e) for e in archive.list()}
        self.assertEqual(entries, {"/readme.txt": b"hello narc", "/data.bin": b"\x01\x02"})

    def test_unnamed_archive_falls_back_to_indices(self):
        archive = Narc(make_narc({"a": b"x" * 5, "b": b"y" * 7}, named=False))
        entries = {str(e.path): archive.read(e) for e in archive.list()}
        self.assertEqual(entries, {"/0000.bin": b"xxxxx", "/0001.bin": b"yyyyyyy"})

    def test_rejects_other_containers(self):
        with self.assertRaises(ParseError):
            Narc(make_nclr())
        self.assertFalse(Narc.looks_like(make_nclr()))


class TestGraphics(unittest.TestCase):
    def test_palette_slots(self):
        palette = graphics.read_nclr(make_nclr())
        self.assertEqual(palette.per_palette, 16)
        self.assertEqual(palette.slot(0)[0][3], 0)  # index 0 is transparent
        with self.assertRaises(ParseError):
            palette.slot(4)

    def test_sheet_shape_and_render(self):
        sheet = graphics.read_ncgr(make_ncgr(width_tiles=4, height_tiles=2))
        self.assertEqual((sheet.width, sheet.height), (32, 16))
        rgba, width, height = graphics.render(
            make_ncgr(width_tiles=4, height_tiles=2), make_nclr()
        )
        self.assertEqual((width, height), (32, 16))
        self.assertEqual(len(rgba), 32 * 16 * 4)

    def test_detiling_puts_the_second_pixel_next_to_the_first(self):
        sheet = graphics.read_ncgr(make_ncgr(width_tiles=1, height_tiles=1))
        indices = sheet.indices()
        # The fixture stores low nibble = (tile + pixel) % 16.
        self.assertEqual(list(indices[:4]), [0, 1, 1, 2])

    def test_undeclared_shape_is_inferred(self):
        blob = bytearray(make_ncgr(width_tiles=4, height_tiles=2))
        # Blank the declared width/height, as sheets in the wild often do.
        offset = nitro.read_header(bytes(blob)).section("CHAR", "RAHC").payload_offset
        blob[offset : offset + 4] = b"\xff\xff\xff\xff"
        sheet = graphics.read_ncgr(bytes(blob))
        self.assertEqual(sheet.width_tiles * sheet.height_tiles, 8)

    def test_rejects_wrong_container(self):
        with self.assertRaises(ParseError):
            graphics.read_ncgr(make_nclr())
        with self.assertRaises(ParseError):
            graphics.read_nclr(make_ncgr())


class TestSdat(unittest.TestCase):
    def test_entries_and_names(self):
        archive = Sdat(make_sdat())
        entries = archive.entries()
        self.assertEqual([e.kind for e in entries], ["sseq", "swar"])
        self.assertEqual(entries[0].name, "BGM_TITLE")
        self.assertEqual(archive.read(entries[0]), b"SSEQ body")
        self.assertEqual(archive.read(entries[1]), b"SWAR body")
        self.assertEqual(archive.summary(), {"sseq": 1, "swar": 1})

    def test_label_falls_back_to_an_index(self):
        from reatk.formats.sdat import SdatEntry

        self.assertEqual(SdatEntry("sseq", 3, None, 0, 0, 0).label, "sseq_0003")

    def test_rejects_other_files(self):
        self.assertFalse(Sdat.looks_like(make_narc({})))
        with self.assertRaises(ParseError):
            Sdat(make_narc({"a": b"b"}))


class TestExtractionPipeline(unittest.TestCase):
    def setUp(self):
        self.rom = NdsRom(make_rich_nds())

    def test_plain_pass_writes_files_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            written = extract_filesystem(self.rom, out)
            self.assertEqual(len(written), 7)
            # Still compressed, because --decompress was not asked for.
            self.assertEqual((out / "data" / "text.bin").read_bytes()[0], 0x10)

    def test_every_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            extract_filesystem(self.rom, out, ExtractOptions.everything())
            self.assertEqual(
                (out / "data" / "text.bin").read_bytes(), b"compressed payload " * 16
            )
            self.assertEqual(
                (out / "data" / "pack.narc.d" / "inner.txt").read_bytes(),
                b"inside the archive",
            )
            self.assertTrue((out / "data" / "sound.sdat.d" / "BGM_TITLE.sseq").is_file())
            index = json.loads((out / "data" / "sound.sdat.d" / "index.json").read_text())
            self.assertEqual(index[0]["name"], "BGM_TITLE")
            png = (out / "data" / "gfx.png").read_bytes()
            self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_graphics_pass_needs_a_palette_in_the_same_directory(self):
        from tests.fixtures import make_nds

        rom = NdsRom(make_nds(data_files={"lonely.ncgr": make_ncgr()}))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            extract_filesystem(rom, out, ExtractOptions.everything())
            self.assertFalse((out / "data" / "lonely.png").exists())

    def test_damaged_container_is_noted_not_fatal(self):
        from reatk.plugins import PluginContext
        from tests.fixtures import make_nds

        broken = bytearray(make_narc({"a": b"payload"}))
        broken[0x1C:0x20] = b"\xff\xff\xff\x7f"  # nonsense section size
        rom = NdsRom(make_nds(data_files={"broken.narc": bytes(broken)}))
        ctx = PluginContext()
        with tempfile.TemporaryDirectory() as tmp:
            written = extract_filesystem(
                rom, Path(tmp), ExtractOptions.everything(), ctx=ctx
            )
        self.assertTrue(any(p.name == "broken.narc" for p in written))
        self.assertTrue(any("not unpacked" in line for line in ctx.log))


class TestNewCliCommands(unittest.TestCase):
    def test_decompress_and_compress(self):
        from reatk.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw.bin"
            raw.write_bytes(b"payload payload payload " * 20)
            packed = Path(tmp) / "packed.lz"
            self.assertEqual(main(["decompress", str(raw), "-o", str(packed), "--compress"]), 0)
            back = Path(tmp) / "back.bin"
            self.assertEqual(main(["decompress", str(packed), "-o", str(back)]), 0)
            self.assertEqual(back.read_bytes(), raw.read_bytes())

    def test_decompress_rejects_plain_file(self):
        from reatk.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw.bin"
            raw.write_bytes(b"not compressed at all")
            self.assertEqual(
                main(["decompress", str(raw), "-o", str(Path(tmp) / "x")]), 1
            )

    def test_narc_list_and_extract(self):
        from reatk.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "pack.narc"
            archive.write_bytes(make_narc({"inner.txt": b"hi"}))
            self.assertEqual(main(["narc", str(archive)]), 0)
            out = Path(tmp) / "out"
            self.assertEqual(main(["narc", str(archive), "-o", str(out)]), 0)
            self.assertEqual((out / "inner.txt").read_bytes(), b"hi")

    def test_sdat_list_and_extract(self):
        from reatk.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "sound.sdat"
            archive.write_bytes(make_sdat())
            self.assertEqual(main(["sdat", str(archive)]), 0)
            out = Path(tmp) / "out"
            self.assertEqual(main(["sdat", str(archive), "-o", str(out)]), 0)
            self.assertTrue((out / "BGM_TITLE.sseq").is_file())

    def test_gfx_render(self):
        from reatk.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            sheet = Path(tmp) / "a.ncgr"
            palette = Path(tmp) / "a.nclr"
            sheet.write_bytes(make_ncgr())
            palette.write_bytes(make_nclr())
            png = Path(tmp) / "a.png"
            self.assertEqual(main(["gfx", str(sheet), str(palette), "-o", str(png)]), 0)
            self.assertTrue(png.read_bytes().startswith(b"\x89PNG"))

    def test_extract_all_flag(self):
        from reatk.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            rom = Path(tmp) / "rich.nds"
            rom.write_bytes(make_rich_nds())
            out = Path(tmp) / "out"
            self.assertEqual(main(["extract", str(rom), "-o", str(out), "--all"]), 0)
            self.assertTrue((out / "romfs" / "data" / "gfx.png").is_file())


if __name__ == "__main__":
    unittest.main()
