"""Move material from one title into another.

Taking a sprite sheet or a sound bank out of ROM A and into ROM B is not a
file copy.  Three things usually differ:

* **the path** - no two titles lay their filesystem out the same way, so the
  pairing has to be stated explicitly (or drafted and then corrected);
* **the compression** - if the target file was LZ77 the game will call the
  BIOS decompressor on whatever sits there, so the replacement has to be
  compressed the same way;
* **the encoding** - a sheet has a fixed tile shape, bit depth and palette,
  and the palette is often shared with other sheets, so the imported image
  has to be re-quantised onto the *target's* palette rather than carrying its
  own.

This module does those three things and refuses, loudly, when it cannot: a
substitution that silently breaks the game at runtime is worse than an error.
"""

from __future__ import annotations

import dataclasses
import difflib
import json
from pathlib import Path, PurePosixPath

from ._util import ParseError
from .convert.images import PngError, read_png
from .formats import graphics
from .formats.compression import FORMAT_NAMES, LZ77, compress_lz77, detect, maybe_decompress
from .formats.nds import NdsRom

SCHEMA = 1
IMAGE_SUFFIXES = {".png", ".ncgr", ".rgcn"}
PALETTE_SUFFIXES = {".nclr", ".rlcn"}


class TransferError(RuntimeError):
    """Raised when an import cannot be done safely."""


@dataclasses.dataclass
class MappingEntry:
    """One donor file and where it goes in the target."""

    source: str
    target: str
    mode: str = "auto"  # auto | raw | image
    note: str | None = None

    def as_dict(self) -> dict:
        out = {"from": self.source, "to": self.target, "mode": self.mode}
        if self.note:
            out["note"] = self.note
        return out


@dataclasses.dataclass
class Mapping:
    entries: list[MappingEntry]
    source_label: str | None = None

    @classmethod
    def load(cls, path: str | Path) -> "Mapping":
        raw = json.loads(Path(path).read_text("utf-8"))
        if raw.get("schema") != SCHEMA:
            raise TransferError(
                f"{path}: mapping schema {raw.get('schema')!r}, expected {SCHEMA}"
            )
        entries = []
        for item in raw.get("entries", []):
            if "from" not in item or "to" not in item:
                raise TransferError(f"{path}: an entry is missing 'from' or 'to'")
            entries.append(
                MappingEntry(
                    source=item["from"],
                    target=item["to"],
                    mode=item.get("mode", "auto"),
                    note=item.get("note"),
                )
            )
        if not entries:
            raise TransferError(f"{path}: the mapping has no entries")
        return cls(entries, raw.get("source"))

    def save(self, path: str | Path, unmatched: dict | None = None) -> Path:
        path = Path(path)
        document = {
            "schema": SCHEMA,
            "source": self.source_label,
            "entries": [entry.as_dict() for entry in self.entries],
        }
        if unmatched:
            document["unmatched"] = unmatched
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", "utf-8")
        return path


# -- sources -------------------------------------------------------------


class DonorRom:
    """Material taken from another cartridge image."""

    def __init__(self, rom: NdsRom):
        self.rom = rom
        self.fs = rom.filesystem()
        self._by_path = {str(entry.path): entry for entry in self.fs.entries()}

    @classmethod
    def from_file(cls, path: str | Path) -> "DonorRom":
        return cls(NdsRom.from_file(path))

    def paths(self) -> list[str]:
        return list(self._by_path)

    def read(self, path: str) -> bytes:
        try:
            entry = self._by_path[path]
        except KeyError:
            raise TransferError(f"the donor ROM has no {path}") from None
        return self.fs.read(entry)


class DonorDirectory:
    """Material taken from a directory, for example an earlier extraction."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        if not self.root.is_dir():
            raise TransferError(f"{self.root} is not a directory")

    def paths(self) -> list[str]:
        return [
            "/" + str(path.relative_to(self.root)).replace("\\", "/")
            for path in sorted(self.root.rglob("*"))
            if path.is_file()
        ]

    def read(self, path: str) -> bytes:
        target = (self.root / path.lstrip("/")).resolve()
        if self.root.resolve() not in target.parents:
            raise TransferError(f"{path} escapes the donor directory")
        if not target.is_file():
            raise TransferError(f"the donor directory has no {path}")
        return target.read_bytes()


def open_donor(path: str | Path):
    """A ROM image or a directory, whichever *path* is."""
    path = Path(path)
    return DonorDirectory(path) if path.is_dir() else DonorRom.from_file(path)


# -- drafting ------------------------------------------------------------


def _kind(path: str) -> str:
    return PurePosixPath(path).suffix.lower()


def draft_mapping(donor, target: NdsRom, limit_per_file: int = 1) -> tuple[Mapping, dict]:
    """Guess a mapping by file name and size.

    A draft is a starting point, not an answer: it pairs only files that share
    an extension, and scores by how close their names and sizes are.  Review it
    before running an import.
    """
    target_entries = {str(entry.path): entry for entry in target.filesystem().entries()}
    donor_paths = donor.paths()

    by_kind: dict[str, list[str]] = {}
    for path in donor_paths:
        by_kind.setdefault(_kind(path), []).append(path)

    entries: list[MappingEntry] = []
    unmatched_targets: list[str] = []
    for target_path, target_entry in sorted(target_entries.items()):
        candidates = by_kind.get(_kind(target_path), [])
        if not candidates:
            unmatched_targets.append(target_path)
            continue
        target_stem = PurePosixPath(target_path).stem.lower()

        scored = []
        for candidate in candidates:
            stem = PurePosixPath(candidate).stem.lower()
            name_score = difflib.SequenceMatcher(None, target_stem, stem).ratio()
            donor_size = len(donor.read(candidate))
            size_score = min(donor_size, target_entry.size) / max(
                donor_size, target_entry.size, 1
            )
            scored.append((0.7 * name_score + 0.3 * size_score, candidate, name_score))
        scored.sort(reverse=True)

        for score, candidate, name_score in scored[:limit_per_file]:
            entries.append(
                MappingEntry(
                    source=candidate,
                    target=target_path,
                    mode="auto",
                    note=f"drafted, confidence {score:.2f}"
                    + (" (names barely match)" if name_score < 0.4 else ""),
                )
            )
        if not scored:
            unmatched_targets.append(target_path)

    matched_sources = {entry.source for entry in entries}
    unmatched = {
        "donor_files_unused": sorted(set(donor_paths) - matched_sources),
        "target_files_without_a_source": unmatched_targets,
    }
    label = str(getattr(getattr(donor, "rom", None), "source", None) or getattr(donor, "root", ""))
    return Mapping(entries, label or None), unmatched


# -- applying ------------------------------------------------------------


@dataclasses.dataclass
class TransferResult:
    """What an import produced, before anything is written."""

    payloads: dict[str, bytes] = dataclasses.field(default_factory=dict)
    notes: list[str] = dataclasses.field(default_factory=list)
    skipped: list[tuple[str, str]] = dataclasses.field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "imported": sorted(self.payloads),
            "notes": self.notes,
            "skipped": [{"target": path, "reason": reason} for path, reason in self.skipped],
        }


def _target_palette(target: NdsRom, target_path: str) -> graphics.Palette | None:
    """The palette sitting next to *target_path* inside the target ROM."""
    directory = PurePosixPath(target_path).parent
    for entry in target.filesystem().entries():
        if entry.path.parent != directory:
            continue
        payload, _ = maybe_decompress(target.filesystem().read(entry))
        if entry.path.suffix.lower() in PALETTE_SUFFIXES or payload[:4] == b"RLCN":
            try:
                return graphics.read_nclr(payload)
            except ParseError:
                continue
    return None


def _source_image(donor, source_path: str) -> tuple[bytes, int, int]:
    """``(rgba, width, height)`` for a donor image, PNG or NCGR."""
    suffix = _kind(source_path)
    if suffix == ".png":
        if isinstance(donor, DonorDirectory):
            return read_png(donor.root / source_path.lstrip("/"))
        raise TransferError(
            f"{source_path}: PNG sources have to come from a directory donor"
        )
    payload, _ = maybe_decompress(donor.read(source_path))
    sheet = graphics.read_ncgr(payload)
    palette_path = str(PurePosixPath(source_path).with_suffix(".nclr"))
    try:
        palette = graphics.read_nclr(maybe_decompress(donor.read(palette_path))[0])
    except (TransferError, ParseError):
        raise TransferError(
            f"{source_path}: no palette beside it in the donor "
            f"(looked for {palette_path}); export it as PNG instead"
        ) from None
    return sheet.to_rgba(palette), sheet.width, sheet.height


def _palette_overrides(mapping: "Mapping", donor) -> dict[str, graphics.Palette]:
    """Palettes the mapping itself replaces, keyed by target directory.

    A sheet has to be quantised onto the palette that will actually be in place
    at runtime.  If the mapping swaps the palette too, that is the donor's, not
    the target's.
    """
    out: dict[str, graphics.Palette] = {}
    for entry in mapping.entries:
        if _kind(entry.target) not in PALETTE_SUFFIXES:
            continue
        try:
            payload, _ = maybe_decompress(donor.read(entry.source))
            out[str(PurePosixPath(entry.target).parent)] = graphics.read_nclr(payload)
        except (TransferError, ParseError):
            continue
    return out


def _convert_image(
    donor,
    source_path: str,
    original: bytes,
    target: NdsRom,
    target_path: str,
    palette_overrides: dict[str, graphics.Palette] | None = None,
) -> tuple[bytes, str]:
    """Re-encode a donor image into the target sheet's shape and palette."""
    sheet = graphics.read_ncgr(original)
    rgba, width, height = _source_image(donor, source_path)
    if (width, height) != (sheet.width, sheet.height):
        raise TransferError(
            f"{target_path} is {sheet.width}x{sheet.height} but {source_path} is "
            f"{width}x{height}; resize the source before importing"
        )
    directory = str(PurePosixPath(target_path).parent)
    palette = (palette_overrides or {}).get(directory)
    which = "the donor palette this mapping also imports"
    if palette is None:
        palette = _target_palette(target, target_path)
        which = "the target's own palette" if palette else "a palette built from the image"
    encoded, _ = graphics.encode_sheet(
        rgba, width, height, bit_depth=sheet.bit_depth, palette=palette
    )
    return encoded, which


def apply_mapping(
    mapping: Mapping,
    donor,
    target: NdsRom,
    force_raw: bool = False,
) -> TransferResult:
    """Build the replacement payloads described by *mapping*.

    Nothing is written here; the caller decides whether the payloads go into an
    extraction, an app bundle, or a dry-run report.
    """
    result = TransferResult()
    target_fs = target.filesystem()
    target_entries = {str(entry.path): entry for entry in target_fs.entries()}
    palette_overrides = _palette_overrides(mapping, donor)

    for entry in mapping.entries:
        if entry.target not in target_entries:
            result.skipped.append((entry.target, "the target ROM has no such file"))
            continue
        original = target_fs.read(target_entries[entry.target])
        codec = detect(original)
        decoded, _ = maybe_decompress(original)

        mode = entry.mode
        if mode == "auto":
            mode = (
                "image"
                if decoded[:4] in (b"RGCN", b"NCGR") and _kind(entry.source) in IMAGE_SUFFIXES
                else "raw"
            )

        try:
            if mode == "image":
                payload, which = _convert_image(
                    donor,
                    entry.source,
                    decoded,
                    target,
                    entry.target,
                    palette_overrides,
                )
                result.notes.append(
                    f"{entry.target}: re-encoded from {entry.source} onto {which}"
                )
            else:
                payload = donor.read(entry.source)
                if codec is not None:
                    # The donor file may itself be compressed; compare raw forms.
                    payload, _ = maybe_decompress(payload)
        except (TransferError, ParseError, PngError, ValueError) as exc:
            result.skipped.append((entry.target, str(exc)))
            continue

        if codec is not None:
            if codec == FORMAT_NAMES[LZ77]:
                payload = compress_lz77(payload)
                result.notes.append(f"{entry.target}: re-compressed as lz77")
            elif force_raw:
                result.notes.append(
                    f"{entry.target}: left uncompressed although the original was "
                    f"{codec} (--force-raw); the game may fail to load it"
                )
            else:
                result.skipped.append(
                    (
                        entry.target,
                        f"the original is {codec}-compressed and this toolkit can only "
                        "re-compress lz77; pass --force-raw to write it uncompressed "
                        "anyway",
                    )
                )
                continue

        result.payloads[entry.target] = payload

    return result
