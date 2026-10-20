"""Emit an Android (APK) project around a title.

The project text lives in ``reatk/package/templates/android``; this module
decides the values that go into it and what else has to be written (icons, the
manifest, imported overrides, an embedded ROM).
"""

from __future__ import annotations

from pathlib import Path

from ..convert.images import scale_rgba, write_png
from ..plugins import PluginContext, PluginRegistry
from . import templates
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
    "portrait": "portrait",
    "landscape": "landscape",
    "both": "fullSensor",
}
ICON_DENSITIES = (("mdpi", 1), ("hdpi", 2), ("xhdpi", 3), ("xxhdpi", 4))


def _gradle_name(app_name: str) -> str:
    """Gradle wants a plain identifier, not a display name."""
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in app_name).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned or "reatk-app"


def _namespace(bundle_id: str) -> str:
    return ".".join(part for part in bundle_id.split(".") if part)


def xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def emit_android(
    rom,
    options: PackageOptions,
    registry: PluginRegistry | None = None,
    ctx: PluginContext | None = None,
) -> Path:
    """Write a Gradle project for *rom* under ``options.out_dir``."""
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
    namespace = _namespace(bundle_id)
    one_line_name = app_name.replace("\n", " ")

    root = Path(options.out_dir)
    app = root / "app"
    main = app / "src" / "main"
    java_dir = main / "java" / Path(*namespace.split("."))

    from .. import __version__

    values = {
        "APP_NAME": one_line_name,
        "XML_APP_NAME": xml_escape(one_line_name),
        "PROJECT_NAME": _gradle_name(one_line_name),
        "BUNDLE_ID": bundle_id,
        "NAMESPACE": namespace,
        "NAMESPACE_PATH": namespace.replace(".", "/"),
        # JNI symbol names encode the package: a.b.C -> Java_a_b_C_method.
        "JNI_PREFIX": namespace.replace("_", "_1").replace(".", "_"),
        "VERSION": options.version,
        "ORIENTATION": ORIENTATIONS.get(options.orientation, "fullSensor"),
        "CORE": options.core,
        "TOOL_VERSION": __version__,
        "SOURCE": str(getattr(rom, "source", None) or "<memory>"),
        "ROM_NOTE": (
            "5. Nothing else - the ROM is embedded at "
            "`app/src/main/assets/reatk/game.nds`.\n"
            if options.embed_rom
            else "5. **A ROM at runtime.** Nothing is embedded: the app asks the "
            "player to open one from device storage.\n"
        ),
    }

    layout = {
        root / "settings.gradle.kts": "android/settings.gradle.kts",
        app / "build.gradle.kts": "android/build.gradle.kts",
        main / "AndroidManifest.xml": "android/AndroidManifest.xml",
        main / "res" / "values" / "strings.xml": "android/strings.xml",
        java_dir / "MainActivity.kt": "android/MainActivity.kt",
        java_dir / "EmuCore.kt": "android/EmuCore.kt",
        java_dir / "EmuView.kt": "android/EmuView.kt",
        java_dir / "ControlOverlay.kt": "android/ControlOverlay.kt",
        main / "cpp" / "reatk_host.c": "android/reatk_host.c",
        main / "cpp" / "CMakeLists.txt": "android/CMakeLists.txt",
        root / "README.md": "android/README.md",
    }
    for target, template in layout.items():
        write_text(target, templates.render(template, **values))

    banner = banner_of(rom)
    if banner is not None:
        for density, factor in ICON_DENSITIES:
            size = 32 * factor
            write_png(
                main / "res" / f"mipmap-{density}" / "ic_launcher.png",
                scale_rgba(banner.icon_rgba(), 32, 32, factor),
                size,
                size,
            )

    write_manifest(manifest, main / "assets" / "reatk" / "manifest.json")
    if overrides:
        write_overrides(overrides, main / "assets" / "reatk" / "overrides")
    if options.embed_rom:
        target = main / "assets" / "reatk" / "game.nds"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(rom.data)

    registry.run_project(root, ctx)
    return root
