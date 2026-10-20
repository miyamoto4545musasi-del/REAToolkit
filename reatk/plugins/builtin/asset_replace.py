"""Swap embedded assets for files from a directory.

Enable with ``--plugin asset-replace --set assets=./my_mod``.  A file at
``./my_mod/data/font/main.nftr`` replaces the ROM's ``/data/font/main.nftr``.

Sizes do not have to match: during ``extract`` the replacement is written in
place of the original, and during ``convert`` it is copied into the app bundle
under ``reatk/overrides/`` and listed in the manifest, so the runtime loads it
instead of the copy inside the image.
"""

from __future__ import annotations

from pathlib import Path

from ...plugins import Plugin, PluginContext


class AssetReplacePlugin(Plugin):
    name = "asset-replace"
    description = "override NitroFS files from a directory of replacements"

    def on_rom(self, rom, ctx: PluginContext) -> None:
        root = ctx.option("assets")
        ctx.state["asset_root"] = Path(root) if root else None
        ctx.state.setdefault("asset_overrides", [])

    def on_asset(self, entry, data: bytes, ctx: PluginContext) -> bytes | None:
        root = ctx.state.get("asset_root")
        if root is None:
            return None
        candidate = root / str(entry.path).lstrip("/")
        if not candidate.is_file():
            return None
        ctx.state["asset_overrides"].append(str(entry.path))
        ctx.note(f"asset-replace: {entry.path} <- {candidate}")
        return candidate.read_bytes()
