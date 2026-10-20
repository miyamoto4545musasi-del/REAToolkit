"""Apply an IPS patch to the image before anything else reads it.

Enable with ``--plugin ips-patch --set ips=path/to/patch.ips``.
"""

from __future__ import annotations

from pathlib import Path

from ...plugins import Plugin, PluginContext


def apply_ips(data: bytes, patch: bytes) -> bytes:
    """Apply a classic IPS patch (big-endian 3-byte offsets, RLE records)."""
    if patch[:5] != b"PATCH":
        raise ValueError("not an IPS patch (missing PATCH magic)")
    out = bytearray(data)
    cursor = 5
    while True:
        marker = patch[cursor : cursor + 3]
        if marker == b"EOF" or len(marker) < 3:
            break
        offset = int.from_bytes(marker, "big")
        cursor += 3
        size = int.from_bytes(patch[cursor : cursor + 2], "big")
        cursor += 2
        if size == 0:  # RLE record
            run = int.from_bytes(patch[cursor : cursor + 2], "big")
            cursor += 2
            value = patch[cursor : cursor + 1]
            cursor += 1
            chunk = value * run
        else:
            chunk = patch[cursor : cursor + size]
            cursor += size
        if offset + len(chunk) > len(out):
            out.extend(b"\x00" * (offset + len(chunk) - len(out)))
        out[offset : offset + len(chunk)] = chunk
    return bytes(out)


class IpsPatchPlugin(Plugin):
    name = "ips-patch"
    description = "apply an IPS patch to the ROM image in memory"

    def on_rom(self, rom, ctx: PluginContext) -> None:
        patch_path = ctx.option("ips")
        if not patch_path:
            return
        patch = Path(patch_path).read_bytes()
        rom.data = apply_ips(rom.data, patch)
        ctx.note(f"ips-patch: applied {Path(patch_path).name}")
