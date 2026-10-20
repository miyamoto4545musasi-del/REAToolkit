"""Shared packaging logic: options, identifiers, the app manifest.

The emitters never invoke a compiler.  They write a project you can open in
Android Studio or Xcode and build with your own signing identity, which is the
only way a sideloadable build is going to work anyway.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

from .._util import human_size

SAFE_NAME = re.compile(r"[^A-Za-z0-9]+")


def banner_of(rom):
    """The ROM's banner when it has a readable one, else ``None``.

    GBA images have no banner at all, and a damaged DS dump can have an
    unreadable one; neither should stop packaging.
    """
    getter = getattr(rom, "try_banner", None) or getattr(rom, "banner", None)
    return getter() if getter else None


class OwnershipError(RuntimeError):
    """Raised when a run asks to embed a ROM without the ownership flag."""


@dataclasses.dataclass
class PackageOptions:
    """Everything the emitters need that is not derived from the ROM."""

    out_dir: Path
    app_name: str | None = None
    bundle_id: str | None = None
    version: str = "1.0.0"
    core: str = "melonds"
    embed_rom: bool = False
    rom_owned: bool = False
    icon_scale: int = 8
    orientation: str = "both"
    plugin_options: dict = dataclasses.field(default_factory=dict)

    def resolved_app_name(self, rom) -> str:
        if self.app_name:
            return self.app_name
        banner = banner_of(rom)
        if banner:
            titles = banner.titles()
            for language in ("english", "japanese"):
                if titles.get(language):
                    return titles[language].splitlines()[0]
        return rom.title or rom.game_code or "Untitled"

    def resolved_bundle_id(self, rom) -> str:
        if self.bundle_id:
            return self.bundle_id
        code = SAFE_NAME.sub("", (rom.game_code or "rom")).lower() or "rom"
        return f"com.example.reatk.{code}"

    def check_embedding(self) -> None:
        """Embedding somebody else's ROM in an app is redistribution."""
        if self.embed_rom and not self.rom_owned:
            raise OwnershipError(
                "--embed-rom copies the ROM image into the app bundle, which is "
                "redistribution unless the title is yours (homebrew you wrote, or "
                "a dump of a cartridge you own for your own use). Re-run with "
                "--i-own-this-rom to confirm, or drop --embed-rom and let the app "
                "import a ROM from the device at runtime."
            )


def build_manifest(rom, options: PackageOptions) -> dict:
    """The description the generated app reads at startup.

    Written as ``assets/reatk/manifest.json`` on Android and
    ``Resources/reatk/manifest.json`` on iOS so both runtimes share one schema.
    """
    banner = banner_of(rom)
    manifest = {
        "schema": 1,
        "app": {
            "name": options.resolved_app_name(rom),
            "bundle_id": options.resolved_bundle_id(rom),
            "version": options.version,
            "orientation": options.orientation,
        },
        "title": {
            "platform": rom.platform,
            "internal_name": rom.title,
            "game_code": rom.game_code,
            "maker_code": rom.maker_code,
            "rom_version": getattr(rom, "rom_version", 0),
            "banner_titles": banner.titles() if banner else {},
        },
        "rom": {
            "embedded": bool(options.embed_rom),
            "path": "reatk/game.nds" if options.embed_rom else None,
            "size": len(rom.data),
            "size_human": human_size(len(rom.data)),
            "import_at_runtime": not options.embed_rom,
        },
        "core": {
            "name": options.core,
            "bundled": False,
            "note": "drop the libretro core for this platform into the path the "
            "project README gives; no core binary is generated or shipped here",
        },
    }
    return manifest


def write_manifest(manifest: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return path


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")
    return path
