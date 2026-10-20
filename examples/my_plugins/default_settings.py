"""Example plugin: see docs/plugins.md.

Run it with:

    reatk convert game.nds -t apk -o out \
        --plugin-dir examples/my_plugins --plugin default-settings --set frameskip=1
"""

from __future__ import annotations

from reatk.plugins import Plugin, PluginContext


class DefaultSettings(Plugin):
    name = "default-settings"
    description = "write startup defaults into the generated app"

    def on_manifest(self, manifest: dict, ctx: PluginContext) -> dict:
        manifest["settings"] = {
            "frameskip": int(ctx.option("frameskip", 0)),
            "screen_layout": ctx.option("layout", "stacked"),
        }
        return manifest

    def on_project(self, project_dir, ctx: PluginContext) -> None:
        target = project_dir / "docs" / "settings.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "Startup defaults for this build come from the default-settings "
            "plugin; edit reatk/manifest.json to change them.\n",
            "utf-8",
        )
        ctx.note("default-settings: wrote docs/settings.md")
