"""Emit an iOS (IPA) project around a title.

The output is an XcodeGen spec plus sources, because a hand-written
`.xcodeproj` is unreadable in a diff and XcodeGen is the usual way these
sideload projects are kept in version control.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..convert.images import scale_rgba, write_png
from ..plugins import PluginContext, PluginRegistry
from .common import (
    PackageOptions,
    banner_of,
    build_manifest,
    collect_asset_overrides,
    write_manifest,
    write_overrides,
    write_text,
)

PROJECT_YML = """name: {target}
options:
  bundleIdPrefix: {prefix}
  deploymentTarget:
    iOS: "14.0"
settings:
  base:
    # Sideloading only: no team is set, sign with your own identity.
    CODE_SIGN_STYLE: Manual
    ENABLE_BITCODE: NO
targets:
  {target}:
    type: application
    platform: iOS
    sources:
      - Sources
      - path: Resources
        type: folder
    info:
      path: Resources/Info.plist
      properties:
        CFBundleDisplayName: {app_name_yaml}
        CFBundleShortVersionString: "{version}"
        CFBundleVersion: "1"
        UILaunchStoryboardName: ""
        UIRequiresFullScreen: true
        UISupportedInterfaceOrientations: [{orientations}]
        # JIT-capable cores need this; harmless for interpreter cores.
        UIFileSharingEnabled: true
        LSSupportsOpeningDocumentsInPlace: true
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: {bundle_id}
        OTHER_LDFLAGS: "$(inherited)"
    dependencies: []
"""

APP_DELEGATE = """import UIKit

@main
class AppDelegate: UIResponder, UIApplicationDelegate {

    var window: UIWindow?

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]?
    ) -> Bool {
        let window = UIWindow(frame: UIScreen.main.bounds)
        window.rootViewController = GameViewController()
        window.makeKeyAndVisible()
        self.window = window
        return true
    }
}
"""

GAME_VC = '''import UIKit

/// Host screen for the converted title.
///
/// Reads `Resources/reatk/manifest.json`, lays out the on-screen controls the
/// `touch-controls` plugin recorded there, and drives the core through
/// `EmuCore`. Video presentation is left as a `CALayer` you can swap for a
/// Metal view once a core is wired up.
final class GameViewController: UIViewController {

    private var manifest: [String: Any] = [:]
    private let status = UILabel()

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black

        manifest = Self.loadManifest()

        status.numberOfLines = 0
        status.textColor = .white
        status.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        status.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(status)
        NSLayoutConstraint.activate([
            status.leadingAnchor.constraint(equalTo: view.layoutMarginsGuide.leadingAnchor),
            status.trailingAnchor.constraint(equalTo: view.layoutMarginsGuide.trailingAnchor),
            status.centerYAnchor.constraint(equalTo: view.centerYAnchor),
        ])

        let app = manifest["app"] as? [String: Any] ?? [:]
        let rom = manifest["rom"] as? [String: Any] ?? [:]
        let embedded = rom["embedded"] as? Bool ?? false

        status.text = """
        \\(app["name"] as? String ?? "Untitled")
        core: \\((manifest["core"] as? [String: Any])?["name"] as? String ?? "none")
        rom: \\(embedded ? (rom["path"] as? String ?? "?") : "import from Files at runtime")

        Place a libretro core in Frameworks/, then call
        EmuCore.shared.open(core:rom:) and present its frames here.
        """

        installControls()
    }

    /// Build buttons from the manifest layout, in screen fractions.
    private func installControls() {
        guard let controls = manifest["controls"] as? [String: Any] else { return }
        let key = view.bounds.width > view.bounds.height ? "landscape" : "portrait"
        guard
            let orientation = controls[key] as? [String: Any],
            let buttons = orientation["buttons"] as? [[String: Any]]
        else { return }

        for spec in buttons {
            guard let rect = spec["rect"] as? [Double], rect.count == 4 else { continue }
            let button = UIButton(type: .custom)
            button.setTitle((spec["id"] as? String)?.uppercased(), for: .normal)
            button.titleLabel?.font = .systemFont(ofSize: 11, weight: .semibold)
            button.backgroundColor = UIColor.white.withAlphaComponent(0.12)
            button.layer.cornerRadius = spec["shape"] as? String == "round" ? 24 : 8
            button.frame = CGRect(
                x: rect[0] * view.bounds.width,
                y: rect[1] * view.bounds.height,
                width: rect[2] * view.bounds.width,
                height: rect[3] * view.bounds.height
            )
            view.addSubview(button)
        }
    }

    private static func loadManifest() -> [String: Any] {
        guard
            let url = Bundle.main.url(forResource: "manifest", withExtension: "json", subdirectory: "reatk"),
            let data = try? Data(contentsOf: url),
            let parsed = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return [:] }
        return parsed
    }
}
'''

EMU_CORE_SWIFT = '''import Foundation

/// Swift side of the libretro host.
///
/// iOS cannot `dlopen` an arbitrary shared object in a sideloaded app, so a
/// core has to be linked in as a static library or an embedded framework and
/// its `retro_*` symbols declared here. That link step is yours; this type
/// exists so the call sites in `GameViewController` are already in place.
final class EmuCore {

    static let shared = EmuCore()

    enum CoreError: Error {
        case notLinked
        case loadFailed(String)
    }

    private(set) var isRunning = false

    /// Open `rom` with the statically linked core.
    func open(core: String, rom: String) throws {
        // Replace with calls to the linked core:
        //   retro_set_environment(environmentCallback)
        //   retro_init()
        //   retro_load_game(&info)
        throw CoreError.notLinked
    }

    /// Advance one frame. Returns RGBA bytes plus dimensions.
    func runFrame() -> (pixels: Data, width: Int, height: Int)? {
        guard isRunning else { return nil }
        return nil
    }

    func setButtons(_ mask: UInt16) {}

    /// Stylus position in bottom-screen pixels, or nil to lift it.
    func setPointer(_ point: CGPoint?) {}

    func close() {
        isRunning = false
    }
}
'''

INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDisplayName</key><string>{app_name}</string>
    <key>CFBundleExecutable</key><string>$(EXECUTABLE_NAME)</string>
    <key>CFBundleIdentifier</key><string>{bundle_id}</string>
    <key>CFBundleName</key><string>$(PRODUCT_NAME)</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>{version}</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>UIRequiresFullScreen</key><true/>
    <key>UIFileSharingEnabled</key><true/>
    <key>LSSupportsOpeningDocumentsInPlace</key><true/>
    <key>UISupportedInterfaceOrientations</key>
    <array>
{orientation_entries}
    </array>
</dict>
</plist>
"""

BUILD_SCRIPT = """#!/bin/sh
# Build an unsigned .ipa for sideloading with your own tooling.
#
# Requires: xcodegen, Xcode command line tools, and your own signing identity.
set -eu

cd "$(dirname "$0")/.."
xcodegen generate

xcodebuild \\
  -project "{target}.xcodeproj" \\
  -scheme "{target}" \\
  -configuration Release \\
  -sdk iphoneos \\
  -derivedDataPath build \\
  CODE_SIGNING_ALLOWED=NO \\
  build

rm -rf build/ipa && mkdir -p build/ipa/Payload
cp -R "build/Build/Products/Release-iphoneos/{target}.app" build/ipa/Payload/
cd build/ipa && zip -qry "../{target}.ipa" Payload
echo "wrote build/{target}.ipa (unsigned - sign it before installing)"
"""

PROJECT_README = """# {app_name} (iOS)

Generated by REAToolkit {version_tool} from `{source}`.

## What is here

    project.yml                     XcodeGen spec
    Sources/                        app delegate, host view controller, core shim
    Resources/reatk/manifest.json   title metadata + control layout
    Resources/AppIcon.png           icon converted from the DS banner
    scripts/build_ipa.sh            xcodegen + xcodebuild + Payload/ zip

## What you have to add

1. **A core.** iOS will not `dlopen` one in a sideloaded app, so link a
   libretro core as a static library or embedded framework and declare its
   `retro_*` symbols in `Sources/EmuCore.swift`. Nothing is bundled here.
2. **A signing identity.** `scripts/build_ipa.sh` produces an *unsigned* IPA;
   sign it yourself for your own device.
{rom_note}

## Build

    sh scripts/build_ipa.sh

## Legal

See `LEGAL.md` in the toolkit. Keep the result on hardware you own, and do not
redistribute an IPA containing a ROM or BIOS you do not hold the rights to.
"""

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
        manifest["overrides"] = {
            "root": "reatk/overrides",
            "assets": sorted(overrides),
        }
    manifest = registry.run_manifest(manifest, ctx)
    app_name = manifest["app"]["name"]
    bundle_id = manifest["app"]["bundle_id"]
    target = _target_name(app_name)
    orientations = ORIENTATIONS.get(options.orientation, ORIENTATIONS["both"])

    root = Path(options.out_dir)
    write_text(
        root / "project.yml",
        PROJECT_YML.format(
            target=target,
            prefix=bundle_id.rsplit(".", 1)[0],
            # Titles carry quotes and colons; json.dumps gives valid YAML scalars.
            app_name_yaml=json.dumps(app_name.replace("\n", " ")),
            version=options.version,
            bundle_id=bundle_id,
            orientations=", ".join(orientations),
        ),
    )
    write_text(root / "Sources" / "AppDelegate.swift", APP_DELEGATE)
    write_text(root / "Sources" / "GameViewController.swift", GAME_VC)
    write_text(root / "Sources" / "EmuCore.swift", EMU_CORE_SWIFT)
    write_text(
        root / "Resources" / "Info.plist",
        INFO_PLIST.format(
            app_name=_xml_escape(app_name.replace("\n", " ")),
            bundle_id=bundle_id,
            version=options.version,
            orientation_entries="\n".join(
                f"        <string>{name}</string>" for name in orientations
            ),
        ),
    )
    script = write_text(root / "scripts" / "build_ipa.sh", BUILD_SCRIPT.format(target=target))
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

    rom_note = (
        "3. Nothing else - the ROM is embedded at `Resources/reatk/game.nds`.\n"
        if options.embed_rom
        else "3. **A ROM at runtime.** Nothing is embedded: the app opens one "
        "from Files (`UIFileSharingEnabled` is on).\n"
    )
    if options.embed_rom:
        target_rom = root / "Resources" / "reatk" / "game.nds"
        target_rom.parent.mkdir(parents=True, exist_ok=True)
        target_rom.write_bytes(rom.data)

    from .. import __version__

    write_text(
        root / "README.md",
        PROJECT_README.format(
            app_name=app_name,
            version_tool=__version__,
            source=getattr(rom, "source", None) or "<memory>",
            rom_note=rom_note,
        ),
    )

    registry.run_project(root, ctx)
    return root


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
