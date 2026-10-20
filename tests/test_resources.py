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


class TestPngReader(unittest.TestCase):
    def test_round_trip_rgba(self):
        from reatk.convert.images import read_png, write_png

        pixels = bytes(
            [255, 0, 0, 255, 0, 255, 0, 128, 0, 0, 255, 255, 255, 255, 255, 0]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            write_png(path, pixels, 2, 2)
            self.assertEqual(read_png(path), (pixels, 2, 2))

    def test_reads_filtered_rgb(self):
        import struct
        import zlib

        from reatk.convert.images import read_png

        def chunk(tag: bytes, payload: bytes) -> bytes:
            body = tag + payload
            return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

        width = height = 4
        rows = bytearray()
        previous = [0] * (width * 3)
        for y in range(height):
            row = [(x * 17 + y * 5) % 256 for x in range(width * 3)]
            rows.append(2 if y else 0)  # Up filter on every row but the first
            rows += bytes((row[i] - previous[i]) % 256 for i in range(len(row)))
            previous = row
        png = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows)))
            + chunk(b"IEND", b"")
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.png"
            path.write_bytes(png)
            rgba, got_width, got_height = read_png(path)
        self.assertEqual((got_width, got_height), (4, 4))
        self.assertEqual(rgba[:8], bytes([0x00, 0x11, 0x22, 0xFF, 0x33, 0x44, 0x55, 0xFF]))

    def test_rejects_non_png(self):
        from reatk.convert.images import PngError, read_png

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.png"
            path.write_bytes(b"not a png at all")
            with self.assertRaises(PngError):
                read_png(path)


class TestGraphicsEncoding(unittest.TestCase):
    def setUp(self):
        self.sheet = graphics.read_ncgr(make_ncgr(width_tiles=4, height_tiles=2))
        self.palette = graphics.read_nclr(make_nclr())
        self.rgba = self.sheet.to_rgba(self.palette)

    def test_sheet_round_trip_is_lossless_with_the_original_palette(self):
        ncgr, nclr = graphics.encode_sheet(
            self.rgba, self.sheet.width, self.sheet.height, palette=self.palette
        )
        again = graphics.read_ncgr(ncgr)
        self.assertEqual(again.indices(), self.sheet.indices())
        self.assertEqual(again.to_rgba(graphics.read_nclr(nclr)), self.rgba)

    def test_8bpp_round_trip(self):
        indices, _ = graphics.quantize(self.rgba, 256)
        encoded = graphics.encode_ncgr(
            indices, self.sheet.width, self.sheet.height, graphics.BPP_8
        )
        self.assertEqual(graphics.read_ncgr(encoded).indices(), indices)

    def test_quantise_builds_an_exact_palette_when_it_fits(self):
        pixels = b"".join(
            bytes((value, value, value, 255)) for value in (0, 40, 80, 120)
        ) * 4
        indices, palette = graphics.quantize(pixels, 16)
        self.assertEqual(len(palette), 16)
        self.assertEqual(len(set(indices)), 4)

    def test_quantise_reduces_a_large_palette(self):
        pixels = b"".join(
            bytes(((i * 7) % 256, (i * 3) % 256, (i * 11) % 256, 255))
            for i in range(32 * 32)
        )
        indices, palette = graphics.quantize(pixels, 16)
        self.assertEqual(len(palette), 16)
        self.assertLessEqual(max(indices), 15)

    def test_transparent_pixels_take_index_zero(self):
        pixels = bytes([0, 0, 0, 0, 10, 20, 30, 255]) * 2
        indices, palette = graphics.quantize(pixels, 16)
        self.assertEqual(indices[0], 0)
        self.assertEqual(palette[0][3], 0)
        self.assertNotEqual(indices[1], 0)

    def test_encoder_rejects_a_partial_tile(self):
        with self.assertRaises(ValueError):
            graphics.encode_ncgr(bytes(5 * 5), 5, 5)

    def test_encoder_rejects_out_of_range_indices(self):
        with self.assertRaises(ValueError):
            graphics.encode_ncgr(bytes([99]) * 64, 8, 8)


class TestTransfer(unittest.TestCase):
    """Importing material from one title into another."""

    def setUp(self):
        from tests.fixtures import make_nds

        self.target = NdsRom(make_rich_nds())
        self.donor_image = make_ncgr(width_tiles=4, height_tiles=2, seed=5)
        self.donor = NdsRom(
            make_nds(
                game_code="DNOR",
                title="DONOR",
                data_files={
                    "hero.ncgr": self.donor_image,
                    "hero.nclr": make_nclr([index * 0x0101 for index in range(16)]),
                    "text.bin": b"donor plain text payload" * 4,
                },
            )
        )

    def _donor(self):
        from reatk.transfer import DonorRom

        return DonorRom(self.donor)

    def test_draft_pairs_by_name_and_kind(self):
        from reatk.transfer import draft_mapping

        mapping, unmatched = draft_mapping(self._donor(), self.target)
        pairs = {(e.source, e.target) for e in mapping.entries}
        self.assertIn(("/readme.txt", "/readme.txt"), pairs)
        self.assertIn(("/data/hero.ncgr", "/data/gfx.ncgr"), pairs)
        # Nothing in the donor looks like an archive, so these stay unpaired.
        self.assertIn("/data/pack.narc", unmatched["target_files_without_a_source"])
        # Low-confidence guesses are flagged in the note.
        sheet = next(e for e in mapping.entries if e.target == "/data/gfx.ncgr")
        self.assertIn("confidence", sheet.note)

    def test_mapping_round_trip_through_a_file(self):
        from reatk.transfer import Mapping, draft_mapping

        mapping, unmatched = draft_mapping(self._donor(), self.target)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "map.json"
            mapping.save(path, unmatched)
            again = Mapping.load(path)
        self.assertEqual(
            [(e.source, e.target) for e in again.entries],
            [(e.source, e.target) for e in mapping.entries],
        )

    def test_rejects_a_mapping_from_the_future(self):
        from reatk.transfer import Mapping, TransferError

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "map.json"
            path.write_text(json.dumps({"schema": 99, "entries": []}))
            with self.assertRaises(TransferError):
                Mapping.load(path)

    def test_image_import_is_pixel_exact_under_the_imported_palette(self):
        from reatk.formats import graphics as gfx
        from reatk.transfer import Mapping, MappingEntry, apply_mapping

        mapping = Mapping(
            [
                MappingEntry("/data/hero.ncgr", "/data/gfx.ncgr"),
                MappingEntry("/data/hero.nclr", "/data/gfx.nclr"),
            ]
        )
        result = apply_mapping(mapping, self._donor(), self.target)
        self.assertEqual(sorted(result.payloads), ["/data/gfx.ncgr", "/data/gfx.nclr"])

        donor_fs = self.donor.filesystem()
        by_path = {str(e.path): e for e in donor_fs}
        wanted, _, _ = gfx.render(
            donor_fs.read(by_path["/data/hero.ncgr"]),
            donor_fs.read(by_path["/data/hero.nclr"]),
        )
        got, _, _ = gfx.render(
            result.payloads["/data/gfx.ncgr"], result.payloads["/data/gfx.nclr"]
        )
        self.assertEqual(got, wanted)
        self.assertTrue(any("donor palette" in note for note in result.notes))

    def test_image_import_without_a_palette_swap_uses_the_target_palette(self):
        from reatk.transfer import Mapping, MappingEntry, apply_mapping

        result = apply_mapping(
            Mapping([MappingEntry("/data/hero.ncgr", "/data/gfx.ncgr")]),
            self._donor(),
            self.target,
        )
        self.assertIn("/data/gfx.ncgr", result.payloads)
        self.assertTrue(any("target's own palette" in note for note in result.notes))

    def test_mismatched_dimensions_are_refused(self):
        from tests.fixtures import make_nds
        from reatk.transfer import DonorRom, Mapping, MappingEntry, apply_mapping

        donor = DonorRom(
            NdsRom(
                make_nds(
                    data_files={"small.ncgr": make_ncgr(1, 1), "small.nclr": make_nclr()}
                )
            )
        )
        result = apply_mapping(
            Mapping([MappingEntry("/data/small.ncgr", "/data/gfx.ncgr")]),
            donor,
            self.target,
        )
        self.assertFalse(result.payloads)
        reason = result.skipped[0][1]
        self.assertIn("32x16", reason)
        self.assertIn("8x8", reason)

    def test_raw_import_recompresses_an_lz77_target(self):
        from reatk.formats.compression import detect, maybe_decompress
        from reatk.transfer import Mapping, MappingEntry, apply_mapping

        result = apply_mapping(
            Mapping([MappingEntry("/data/text.bin", "/data/text.bin", mode="raw")]),
            self._donor(),
            self.target,
        )
        payload = result.payloads["/data/text.bin"]
        self.assertEqual(detect(payload), "lz77")
        self.assertEqual(
            maybe_decompress(payload)[0], b"donor plain text payload" * 4
        )
        self.assertTrue(any("re-compressed as lz77" in note for note in result.notes))

    def test_unreproducible_compression_is_skipped_unless_forced(self):
        from tests.fixtures import make_nds
        from reatk.transfer import DonorRom, Mapping, MappingEntry, apply_mapping

        # A target file compressed with RLE, which this toolkit cannot encode.
        rle = bytes([0x30]) + (600).to_bytes(3, "little") + bytes([0x80 | 0x7F, 0x41]) * 4
        target = NdsRom(make_nds(data_files={"packed.bin": rle}))
        donor = DonorRom(NdsRom(make_nds(data_files={"packed.bin": b"replacement"})))
        mapping = Mapping([MappingEntry("/data/packed.bin", "/data/packed.bin")])

        refused = apply_mapping(mapping, donor, target)
        self.assertFalse(refused.payloads)
        self.assertIn("force-raw", refused.skipped[0][1])

        forced = apply_mapping(mapping, donor, target, force_raw=True)
        self.assertEqual(forced.payloads["/data/packed.bin"], b"replacement")
        self.assertTrue(any("may fail to load" in note for note in forced.notes))

    def test_missing_target_file_is_reported(self):
        from reatk.transfer import Mapping, MappingEntry, apply_mapping

        result = apply_mapping(
            Mapping([MappingEntry("/readme.txt", "/nowhere.bin")]),
            self._donor(),
            self.target,
        )
        self.assertEqual(result.skipped[0][0], "/nowhere.bin")

    def test_directory_donor_takes_png_sources(self):
        from reatk.convert.images import write_png
        from reatk.formats import graphics as gfx
        from reatk.transfer import DonorDirectory, Mapping, MappingEntry, apply_mapping

        sheet = gfx.read_ncgr(self.donor_image)
        palette = gfx.read_nclr(make_nclr())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "mod"
            (root / "data").mkdir(parents=True)
            write_png(
                root / "data" / "hero.png",
                sheet.to_rgba(palette),
                sheet.width,
                sheet.height,
            )
            result = apply_mapping(
                Mapping([MappingEntry("/data/hero.png", "/data/gfx.ncgr")]),
                DonorDirectory(root),
                self.target,
            )
        self.assertIn("/data/gfx.ncgr", result.payloads)
        rendered = gfx.read_ncgr(result.payloads["/data/gfx.ncgr"])
        self.assertEqual((rendered.width, rendered.height), (32, 16))


class TestImportCli(unittest.TestCase):
    def _write_roms(self, tmp: Path) -> tuple[Path, Path]:
        from tests.fixtures import make_nds

        donor = tmp / "donor.nds"
        target = tmp / "target.nds"
        donor.write_bytes(
            make_nds(
                game_code="DNOR",
                data_files={
                    "hero.ncgr": make_ncgr(4, 2, seed=5),
                    "hero.nclr": make_nclr([i * 0x0101 for i in range(16)]),
                },
            )
        )
        target.write_bytes(make_rich_nds())
        return donor, target

    def test_map_then_import_to_files(self):
        from reatk.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            donor, target = self._write_roms(tmp)
            mapping = tmp / "map.json"
            self.assertEqual(
                main(["map", "--from", str(donor), "--to", str(target), "-o", str(mapping)]),
                0,
            )
            document = json.loads(mapping.read_text())
            self.assertEqual(document["schema"], 1)

            out = tmp / "imported"
            report = tmp / "report.json"
            self.assertEqual(
                main(
                    [
                        "import", "--from", str(donor), "--to", str(target),
                        "--map", str(mapping), "-o", str(out), "--report", str(report),
                    ]
                ),
                0,
            )
            self.assertTrue((out / "data" / "gfx.ncgr").is_file())
            self.assertTrue(json.loads(report.read_text())["imported"])

    def test_dry_run_writes_nothing(self):
        from reatk.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            donor, target = self._write_roms(tmp)
            mapping = tmp / "map.json"
            main(["map", "--from", str(donor), "--to", str(target), "-o", str(mapping)])
            out = tmp / "nothing"
            self.assertEqual(
                main(
                    [
                        "import", "--from", str(donor), "--to", str(target),
                        "--map", str(mapping), "--dry-run",
                    ]
                ),
                0,
            )
            self.assertFalse(out.exists())

    def test_import_emits_an_app_project_with_overrides(self):
        from reatk.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            donor, target = self._write_roms(tmp)
            mapping = tmp / "map.json"
            main(["map", "--from", str(donor), "--to", str(target), "-o", str(mapping)])
            out = tmp / "app"
            self.assertEqual(
                main(
                    [
                        "import", "--from", str(donor), "--to", str(target),
                        "--map", str(mapping), "--emit", "apk", "-o", str(out),
                    ]
                ),
                0,
            )
            manifest = json.loads(
                (out / "app/src/main/assets/reatk/manifest.json").read_text()
            )
            self.assertIn("/data/gfx.ncgr", manifest["overrides"]["assets"])
            self.assertTrue(
                (out / "app/src/main/assets/reatk/overrides/data/gfx.ncgr").is_file()
            )


class TestBlz(unittest.TestCase):
    """ARM9-style backwards LZ."""

    def test_round_trip_with_and_without_a_verbatim_head(self):
        from reatk.formats.compression import compress_blz, decompress_blz

        cases = [
            (b"ABCABCABCABC" * 60, 0),
            (b"arm9 entry stub" + bytes(16) + b"repeated block " * 100, 32),
            (bytes(range(64)) * 40, 8),
        ]
        for payload, head in cases:
            packed = compress_blz(payload, verbatim=head)
            self.assertLess(len(packed), len(payload))
            self.assertEqual(packed[:head], payload[:head])
            self.assertEqual(decompress_blz(packed), payload)

    def test_footer_is_read_back(self):
        from reatk.formats.compression import compress_blz, read_blz_footer

        payload = b"repeat me " * 200
        footer = read_blz_footer(compress_blz(payload, verbatim=16))
        self.assertIsNotNone(footer)
        self.assertEqual(footer.verbatim, 16)
        self.assertEqual(footer.decompressed, len(payload))
        self.assertIn(footer.header_length, range(8, 12))

    def test_ordinary_data_is_not_mistaken_for_blz(self):
        from reatk.formats.compression import looks_like_blz

        self.assertFalse(looks_like_blz(b"just some bytes" * 20))
        self.assertFalse(looks_like_blz(bytes(64)))  # a zero footer means "raw"
        self.assertFalse(looks_like_blz(b"short"))

    def test_decode_refuses_a_file_without_a_footer(self):
        from reatk.formats.compression import decompress_blz

        with self.assertRaises(ParseError):
            decompress_blz(b"no footer here" * 8)

    def test_compress_refuses_incompressible_data(self):
        from reatk.formats.compression import compress_blz

        with self.assertRaises(ValueError):
            compress_blz(bytes(range(256)), verbatim=0)

    def test_extract_writes_a_decoded_arm9(self):
        from reatk.convert.extract import extract_code
        from reatk.plugins import PluginContext
        from tests.fixtures import make_nds_with_blz_arm9

        rom = NdsRom(make_nds_with_blz_arm9())
        ctx = PluginContext()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            written = extract_code(rom, out, decompress=True, ctx=ctx)
            self.assertIn(out / "arm9.dec.bin", written)
            decoded = (out / "arm9.dec.bin").read_bytes()
            self.assertTrue(decoded.startswith(b"arm9 entry stub"))
            self.assertGreater(len(decoded), (out / "arm9.bin").stat().st_size)
        self.assertTrue(any("BLZ decoded" in line for line in ctx.log))

    def test_analyze_reports_blz(self):
        from reatk.analyze import analyze
        from tests.fixtures import make_nds_with_blz_arm9

        report = analyze(NdsRom(make_nds_with_blz_arm9()))
        self.assertTrue(
            any("BLZ footer" in finding["message"] for finding in report["findings"])
        )

    def test_cli_round_trip(self):
        from reatk.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            raw = tmp / "arm9.bin"
            raw.write_bytes(b"stub" * 4 + b"a repeated run " * 120)
            packed = tmp / "arm9.blz"
            self.assertEqual(
                main(
                    [
                        "decompress", str(raw), "-o", str(packed),
                        "--blz", "--compress", "--blz-verbatim", "16",
                    ]
                ),
                0,
            )
            back = tmp / "arm9.dec"
            self.assertEqual(
                main(["decompress", str(packed), "-o", str(back), "--blz"]), 0
            )
            self.assertEqual(back.read_bytes(), raw.read_bytes())

    def test_cli_rejects_a_file_without_a_footer(self):
        from reatk.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            raw = tmp / "plain.bin"
            raw.write_bytes(b"plain" * 40)
            self.assertEqual(
                main(["decompress", str(raw), "-o", str(tmp / "x"), "--blz"]), 1
            )
