"""Dump the parsed structures of a ROM into a directory tree."""

from __future__ import annotations

import json
from pathlib import Path

from ..formats.nds import NdsRom
from .images import scale_rgba, write_png


def extract_code(rom: NdsRom, out_dir: Path) -> list[Path]:
    """Write the ARM9/ARM7 binaries and their overlay tables."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for region in (rom.arm9, rom.arm7, rom.arm9_overlay, rom.arm7_overlay):
        if not region.size:
            continue
        target = out_dir / f"{region.name}.bin"
        target.write_bytes(rom.read_region(region))
        written.append(target)
    return written


def extract_banner(rom: NdsRom, out_dir: Path, icon_scale: int = 8) -> list[Path]:
    """Write ``icon.png`` (scaled) and ``titles.json``."""
    banner = rom.banner()
    if banner is None:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    icon = write_png(
        out_dir / "icon.png",
        scale_rgba(banner.icon_rgba(), 32, 32, icon_scale),
        32 * icon_scale,
        32 * icon_scale,
    )
    titles = out_dir / "titles.json"
    titles.write_text(
        json.dumps(banner.titles(), ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    return [icon, titles]


def _safe_target(root: Path, relative: str) -> Path:
    """Join *relative* under *root*, refusing anything that escapes it.

    ROM filenames are attacker-controlled data as far as this process is
    concerned, so ``../`` and absolute paths have to be rejected rather than
    normalised away.
    """
    target = (root / relative.lstrip("/")).resolve()
    if root.resolve() not in target.parents and target != root.resolve():
        raise ValueError(f"filesystem entry {relative!r} escapes the output directory")
    return target


def extract_filesystem(rom: NdsRom, out_dir: Path) -> list[Path]:
    """Dump every NitroFS file, preserving its in-ROM path."""
    fs = rom.filesystem()
    written = []
    for entry in fs.entries():
        target = _safe_target(out_dir, str(entry.path))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(fs.read(entry))
        written.append(target)
    return written


def extract_all(rom: NdsRom, out_dir: str | Path, icon_scale: int = 8) -> dict[str, list[Path]]:
    """Run every extractor into ``<out_dir>/{code,banner,romfs}``."""
    out_dir = Path(out_dir)
    return {
        "code": extract_code(rom, out_dir / "code"),
        "banner": extract_banner(rom, out_dir / "banner", icon_scale),
        "romfs": extract_filesystem(rom, out_dir / "romfs"),
    }
