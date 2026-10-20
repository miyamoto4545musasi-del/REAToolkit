"""Generate an on-screen control layout for the packaged app.

A DS title assumes a d-pad, two shoulder buttons and a stylus; a phone has a
touchscreen and nothing else, so the packaging stage needs a layout to draw.
This plugin writes one into the manifest, portrait and landscape, and lets you
override it per title with ``--set layout=my_layout.json``.

Coordinates are fractions of the screen so one layout survives every DPI.
"""

from __future__ import annotations

import json
from pathlib import Path

from ...plugins import Plugin, PluginContext

DEFAULT_LAYOUT = {
    "portrait": {
        "screens": {"top": [0.0, 0.06, 1.0, 0.44], "bottom": [0.125, 0.5, 0.75, 0.32]},
        "buttons": [
            {"id": "dpad", "shape": "dpad", "rect": [0.03, 0.70, 0.28, 0.21]},
            {"id": "a", "shape": "round", "rect": [0.84, 0.74, 0.12, 0.09]},
            {"id": "b", "shape": "round", "rect": [0.75, 0.82, 0.12, 0.09]},
            {"id": "x", "shape": "round", "rect": [0.84, 0.64, 0.12, 0.09]},
            {"id": "y", "shape": "round", "rect": [0.66, 0.74, 0.12, 0.09]},
            {"id": "l", "shape": "pill", "rect": [0.02, 0.60, 0.20, 0.06]},
            {"id": "r", "shape": "pill", "rect": [0.78, 0.60, 0.20, 0.06]},
            {"id": "start", "shape": "pill", "rect": [0.56, 0.93, 0.18, 0.05]},
            {"id": "select", "shape": "pill", "rect": [0.26, 0.93, 0.18, 0.05]},
        ],
    },
    "landscape": {
        "screens": {"top": [0.26, 0.0, 0.24, 1.0], "bottom": [0.51, 0.0, 0.24, 1.0]},
        "buttons": [
            {"id": "dpad", "shape": "dpad", "rect": [0.02, 0.38, 0.20, 0.50]},
            {"id": "a", "shape": "round", "rect": [0.90, 0.52, 0.08, 0.14]},
            {"id": "b", "shape": "round", "rect": [0.84, 0.70, 0.08, 0.14]},
            {"id": "x", "shape": "round", "rect": [0.84, 0.34, 0.08, 0.14]},
            {"id": "y", "shape": "round", "rect": [0.78, 0.52, 0.08, 0.14]},
            {"id": "l", "shape": "pill", "rect": [0.02, 0.04, 0.16, 0.08]},
            {"id": "r", "shape": "pill", "rect": [0.82, 0.04, 0.16, 0.08]},
            {"id": "start", "shape": "pill", "rect": [0.82, 0.90, 0.16, 0.07]},
            {"id": "select", "shape": "pill", "rect": [0.02, 0.90, 0.16, 0.07]},
        ],
    },
    "stylus": {"mode": "direct", "target": "bottom", "pressure": 1.0},
}


class TouchControlsPlugin(Plugin):
    name = "touch-controls"
    description = "write an on-screen control layout into the app manifest"

    def on_manifest(self, manifest: dict, ctx: PluginContext) -> dict:
        override = ctx.option("layout")
        layout = (
            json.loads(Path(override).read_text("utf-8")) if override else DEFAULT_LAYOUT
        )
        manifest["controls"] = layout
        ctx.note(
            "touch-controls: "
            + ("custom layout " + str(override) if override else "default layout")
        )
        return manifest
