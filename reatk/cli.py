"""Command line entry point: ``reatk <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from ._util import ParseError, human_size
from .analyze import analyze, extract_strings
from .convert import ExtractOptions, extract_all, write_png
from .formats import detect, open_rom
from .formats import graphics
from .formats.compression import compress_blz, compress_lz77, decompress
from .formats.compression import decompress_blz, looks_like_blz
from .formats.compression import detect as detect_compression
from .formats.narc import Narc
from .formats.sdat import Sdat
from .package import PackageOptions, emit
from .package.common import OwnershipError
from .plugins import PluginContext, discover


def _parse_sets(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--set expects key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        out[key.strip()] = value
    return out


def _load_registry(args):
    enabled = None
    if getattr(args, "plugin", None):
        enabled = args.plugin
    registry = discover(extra_dirs=getattr(args, "plugin_dir", None) or [], enabled=enabled)
    if enabled:
        missing = set(enabled) - set(registry.names())
        if missing:
            raise SystemExit(f"unknown plugin(s): {', '.join(sorted(missing))}")
    return registry


# -- commands ------------------------------------------------------------


def cmd_info(args) -> int:
    rom = open_rom(args.rom)
    print(f"{rom.source}")
    print(f"  platform      {rom.platform}")
    print(f"  title         {rom.title!r}")
    print(f"  game code     {rom.game_code} (region {getattr(rom, 'region_letter', '?')})")
    print(f"  maker         {rom.maker_code} {getattr(rom, 'maker', '')}")
    print(f"  image size    {human_size(len(rom.data))}")
    if hasattr(rom, "capacity_bytes"):
        print(f"  cart capacity {human_size(rom.capacity_bytes)}")
        print(f"  header CRC    {'ok' if rom.header_crc_ok else 'MISMATCH'}")
        banner = rom.banner()
        if banner:
            for language, title in banner.titles().items():
                print(f"  title[{language}] {title.replace(chr(10), ' / ')}")
        print("  regions:")
        for region in rom.regions():
            print(f"    {region.name:<14} 0x{region.offset:08X} {human_size(region.size):>10}")
    return 0


def cmd_analyze(args) -> int:
    rom = open_rom(args.rom)
    if not hasattr(rom, "filesystem"):
        raise SystemExit("analyze currently covers DS images only")
    report = analyze(rom)
    if args.json:
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if args.json == "-":
            print(text)
        else:
            Path(args.json).write_text(text + "\n", "utf-8")
            print(f"wrote {args.json}")
        return 0

    print(f"{report['title']} [{report['game_code']}] {report['platform']}")
    for finding in report["findings"]:
        mark = {"error": "!!", "warning": " !", "info": "  "}[finding["severity"]]
        print(f"{mark} {finding['kind']}: {finding['message']}")
    return 1 if any(f["severity"] == "error" for f in report["findings"]) else 0


def cmd_ls(args) -> int:
    rom = open_rom(args.rom)
    entries = rom.filesystem().entries()
    if args.glob:
        entries = [e for e in entries if e.path.match(args.glob)]
    for entry in entries:
        print(f"{entry.size:>10}  0x{entry.offset:08X}  {entry.path}")
    print(f"-- {len(entries)} file(s)", file=sys.stderr)
    return 0


def cmd_strings(args) -> int:
    rom = open_rom(args.rom)
    for value in extract_strings(rom.data, args.min_length):
        print(value)
    return 0


def cmd_extract(args) -> int:
    rom = open_rom(args.rom)
    registry = _load_registry(args)
    ctx = PluginContext(options=_parse_sets(args.set))
    registry.run_rom(rom, ctx)

    options = ExtractOptions(
        decompress=args.decompress,
        recurse_narc=args.recurse_narc,
        extract_sdat=args.extract_sdat,
        render_graphics=args.render_graphics,
        icon_scale=args.icon_scale,
    )
    if args.all:
        options = ExtractOptions.everything(icon_scale=args.icon_scale)
    written = extract_all(
        rom,
        args.out,
        icon_scale=args.icon_scale,
        options=options,
        registry=registry,
        ctx=ctx,
    )
    for group, paths in written.items():
        print(f"{group}: {len(paths)} file(s)")
    for line in ctx.log:
        print(f"  {line}")
    print(f"wrote {args.out}")
    return 0


def cmd_decompress(args) -> int:
    data = Path(args.file).read_bytes()
    if args.blz:
        if args.compress:
            payload = compress_blz(data, verbatim=args.blz_verbatim)
            Path(args.out).write_bytes(payload)
            print(f"blz: {len(data)} -> {len(payload)} bytes")
            return 0
        if not looks_like_blz(data):
            print(f"{args.file}: no usable BLZ footer", file=sys.stderr)
            return 1
        payload = decompress_blz(data)
        Path(args.out).write_bytes(payload)
        print(f"blz: {len(data)} -> {len(payload)} bytes")
        return 0
    if args.compress:
        payload = compress_lz77(data)
        Path(args.out).write_bytes(payload)
        print(f"lz77: {len(data)} -> {len(payload)} bytes")
        return 0
    codec = detect_compression(data)
    if codec is None:
        print(
            f"{args.file}: not a recognised BIOS-compressed stream "
            f"(header byte 0x{data[0]:02X})",
            file=sys.stderr,
        )
        return 1
    payload = decompress(data)
    Path(args.out).write_bytes(payload)
    print(f"{codec}: {len(data)} -> {len(payload)} bytes")
    return 0


def cmd_narc(args) -> int:
    archive = Narc(Path(args.file).read_bytes())
    entries = archive.list()
    if args.out is None:
        for entry in entries:
            print(f"{entry.size:>10}  {entry.path}")
        print(f"-- {len(entries)} file(s)", file=sys.stderr)
        return 0
    out = Path(args.out)
    for entry in entries:
        target = out / str(entry.path).lstrip("/")
        if out.resolve() not in target.resolve().parents:
            raise SystemExit(f"archive entry {entry.path} escapes the output directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read(entry))
    print(f"extracted {len(entries)} file(s) to {out}")
    return 0


def cmd_sdat(args) -> int:
    archive = Sdat(Path(args.file).read_bytes())
    entries = archive.entries()
    if args.out is None:
        for entry in entries:
            print(f"{entry.kind:<7} {entry.size:>9}  {entry.label}")
        print(f"-- {archive.summary()}", file=sys.stderr)
        return 0
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        (out / f"{entry.label}.{entry.kind}").write_bytes(archive.read(entry))
    print(f"extracted {len(entries)} record(s) to {out}")
    return 0


def cmd_gfx(args) -> int:
    rgba, width, height = graphics.render(
        Path(args.ncgr).read_bytes(), Path(args.nclr).read_bytes(), slot=args.palette
    )
    write_png(args.out, rgba, width, height)
    print(f"wrote {args.out} ({width}x{height})")
    return 0


def cmd_convert(args) -> int:
    rom = open_rom(args.rom)
    registry = _load_registry(args)
    plugin_options = _parse_sets(args.set)
    ctx = PluginContext(options=plugin_options)
    registry.run_rom(rom, ctx)

    if registry.names():
        print(f"plugins: {', '.join(registry.names())}")
    return _emit_project(args, rom, registry, ctx, plugin_options)


def _emit_project(args, rom, registry, ctx, plugin_options) -> int:
    """Shared tail of `convert` and `import --emit apk|ipa`."""
    options = PackageOptions(
        out_dir=Path(args.out),
        app_name=getattr(args, "app_name", None),
        bundle_id=getattr(args, "bundle_id", None),
        version=getattr(args, "app_version", "1.0.0"),
        core=getattr(args, "core", "melonds"),
        embed_rom=getattr(args, "embed_rom", False),
        rom_owned=getattr(args, "i_own_this_rom", False),
        icon_scale=getattr(args, "icon_scale", 8),
        orientation=getattr(args, "orientation", "both"),
        plugin_options=plugin_options,
    )
    target = args.target if hasattr(args, "target") else args.emit
    try:
        root = emit(target, rom, options, registry=registry, ctx=ctx)
    except OwnershipError as exc:
        print(f"refusing to embed: {exc}", file=sys.stderr)
        return 2
    for line in ctx.log:
        print(f"  {line}")
    print(f"wrote {target} project to {root}")
    print(f"next steps: {root / 'README.md'}")
    return 0


def cmd_map(args) -> int:
    from .transfer import draft_mapping, open_donor

    donor = open_donor(args.source)
    target = open_rom(args.target_rom)
    mapping, unmatched = draft_mapping(donor, target, limit_per_file=1)
    mapping.save(args.out, unmatched)
    print(f"drafted {len(mapping.entries)} pairing(s) into {args.out}")
    print(
        f"  {len(unmatched['target_files_without_a_source'])} target file(s) had no "
        f"candidate, {len(unmatched['donor_files_unused'])} donor file(s) unused"
    )
    print("review the confidences before importing; a draft is a guess, not an answer")
    return 0


def cmd_import(args) -> int:
    from .transfer import Mapping, apply_mapping, open_donor

    donor = open_donor(args.source)
    rom = open_rom(args.target_rom)
    result = apply_mapping(
        Mapping.load(args.map), donor, rom, force_raw=args.force_raw
    )

    for note in result.notes:
        print(f"  {note}")
    for path, reason in result.skipped:
        print(f"  skipped {path}: {reason}", file=sys.stderr)
    print(f"{len(result.payloads)} file(s) ready, {len(result.skipped)} skipped")

    if args.dry_run:
        if args.report:
            Path(args.report).write_text(
                json.dumps(result.as_dict(), ensure_ascii=False, indent=2) + "\n", "utf-8"
            )
            print(f"wrote {args.report}")
        return 0 if result.payloads else 1
    if not args.out:
        raise SystemExit("--out is required unless --dry-run is given")
    if not result.payloads:
        print("nothing to write", file=sys.stderr)
        return 1

    if args.emit == "files":
        out = Path(args.out)
        for path, payload in sorted(result.payloads.items()):
            target = out / path.lstrip("/")
            if out.resolve() not in target.resolve().parents:
                raise SystemExit(f"{path} escapes the output directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        if args.report:
            Path(args.report).write_text(
                json.dumps(result.as_dict(), ensure_ascii=False, indent=2) + "\n", "utf-8"
            )
        print(f"wrote {len(result.payloads)} file(s) to {out}")
        return 0

    # Emit an app project with the imported files bundled as overrides.
    plugin_options = {
        "from": str(args.source),
        "map": str(args.map),
        "force-raw": "1" if args.force_raw else "",
    }
    registry = discover(enabled=["cross-import"])
    ctx = PluginContext(options=plugin_options)
    registry.run_rom(rom, ctx)
    return _emit_project(args, rom, registry, ctx, plugin_options)


def cmd_plugins(args) -> int:
    registry = discover(extra_dirs=args.plugin_dir or [])
    if not registry.plugins:
        print("no plugins found")
        return 0
    for plugin in registry.plugins:
        print(f"{plugin.name:<18} {plugin.description}")
    return 0


def cmd_detect(args) -> int:
    for rom in args.rom:
        try:
            print(f"{detect(rom)}  {rom}")
        except ValueError as exc:
            print(f"?    {rom}: {exc}")
    return 0


# -- parser --------------------------------------------------------------


def _add_plugin_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--plugin",
        action="append",
        metavar="NAME",
        help="enable only this plugin (repeatable); default is every discovered one",
    )
    parser.add_argument(
        "--plugin-dir",
        action="append",
        metavar="DIR",
        help="also load plugins from this directory (repeatable)",
    )
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="option passed through to plugins (repeatable)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reatk",
        description="Reverse-engineer handheld ROM images and re-package them "
        "as Android or iOS projects.",
        epilog="Work on homebrew you wrote or dumps of cartridges you own. "
        "See LEGAL.md.",
    )
    parser.add_argument("--version", action="version", version=f"reatk {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("info", help="print the cartridge header")
    p.add_argument("rom")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("detect", help="identify one or more images")
    p.add_argument("rom", nargs="+")
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("analyze", help="run the inspection passes")
    p.add_argument("rom")
    p.add_argument("--json", metavar="PATH", help="write the report as JSON ('-' for stdout)")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("ls", help="list the embedded filesystem")
    p.add_argument("rom")
    p.add_argument("--glob", help="only paths matching this glob")
    p.set_defaults(func=cmd_ls)

    p = sub.add_parser("strings", help="printable ASCII runs in the image")
    p.add_argument("rom")
    p.add_argument("-n", "--min-length", type=int, default=6)
    p.set_defaults(func=cmd_strings)

    p = sub.add_parser("extract", help="dump code, banner and filesystem to a directory")
    p.add_argument("rom")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--icon-scale", type=int, default=8)
    p.add_argument("--decompress", action="store_true", help="undo BIOS compression")
    p.add_argument("--recurse-narc", action="store_true", help="unpack NARC archives")
    p.add_argument("--extract-sdat", action="store_true", help="unpack sound archives")
    p.add_argument(
        "--render-graphics",
        action="store_true",
        help="render NCGR sheets that have an NCLR palette beside them",
    )
    p.add_argument("--all", action="store_true", help="every pass above")
    _add_plugin_flags(p)
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("convert", help="emit an Android or iOS project for a title")
    p.add_argument("rom")
    p.add_argument("-t", "--target", required=True, choices=["apk", "android", "ipa", "ios"])
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--app-name")
    p.add_argument("--bundle-id")
    p.add_argument("--app-version", default="1.0.0")
    p.add_argument("--core", default="melonds", help="libretro core the project expects")
    p.add_argument(
        "--orientation", default="both", choices=["portrait", "landscape", "both"]
    )
    p.add_argument("--icon-scale", type=int, default=8)
    p.add_argument(
        "--embed-rom",
        action="store_true",
        help="copy the ROM into the app bundle instead of importing it at runtime",
    )
    p.add_argument(
        "--i-own-this-rom",
        action="store_true",
        help="confirm the title is yours to embed (required by --embed-rom)",
    )
    _add_plugin_flags(p)
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("decompress", help="decode (or encode) a BIOS-compressed file")
    p.add_argument("file")
    p.add_argument("-o", "--out", required=True)
    p.add_argument(
        "--compress",
        action="store_true",
        help="compress instead, for putting a modified file back",
    )
    p.add_argument(
        "--blz",
        action="store_true",
        help="treat the file as an ARM9-style BLZ image rather than a BIOS stream",
    )
    p.add_argument(
        "--blz-verbatim",
        type=int,
        default=0,
        help="with --blz --compress, bytes to leave uncompressed at the start",
    )
    p.set_defaults(func=cmd_decompress)

    p = sub.add_parser("narc", help="list or extract a NARC archive")
    p.add_argument("file")
    p.add_argument("-o", "--out", help="extract here instead of listing")
    p.set_defaults(func=cmd_narc)

    p = sub.add_parser("sdat", help="list or extract a sound archive")
    p.add_argument("file")
    p.add_argument("-o", "--out", help="extract here instead of listing")
    p.set_defaults(func=cmd_sdat)

    p = sub.add_parser("gfx", help="render an NCGR sheet with an NCLR palette")
    p.add_argument("ncgr")
    p.add_argument("nclr")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--palette", type=int, default=0, help="palette slot within the NCLR")
    p.set_defaults(func=cmd_gfx)

    p = sub.add_parser(
        "map", help="draft a file pairing between a donor and a target ROM"
    )
    p.add_argument("--from", dest="source", required=True, help="donor ROM or directory")
    p.add_argument("--to", dest="target_rom", required=True, help="target ROM")
    p.add_argument("-o", "--out", required=True, help="mapping file to write")
    p.set_defaults(func=cmd_map)

    p = sub.add_parser(
        "import", help="import material from another title into this one"
    )
    p.add_argument("--from", dest="source", required=True, help="donor ROM or directory")
    p.add_argument("--to", dest="target_rom", required=True, help="target ROM")
    p.add_argument("--map", required=True, help="mapping file from `reatk map`")
    p.add_argument("-o", "--out", help="output directory (not needed with --dry-run)")
    p.add_argument(
        "--emit",
        default="files",
        choices=["files", "apk", "android", "ipa", "ios"],
        help="write the replaced files, or a whole app project around them",
    )
    p.add_argument("--report", help="write a JSON report of what happened")
    p.add_argument("--dry-run", action="store_true", help="decide but write nothing")
    p.add_argument(
        "--force-raw",
        action="store_true",
        help="write a replacement even where the original compression cannot be "
        "reproduced (the game may fail to load it)",
    )
    p.add_argument("--app-name")
    p.add_argument("--bundle-id")
    p.add_argument("--app-version", default="1.0.0")
    p.add_argument("--core", default="melonds")
    p.add_argument("--orientation", default="both", choices=["portrait", "landscape", "both"])
    p.add_argument("--icon-scale", type=int, default=8)
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("plugins", help="list discovered plugins")
    p.add_argument("--plugin-dir", action="append")
    p.set_defaults(func=cmd_plugins)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ParseError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
