"""ROM container parsers."""

from __future__ import annotations

from pathlib import Path

from .gba import GbaRom
from .nds import NdsRom

__all__ = ["GbaRom", "NdsRom", "open_rom", "detect"]

_LOADERS = {"nds": NdsRom, "srl": NdsRom, "gba": GbaRom}


def detect(path: str | Path) -> str:
    """Return ``"nds"`` or ``"gba"`` for *path*, sniffing content before extension."""
    path = Path(path)
    with path.open("rb") as fh:
        head = fh.read(0xC0)
    if NdsRom.looks_like(head):
        return "nds"
    if GbaRom.looks_like(head):
        return "gba"
    suffix = path.suffix.lower().lstrip(".")
    if suffix in _LOADERS:
        return "gba" if suffix == "gba" else "nds"
    raise ValueError(f"{path}: unrecognised ROM container")


def open_rom(path: str | Path):
    """Parse *path* into the matching ROM object."""
    return _LOADERS[detect(path)].from_file(path)
