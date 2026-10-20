"""Command line entry point: ``reatk <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from ._util import ParseError, human_size
from .analyze import analyze, extract_strings
from .convert import extract_all
from .formats import detect, open_rom
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

    written = extract_all(rom, args.out, icon_scale=args.icon_scale)
    for group, paths in written.items():
        print(f"{group}: {len(paths)} file(s)")
    for line in ctx.log:
        print(f"  {line}")
    print(f"wrote {args.out}")
    return 0


def cmd_convert(args) -> int:
    rom = open_rom(args.rom)
    registry = _load_registry(args)
    plugin_options = _parse_sets(args.set)
    ctx = PluginContext(options=plugin_options)
    registry.run_rom(rom, ctx)

    options = PackageOptions(
        out_dir=Path(args.out),
        app_name=args.app_name,
        bundle_id=args.bundle_id,
        version=args.app_version,
        core=args.core,
        embed_rom=args.embed_rom,
        rom_owned=args.i_own_this_rom,
        icon_scale=args.icon_scale,
        orientation=args.orientation,
        plugin_options=plugin_options,
    )
    try:
        root = emit(args.target, rom, options, registry=registry, ctx=ctx)
    except OwnershipError as exc:
        print(f"refusing to embed: {exc}", file=sys.stderr)
        return 2

    if registry.names():
        print(f"plugins: {', '.join(registry.names())}")
    for line in ctx.log:
        print(f"  {line}")
    print(f"wrote {args.target} project to {root}")
    print(f"next steps: {root / 'README.md'}")
    return 0


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
