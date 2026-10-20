"""Emit an iOS (IPA) project around a title.

The output is an XcodeGen spec plus sources, because a hand-written
``.xcodeproj`` is unreadable in a diff and XcodeGen is how these sideload
projects are normally kept in version control.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..convert.images import scale_rgba, write_png
from ..plugins import PluginContext, PluginRegistry
from . import templates
from .android import xml_escape
from .common import (
    PackageOptions,
    banner_of,
    build_manifest,
    collect_asset_overrides,
    write_manifest,
    write_overrides,
    write_text,
)

ORIENTATIONS = {
    "portrait": ["UIInterfaceOrientationPortrait"],
    "landscape": [
        "UIInterfaceOrientationLandscapeLeft",
        "UIInterfaceOrientationLandscapeRight",
    ],
    "both": [
        "UIInterfaceOrientationPortrait",
        "UIInterfaceOrientationLandscapeLeft",
        "UIInterfaceOrientationLandscapeRight",
    ],
}


def _target_name(app_name: str) -> str:
    cleaned = "".join(ch for ch in app_name if ch.isalnum() or ch in " -_").strip()
    return (cleaned.replace(" ", "") or "ReatkApp")[:48]


def emit_ios(
    rom,
    options: PackageOptions,
    registry: PluginRegistry | None = None,
    ctx: PluginContext | None = None,
) -> Path:
    """Write an XcodeGen project for *rom* under ``options.out_dir``."""
    options.check_embedding()
    registry = registry or PluginRegistry()
    ctx = ctx or PluginContext(options=options.plugin_options)

    manifest = build_manifest(rom, options)
    overrides = collect_asset_overrides(rom, registry, ctx)
    if overrides:
        manifest["overrides"] = {"root": "reatk/overrides", "assets": sorted(overrides)}
    manifest = registry.run_manifest(manifest, ctx)

    app_name = manifest["app"]["name"]
    bundle_id = manifest["app"]["bundle_id"]
    target = _target_name(app_name)
    orientations = ORIENTATIONS.get(options.orientation, ORIENTATIONS["both"])
    one_line_name = app_name.replace("\n", " ")

    from .. import __version__

    values = {
        "APP_NAME": one_line_name,
        # Titles carry quotes and colons; json.dumps gives a valid YAML scalar.
        "APP_NAME_YAML": json.dumps(one_line_name),
        "XML_APP_NAME": xml_escape(one_line_name),
        "TARGET": target,
        "PREFIX": bundle_id.rsplit(".", 1)[0],
        "BUNDLE_ID": bundle_id,
        "VERSION": options.version,
        "ORIENTATIONS": ", ".join(orientations),
        "ORIENTATION_ENTRIES": "\n".join(
            f"        <string>{name}</string>" for name in orientations
        ),
        "CORE": options.core,
        "TOOL_VERSION": __version__,
        "SOURCE": str(getattr(rom, "source", None) or "<memory>"),
        "ROM_NOTE": (
            "4. Nothing else - the ROM is embedded at "
            "`Resources/reatk/game.nds`.\n"
            if options.embed_rom
            else "4. Nothing else: with no embedded ROM the app reads whatever you "
            "put in Documents.\n"
        ),
    }

    root = Path(options.out_dir)
    layout = {
        root / "project.yml": "ios/project.yml",
        root / "Sources" / "AppDelegate.swift": "ios/AppDelegate.swift",
        root / "Sources" / "GameViewController.swift": "ios/GameViewController.swift",
        root / "Sources" / "ControlOverlay.swift": "ios/ControlOverlay.swift",
        root / "Sources" / "EmulatorCore.swift": "ios/EmulatorCore.swift",
        root / "Sources" / "AudioOutput.swift": "ios/AudioOutput.swift",
        root / "Resources" / "Info.plist": "ios/Info.plist",
        root / "README.md": "ios/README.md",
    }
    for destination, template in layout.items():
        write_text(destination, templates.render(template, **values))

    script = write_text(
        root / "scripts" / "build_ipa.sh", templates.render("ios/build_ipa.sh", **values)
    )
    script.chmod(0o755)

    write_manifest(manifest, root / "Resources" / "reatk" / "manifest.json")
    if overrides:
        write_overrides(overrides, root / "Resources" / "reatk" / "overrides")

    banner = banner_of(rom)
    if banner is not None:
        factor = max(1, options.icon_scale)
        write_png(
            root / "Resources" / "AppIcon.png",
            scale_rgba(banner.icon_rgba(), 32, 32, factor),
            32 * factor,
            32 * factor,
        )

    if options.embed_rom:
        target_rom = root / "Resources" / "reatk" / "game.nds"
        target_rom.parent.mkdir(parents=True, exist_ok=True)
        target_rom.write_bytes(rom.data)

    registry.run_project(root, ctx)
    return root
