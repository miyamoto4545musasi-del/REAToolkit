import struct
import unittest

from reatk._util import ParseError
from reatk.formats import detect
from reatk.formats.nds import NdsRom
from tests.fixtures import BLOB, README, make_nds


class TestNdsHeader(unittest.TestCase):
    def setUp(self):
        self.rom = NdsRom(make_nds())

    def test_header_fields(self):
        self.assertEqual(self.rom.title, "FIXTURE")
        self.assertEqual(self.rom.game_code, "ATST")
        self.assertEqual(self.rom.maker, "Nintendo")
        self.assertEqual(self.rom.platform, "NDS")
        self.assertEqual(self.rom.region_letter, "T")
        self.assertEqual(self.rom.capacity_bytes, 0x20000 << 9)

    def test_header_crc(self):
        self.assertTrue(self.rom.header_crc_ok)

    def test_header_crc_detects_tampering(self):
        data = bytearray(make_nds())
        data[0x00] = ord("X")
        self.assertFalse(NdsRom(bytes(data)).header_crc_ok)

    def test_regions_are_ordered_and_non_empty(self):
        regions = self.rom.regions()
        offsets = [r.offset for r in regions]
        self.assertEqual(offsets, sorted(offsets))
        self.assertTrue(all(r.size for r in regions))
        self.assertIn("banner", [r.name for r in regions])

    def test_read_region_rejects_truncated_image(self):
        truncated = NdsRom(make_nds()[:0x4100])
        with self.assertRaises(ParseError):
            truncated.read_region(truncated.arm7)

    def test_short_image_is_rejected(self):
        with self.assertRaises(ParseError):
            NdsRom(b"\x00" * 16)

    def test_slack_ranges_cover_the_gaps(self):
        gaps = self.rom.slack_ranges()
        self.assertTrue(any(g.offset == 0x200 for g in gaps))
        for gap in gaps:
            self.assertGreater(gap.size, 0)


class TestNitroFs(unittest.TestCase):
    def setUp(self):
        self.rom = NdsRom(make_nds())
        self.fs = self.rom.filesystem()

    def test_walk_finds_both_files(self):
        paths = sorted(str(e.path) for e in self.fs)
        self.assertEqual(paths, ["/data/a.bin", "/readme.txt"])

    def test_contents_round_trip(self):
        by_path = {str(e.path): e for e in self.fs}
        self.assertEqual(self.fs.read(by_path["/readme.txt"]), README)
        self.assertEqual(self.fs.read(by_path["/data/a.bin"]), BLOB)

    def test_total_size(self):
        self.assertEqual(self.fs.total_size(), len(README) + len(BLOB))

    def test_find_glob(self):
        self.assertEqual(len(self.fs.find("*.bin")), 1)

    def test_negative_length_entry_is_rejected(self):
        data = bytearray(make_nds())
        struct.pack_into("<II", data, 0x4600, 0x8000, 0x10)  # end before start
        with self.assertRaises(ParseError):
            NdsRom(bytes(data)).filesystem().entries()


class TestBanner(unittest.TestCase):
    def setUp(self):
        self.banner = NdsRom(make_nds()).banner()

    def test_crc(self):
        self.assertTrue(self.banner.crc_ok)

    def test_titles(self):
        titles = self.banner.titles()
        self.assertIn("テスト", titles["japanese"])
        self.assertTrue(titles["english"].startswith("Fixture Title"))

    def test_icon_is_rgba_32x32(self):
        icon = self.banner.icon_rgba()
        self.assertEqual(len(icon), 32 * 32 * 4)
        alphas = set(icon[3::4])
        self.assertTrue(alphas <= {0, 255})
        self.assertIn(0, alphas)  # palette index 0 stays transparent

    def test_palette_entry_zero_is_transparent(self):
        self.assertEqual(self.banner.palette()[0][3], 0)


class TestDetect(unittest.TestCase):
    def test_detects_nds(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.nds"
            path.write_bytes(make_nds())
            self.assertEqual(detect(path), "nds")

    def test_unknown_container(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "junk.bin"
            path.write_bytes(b"\x00" * 0x200)
            with self.assertRaises(ValueError):
                detect(path)


if __name__ == "__main__":
    unittest.main()
