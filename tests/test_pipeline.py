import json
import tempfile
import unittest
from pathlib import Path

from reatk.analyze import analyze, entropy, extract_strings
from reatk.convert import extract_all, write_png
from reatk.convert.extract import _safe_target
from reatk.formats.nds import NdsRom
from reatk.package import PackageOptions, emit
from reatk.package.common import OwnershipError, build_manifest
from reatk.plugins import Plugin, PluginContext, PluginRegistry, discover
from reatk.plugins.builtin.ips_patch import apply_ips
from tests.fixtures import make_nds


class TestAnalyze(unittest.TestCase):
    def setUp(self):
        self.report = analyze(NdsRom(make_nds()))

    def test_report_shape(self):
        self.assertEqual(self.report["game_code"], "ATST")
        self.assertTrue(self.report["findings"])
        json.dumps(self.report)  # must stay serialisable

    def test_errors_sort_first(self):
        severities = [f["severity"] for f in self.report["findings"]]
        self.assertEqual(severities, sorted(severities, key={"error": 0, "warning": 1, "info": 2}.get))

    def test_filesystem_finding_counts_files(self):
        fs = [f for f in self.report["findings"] if f["kind"] == "filesystem"]
        self.assertIn("2 files", fs[0]["message"])

    def test_sdk_marker_is_reported(self):
        leftovers = [f for f in self.report["findings"] if f["kind"] == "leftovers"]
        self.assertTrue(leftovers)
        self.assertIn("NITRO-SDK", leftovers[0]["detail"]["markers"])

    def test_truncated_image_is_an_error(self):
        report = analyze(NdsRom(make_nds()[:0x5000]))
        self.assertTrue(any(f["severity"] == "error" for f in report["findings"]))

    def test_entropy_bounds(self):
        self.assertEqual(entropy(b""), 0.0)
        self.assertAlmostEqual(entropy(b"\x00" * 100), 0.0)
        self.assertGreater(entropy(bytes(range(256))), 7.9)

    def test_strings_respect_min_length(self):
        self.assertEqual(extract_strings(b"ab\x00abcdef", 4), ["abcdef"])


class TestConvert(unittest.TestCase):
    def test_extract_all_writes_every_group(self):
        rom = NdsRom(make_nds())
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            written = extract_all(rom, out, icon_scale=2)
            self.assertTrue((out / "code" / "arm9.bin").is_file())
            self.assertTrue((out / "banner" / "icon.png").is_file())
            self.assertTrue((out / "romfs" / "data" / "a.bin").is_file())
            titles = json.loads((out / "banner" / "titles.json").read_text("utf-8"))
            self.assertIn("english", titles)
            self.assertEqual(len(written["romfs"]), 2)

    def test_png_header_and_size_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_png(Path(tmp) / "x.png", b"\xff\x00\x00\xff" * 4, 2, 2)
            self.assertTrue(path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
            with self.assertRaises(ValueError):
                write_png(Path(tmp) / "y.png", b"\x00", 2, 2)

    def test_path_traversal_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                _safe_target(Path(tmp), "../escape.bin")


class TestPackage(unittest.TestCase):
    def setUp(self):
        self.rom = NdsRom(make_nds())

    def test_manifest_defaults(self):
        options = PackageOptions(out_dir=Path("."))
        manifest = build_manifest(self.rom, options)
        self.assertTrue(manifest["app"]["name"].startswith("Fixture Title"))
        self.assertEqual(manifest["app"]["bundle_id"], "com.example.reatk.atst")
        self.assertFalse(manifest["rom"]["embedded"])
        self.assertTrue(manifest["rom"]["import_at_runtime"])
        self.assertFalse(manifest["core"]["bundled"])

    def test_android_project_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = emit("apk", self.rom, PackageOptions(out_dir=Path(tmp), bundle_id="com.t.app"))
            for relative in (
                "settings.gradle.kts",
                "README.md",
                "app/build.gradle.kts",
                "app/src/main/AndroidManifest.xml",
                "app/src/main/java/com/t/app/MainActivity.kt",
                "app/src/main/java/com/t/app/EmuCore.kt",
                "app/src/main/java/com/t/app/EmuView.kt",
                "app/src/main/java/com/t/app/ControlOverlay.kt",
                "app/src/main/cpp/reatk_host.c",
                "app/src/main/cpp/CMakeLists.txt",
                "app/src/main/assets/reatk/manifest.json",
                "app/src/main/res/mipmap-xxhdpi/ic_launcher.png",
                "app/src/main/res/values/strings.xml",
            ):
                self.assertTrue((root / relative).is_file(), relative)
            host = (root / "app/src/main/cpp/reatk_host.c").read_text()
            self.assertIn("Java_com_t_app_EmuCore_open", host)
            self.assertNotIn("PACKAGE_1PATH", host)
            self.assertFalse((root / "app/src/main/assets/reatk/game.nds").exists())

    def test_ios_project_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = emit("ipa", self.rom, PackageOptions(out_dir=Path(tmp), orientation="landscape"))
            for relative in (
                "project.yml",
                "README.md",
                "Sources/AppDelegate.swift",
                "Sources/GameViewController.swift",
                "Sources/EmulatorCore.swift",
                "Sources/ControlOverlay.swift",
                "Sources/AudioOutput.swift",
                "Resources/Info.plist",
                "Resources/AppIcon.png",
                "Resources/reatk/manifest.json",
                "scripts/build_ipa.sh",
            ):
                self.assertTrue((root / relative).is_file(), relative)
            plist = (root / "Resources/Info.plist").read_text()
            self.assertIn("LandscapeLeft", plist)
            self.assertNotIn("UIInterfaceOrientationPortrait", plist)
            self.assertTrue((root / "scripts/build_ipa.sh").stat().st_mode & 0o111)

    def test_strings_xml_escapes_app_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = emit("apk", self.rom, PackageOptions(out_dir=Path(tmp), app_name='A & B "x"'))
            xml = (root / "app/src/main/res/values/strings.xml").read_text()
            self.assertIn("A &amp; B &quot;x&quot;", xml)

    def test_gradle_and_yaml_names_are_sanitised(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = emit("apk", self.rom, PackageOptions(out_dir=Path(tmp), app_name='My: "Game"'))
            self.assertIn('rootProject.name = "My-Game"', (root / "settings.gradle.kts").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            root = emit("ipa", self.rom, PackageOptions(out_dir=Path(tmp), app_name='My: "Game"'))
            spec = (root / "project.yml").read_text()
            self.assertIn('CFBundleDisplayName: "My: \\"Game\\""', spec)
            self.assertIn("  MyGame:", spec)

    def test_embedding_requires_ownership_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            options = PackageOptions(out_dir=Path(tmp), embed_rom=True)
            with self.assertRaises(OwnershipError):
                emit("apk", self.rom, options)
            self.assertFalse(list(Path(tmp).iterdir()))

    def test_embedding_with_ownership_flag_writes_the_rom(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = emit(
                "apk",
                self.rom,
                PackageOptions(out_dir=Path(tmp), embed_rom=True, rom_owned=True),
            )
            embedded = root / "app/src/main/assets/reatk/game.nds"
            self.assertEqual(embedded.read_bytes(), self.rom.data)

    def test_unknown_target(self):
        with self.assertRaises(ValueError):
            emit("exe", self.rom, PackageOptions(out_dir=Path(".")))


class TestPlugins(unittest.TestCase):
    def test_builtins_are_discovered(self):
        names = set(discover().names())
        self.assertEqual(
            names, {"ips-patch", "asset-replace", "touch-controls", "cross-import"}
        )

    def test_touch_controls_lands_in_the_manifest(self):
        rom = NdsRom(make_nds())
        registry = discover(enabled=["touch-controls"])
        ctx = PluginContext()
        with tempfile.TemporaryDirectory() as tmp:
            root = emit("ipa", rom, PackageOptions(out_dir=Path(tmp)), registry=registry, ctx=ctx)
            manifest = json.loads((root / "Resources/reatk/manifest.json").read_text())
        self.assertIn("dpad", [b["id"] for b in manifest["controls"]["portrait"]["buttons"]])
        self.assertTrue(ctx.log)

    def test_asset_replace_swaps_a_file(self):
        rom = NdsRom(make_nds())
        registry = discover(enabled=["asset-replace"])
        with tempfile.TemporaryDirectory() as tmp:
            mod = Path(tmp) / "mod" / "data"
            mod.mkdir(parents=True)
            (mod / "a.bin").write_bytes(b"replaced")
            ctx = PluginContext(options={"assets": str(Path(tmp) / "mod")})
            registry.run_rom(rom, ctx)
            fs = rom.filesystem()
            entry = fs.find("*a.bin")[0]
            self.assertEqual(registry.run_asset(entry, fs.read(entry), ctx), b"replaced")

            # The same hook drives packaging: the replacement lands in the app
            # bundle and is listed in the manifest.
            out = Path(tmp) / "out"
            emit(
                "apk",
                rom,
                PackageOptions(out_dir=out, plugin_options={"assets": str(Path(tmp) / "mod")}),
                registry=registry,
                ctx=ctx,
            )
            manifest = json.loads((out / "app/src/main/assets/reatk/manifest.json").read_text())
            self.assertEqual(manifest["overrides"]["assets"], ["/data/a.bin"])
            override = out / "app/src/main/assets/reatk/overrides/data/a.bin"
            self.assertEqual(override.read_bytes(), b"replaced")

    def test_ips_patch_plain_and_rle_records(self):
        patch = b"PATCH"
        patch += (1).to_bytes(3, "big") + (2).to_bytes(2, "big") + b"AB"
        patch += (5).to_bytes(3, "big") + (0).to_bytes(2, "big") + (3).to_bytes(2, "big") + b"Z"
        patch += b"EOF"
        self.assertEqual(apply_ips(b"\x00" * 8, patch), b"\x00AB\x00\x00ZZZ")

    def test_ips_patch_rejects_foreign_file(self):
        with self.assertRaises(ValueError):
            apply_ips(b"\x00", b"NOTAPATCH")

    def test_ips_patch_extends_a_short_image(self):
        patch = b"PATCH" + (4).to_bytes(3, "big") + (2).to_bytes(2, "big") + b"XY" + b"EOF"
        self.assertEqual(apply_ips(b"\x00\x00", patch), b"\x00\x00\x00\x00XY")

    def test_hooks_chain_in_registration_order(self):
        class First(Plugin):
            name = "first"

            def on_manifest(self, manifest, ctx):
                manifest["order"] = ["first"]
                return manifest

        class Second(Plugin):
            name = "second"

            def on_manifest(self, manifest, ctx):
                manifest["order"].append("second")
                return manifest

        registry = PluginRegistry([First(), Second()])
        self.assertEqual(registry.run_manifest({}, PluginContext())["order"], ["first", "second"])

    def test_unknown_plugin_name_filters_to_nothing(self):
        self.assertEqual(discover(enabled=["nope"]).names(), [])


class TestCli(unittest.TestCase):
    def _rom_file(self, tmp):
        path = Path(tmp) / "fixture.nds"
        path.write_bytes(make_nds())
        return path

    def test_info_and_analyze_and_ls(self):
        from reatk.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            rom = self._rom_file(tmp)
            self.assertEqual(main(["info", str(rom)]), 0)
            self.assertEqual(main(["ls", str(rom)]), 0)
            report = Path(tmp) / "report.json"
            self.assertEqual(main(["analyze", str(rom), "--json", str(report)]), 0)
            self.assertEqual(json.loads(report.read_text())["game_code"], "ATST")

    def test_convert_refuses_embedding_without_flag(self):
        from reatk.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            rom = self._rom_file(tmp)
            code = main(
                ["convert", str(rom), "-t", "apk", "-o", str(Path(tmp) / "out"), "--embed-rom"]
            )
            self.assertEqual(code, 2)

    def test_convert_with_plugin_option(self):
        from reatk.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            rom = self._rom_file(tmp)
            out = Path(tmp) / "out"
            code = main(
                [
                    "convert", str(rom), "-t", "ipa", "-o", str(out),
                    "--plugin", "touch-controls", "--set", "layout=",
                ]
            )
            self.assertEqual(code, 0)
            self.assertTrue((out / "project.yml").is_file())

    def test_bad_set_syntax(self):
        from reatk.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            rom = self._rom_file(tmp)
            with self.assertRaises(SystemExit):
                main(["extract", str(rom), "-o", str(Path(tmp) / "x"), "--set", "oops"])

    def test_unknown_plugin_is_an_error(self):
        from reatk.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            rom = self._rom_file(tmp)
            with self.assertRaises(SystemExit):
                main(["extract", str(rom), "-o", str(Path(tmp) / "x"), "--plugin", "nope"])

    def test_detect_handles_unknown_file(self):
        from reatk.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            junk = Path(tmp) / "junk.bin"
            junk.write_bytes(b"\x01" * 0x200)
            self.assertEqual(main(["detect", str(junk)]), 0)


if __name__ == "__main__":
    unittest.main()


class TestTemplates(unittest.TestCase):
    """The emitters have to supply every placeholder their templates use."""

    def test_every_template_is_fully_rendered(self):
        from reatk.formats.nds import NdsRom
        from tests.fixtures import make_nds

        rom = NdsRom(make_nds())
        for target in ("apk", "ipa"):
            with tempfile.TemporaryDirectory() as tmp:
                root = emit(target, rom, PackageOptions(out_dir=Path(tmp)))
                for path in root.rglob("*"):
                    if not path.is_file() or path.suffix in (".png", ".nds"):
                        continue
                    self.assertNotIn(
                        "@@", path.read_text("utf-8"), f"{path} has an unfilled token"
                    )

    def test_missing_token_is_an_error(self):
        from reatk.package import templates

        with self.assertRaises(templates.MissingToken):
            templates.render("android/settings.gradle.kts")

    def test_android_templates_cover_the_generated_sources(self):
        from reatk.package import templates

        self.assertEqual(
            templates.tokens("android/reatk_host.c"), {"CORE", "JNI_PREFIX"}
        )
