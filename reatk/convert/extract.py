"""Dump the parsed structures of a ROM into a directory tree.

The filesystem pass is a small pipeline.  Each file is read, offered to the
plugins' ``on_asset`` hook, optionally decompressed, and then written; files
that turn out to be containers are unpacked beside themselves into a
``<name>.d/`` directory.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from ..formats import graphics
from ..formats.compression import maybe_decompress
from ..formats.narc import Narc
from ..formats.nds import NdsRom
from ..formats.sdat import Sdat
from .._util import ParseError
from .images import scale_rgba, write_png


@dataclasses.dataclass
class ExtractOptions:
    """Which passes the filesystem dump runs."""

    decompress: bool = False
    recurse_narc: bool = False
    extract_sdat: bool = False
    render_graphics: bool = False
    icon_scale: int = 8

    @classmethod
    def everything(cls, icon_scale: int = 8) -> "ExtractOptions":
        return cls(
            decompress=True,
            recurse_narc=True,
            extract_sdat=True,
            render_graphics=True,
            icon_scale=icon_scale,
        )


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
    banner = rom.try_banner()
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


def _unpack_narc(data: bytes, target: Path, options: ExtractOptions) -> list[Path]:
    """Expand a NARC beside the file it came from."""
    archive = Narc(data)
    written = []
    for entry in archive.list():
        inner = _safe_target(target, str(entry.path))
        inner.parent.mkdir(parents=True, exist_ok=True)
        payload = archive.read(entry)
        if options.decompress:
            payload, _ = maybe_decompress(payload)
        inner.write_bytes(payload)
        written.append(inner)
    return written


def _unpack_sdat(data: bytes, target: Path) -> list[Path]:
    """Expand a sound archive into one file per record."""
    archive = Sdat(data)
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for entry in archive.entries():
        inner = target / f"{entry.label}.{entry.kind}"
        inner.write_bytes(archive.read(entry))
        written.append(inner)
    index = target / "index.json"
    index.write_text(
        json.dumps(
            [
                {
                    "kind": entry.kind,
                    "name": entry.name,
                    "file_id": entry.file_id,
                    "size": entry.size,
                }
                for entry in archive.entries()
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        "utf-8",
    )
    return written + [index]


def _render_graphics(written: list[Path]) -> list[Path]:
    """Render every NCGR sheet that has a palette next to it.

    Pairing is by directory: titles keep a sheet and its NCLR together, and
    guessing beyond that produces wrong colours rather than no colours.
    """
    sheets = [p for p in written if p.suffix.lower() in (".ncgr", ".rgcn")]
    palettes = {}
    for path in written:
        if path.suffix.lower() in (".nclr", ".rlcn"):
            palettes.setdefault(path.parent, path)

    rendered = []
    for sheet in sheets:
        palette = palettes.get(sheet.parent)
        if palette is None:
            continue
        try:
            rgba, width, height = graphics.render(
                sheet.read_bytes(), palette.read_bytes()
            )
        except (ParseError, ValueError, IndexError):
            continue  # a sheet this toolkit cannot read yet is not fatal
        rendered.append(write_png(sheet.with_suffix(".png"), rgba, width, height))
    return rendered


def extract_filesystem(
    rom: NdsRom,
    out_dir: Path,
    options: ExtractOptions | None = None,
    registry=None,
    ctx=None,
) -> list[Path]:
    """Dump every NitroFS file, preserving its in-ROM path."""
    options = options or ExtractOptions()
    fs = rom.filesystem()
    written: list[Path] = []

    for entry in fs.entries():
        target = _safe_target(out_dir, str(entry.path))
        target.parent.mkdir(parents=True, exist_ok=True)

        data = fs.read(entry)
        if registry is not None:
            data = registry.run_asset(entry, data, ctx)
        if options.decompress:
            data, codec = maybe_decompress(data)
            if codec and ctx is not None:
                ctx.note(f"decompressed {entry.path} ({codec})")

        target.write_bytes(data)
        written.append(target)

        try:
            if options.recurse_narc and Narc.looks_like(data):
                written += _unpack_narc(data, target.with_suffix(target.suffix + ".d"), options)
            elif options.extract_sdat and Sdat.looks_like(data):
                written += _unpack_sdat(data, target.with_suffix(target.suffix + ".d"))
        except ParseError as exc:
            if ctx is not None:
                ctx.note(f"{entry.path}: container not unpacked ({exc})")

    if options.render_graphics:
        written += _render_graphics(written)
    return written


def extract_all(
    rom: NdsRom,
    out_dir: str | Path,
    icon_scale: int = 8,
    options: ExtractOptions | None = None,
    registry=None,
    ctx=None,
) -> dict[str, list[Path]]:
    """Run every extractor into ``<out_dir>/{code,banner,romfs}``."""
    out_dir = Path(out_dir)
    return {
        "code": extract_code(rom, out_dir / "code"),
        "banner": extract_banner(rom, out_dir / "banner", icon_scale),
        "romfs": extract_filesystem(rom, out_dir / "romfs", options, registry, ctx),
    }
