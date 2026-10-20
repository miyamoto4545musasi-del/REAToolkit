"""Import material from another title.

Enable with::

    --plugin cross-import --set from=donor.nds --set map=mapping.json

The mapping says which donor file replaces which target file; the engine in
:mod:`reatk.transfer` handles re-compression and re-encoding, and anything it
cannot do safely is reported rather than written.  Add ``--set force-raw=1``
to write a replacement even where the original compression cannot be
reproduced.
"""

from __future__ import annotations

from ...plugins import Plugin, PluginContext


class CrossImportPlugin(Plugin):
    name = "cross-import"
    description = "replace files with material from another ROM or directory"

    def on_rom(self, rom, ctx: PluginContext) -> None:
        from ...transfer import Mapping, apply_mapping, open_donor

        donor_path = ctx.option("from")
        mapping_path = ctx.option("map")
        if not donor_path or not mapping_path:
            return

        result = apply_mapping(
            Mapping.load(mapping_path),
            open_donor(donor_path),
            rom,
            force_raw=bool(ctx.option("force-raw")),
        )
        ctx.state["cross_import"] = result.payloads
        for note in result.notes:
            ctx.note(f"cross-import: {note}")
        for path, reason in result.skipped:
            ctx.note(f"cross-import: skipped {path} - {reason}")

    def on_asset(self, entry, data: bytes, ctx: PluginContext) -> bytes | None:
        return ctx.state.get("cross_import", {}).get(str(entry.path))
