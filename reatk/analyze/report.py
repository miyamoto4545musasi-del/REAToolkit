"""Inspection passes over a parsed ROM.

Nothing here writes to the image.  Each pass returns findings so the CLI, the
JSON report and plugins all see the same structure.
"""

from __future__ import annotations

import collections
import dataclasses
import math
import re

from .._util import human_size
from ..formats.compression import detect as detect_compression
from ..formats.narc import Narc
from ..formats.nds import NdsRom
from ..formats.sdat import Sdat

PRINTABLE = re.compile(rb"[\x20-\x7E]{4,}")
# Markers that commonly survive into a retail build.
LEFTOVER_MARKERS = (
    b"NITRO-SDK",
    b"TwlSDK",
    b"/home/",
    b"C:\\",
    b"\\projects\\",
    b"DEBUG",
    b"assert",
    b".c\x00",
    b"svn",
    b".pdb",
)


@dataclasses.dataclass
class Finding:
    """One observation, severity used only for ordering in the report."""

    kind: str
    message: str
    severity: str = "info"
    detail: dict | None = None

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "message": self.message,
            "severity": self.severity,
            "detail": self.detail or {},
        }


def entropy(data: bytes) -> float:
    """Shannon entropy in bits per byte; ~8.0 means compressed or encrypted."""
    if not data:
        return 0.0
    counts = collections.Counter(data)
    total = len(data)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


def extract_strings(data: bytes, min_length: int = 4) -> list[str]:
    """ASCII runs of at least *min_length* printable characters."""
    return [
        m.group().decode("ascii")
        for m in PRINTABLE.finditer(data)
        if len(m.group()) >= min_length
    ]


def _fs_findings(rom: NdsRom) -> list[Finding]:
    try:
        fs = rom.filesystem()
        entries = fs.entries()
    except Exception as exc:  # malformed tables are a finding, not a crash
        return [Finding("filesystem", f"NitroFS could not be walked: {exc}", "warning")]

    if not entries:
        return [Finding("filesystem", "no embedded filesystem", "info")]

    by_suffix = collections.Counter(e.path.suffix.lower() or "(none)" for e in entries)
    findings = [
        Finding(
            "filesystem",
            f"{len(entries)} files, {human_size(fs.total_size())} of content",
            "info",
            {"by_suffix": dict(by_suffix.most_common())},
        )
    ]
    overlap = [e for e in entries if e.end > len(rom.data)]
    if overlap:
        findings.append(
            Finding(
                "filesystem",
                f"{len(overlap)} file(s) point past the end of the image",
                "error",
                {"paths": [str(e.path) for e in overlap[:10]]},
            )
        )
    return findings


def _integrity_findings(rom: NdsRom) -> list[Finding]:
    findings = [
        Finding(
            "integrity",
            "header CRC matches" if rom.header_crc_ok else "header CRC mismatch",
            "info" if rom.header_crc_ok else "warning",
        )
    ]
    banner = rom.try_banner()
    if banner is not None:
        findings.append(
            Finding(
                "integrity",
                "banner CRC matches" if banner.crc_ok else "banner CRC mismatch",
                "info" if banner.crc_ok else "warning",
            )
        )
    elif rom.banner_offset:
        findings.append(
            Finding(
                "integrity",
                f"banner at 0x{rom.banner_offset:X} is unreadable in this image",
                "error",
            )
        )
    if rom.total_used_size > len(rom.data):
        findings.append(
            Finding(
                "integrity",
                f"header claims {human_size(rom.total_used_size)} but image is "
                f"{human_size(len(rom.data))} - likely truncated",
                "error",
            )
        )
    trimmed = len(rom.data) < rom.capacity_bytes
    findings.append(
        Finding(
            "integrity",
            f"image is {'trimmed' if trimmed else 'padded'} relative to the "
            f"{human_size(rom.capacity_bytes)} cartridge capacity",
            "info",
        )
    )
    return findings


def _code_findings(rom: NdsRom) -> list[Finding]:
    findings = []
    for region in (rom.arm9, rom.arm7):
        try:
            blob = rom.read_region(region)
        except Exception as exc:
            findings.append(Finding("code", f"{region.name}: {exc}", "error"))
            continue
        ent = entropy(blob)
        findings.append(
            Finding(
                "code",
                f"{region.name}: {human_size(region.size)} at 0x{region.offset:X}, "
                f"entropy {ent:.2f}",
                "info",
                {"entropy": round(ent, 3), "offset": region.offset, "size": region.size},
            )
        )
        if ent > 7.5:
            findings.append(
                Finding(
                    "code",
                    f"{region.name} looks compressed or encrypted (entropy {ent:.2f}); "
                    "decompress before static analysis",
                    "warning",
                )
            )
    return findings


def _leftover_findings(rom: NdsRom) -> list[Finding]:
    hits = collections.Counter()
    for marker in LEFTOVER_MARKERS:
        count = rom.data.count(marker)
        if count:
            hits[marker.decode("ascii", "replace")] = count
    if not hits:
        return []
    return [
        Finding(
            "leftovers",
            f"{len(hits)} build-time marker kind(s) present in the image",
            "info",
            {"markers": dict(hits.most_common())},
        )
    ]


def _slack_findings(rom: NdsRom) -> list[Finding]:
    findings = []
    for gap in rom.slack_ranges():
        if gap.size < 0x1000:
            continue
        blob = rom.data[gap.offset : min(gap.end, len(rom.data))]
        if not blob:
            continue
        ent = entropy(blob)
        if ent < 0.5:  # plain padding
            continue
        findings.append(
            Finding(
                "slack",
                f"{human_size(gap.size)} of unclaimed data at 0x{gap.offset:X} "
                f"with entropy {ent:.2f}",
                "info",
                {"offset": gap.offset, "size": gap.size, "entropy": round(ent, 3)},
            )
        )
    return findings


def _classify(data: bytes) -> str:
    """Name what a file is, for the content survey."""
    codec = detect_compression(data)
    if codec:
        return f"compressed:{codec}"
    if Narc.looks_like(data):
        return "narc"
    if Sdat.looks_like(data):
        return "sdat"
    magic = data[:4].decode("ascii", "replace")
    known = {
        "RLCN": "nclr palette",
        "RGCN": "ncgr sheet",
        "RCSN": "nscr screen",
        "RECN": "ncer cells",
        "RNAN": "nanr animation",
        "RTFN": "nftr font",
        "NFTR": "nftr font",
    }
    return known.get(magic, "other")


def _content_findings(rom: NdsRom) -> list[Finding]:
    """What the embedded filesystem is actually made of.

    Worth knowing before anything else: a title whose files are nearly all
    compressed needs `extract --decompress` before a single string shows up.
    """
    try:
        fs = rom.filesystem()
        entries = fs.entries()
    except Exception:
        return []
    if not entries:
        return []

    kinds = collections.Counter(
        _classify(rom.data[entry.offset : entry.end]) for entry in entries
    )
    findings = [
        Finding(
            "content",
            ", ".join(f"{count}x {kind}" for kind, count in kinds.most_common()),
            "info",
            {"kinds": dict(kinds.most_common())},
        )
    ]
    compressed = sum(count for kind, count in kinds.items() if kind.startswith("compressed:"))
    if compressed:
        findings.append(
            Finding(
                "content",
                f"{compressed} of {len(entries)} files are BIOS-compressed; run "
                "`reatk extract --decompress` to read them",
                "info",
            )
        )
    containers = kinds.get("narc", 0) + kinds.get("sdat", 0)
    if containers:
        findings.append(
            Finding(
                "content",
                f"{containers} nested archive(s); `--recurse-narc` / `--extract-sdat` "
                "unpack them",
                "info",
            )
        )
    return findings


_ORDER = {"error": 0, "warning": 1, "info": 2}


def analyze(rom: NdsRom) -> dict:
    """Run every pass and return a JSON-serialisable report."""
    findings: list[Finding] = []
    findings += _integrity_findings(rom)
    findings += _code_findings(rom)
    findings += _fs_findings(rom)
    findings += _content_findings(rom)
    findings += _leftover_findings(rom)
    findings += _slack_findings(rom)
    findings.sort(key=lambda f: _ORDER.get(f.severity, 3))

    banner = rom.try_banner()
    return {
        "source": str(rom.source) if rom.source else None,
        "platform": rom.platform,
        "title": rom.title,
        "game_code": rom.game_code,
        "maker_code": rom.maker_code,
        "maker": rom.maker,
        "region": rom.region_letter,
        "rom_version": rom.rom_version,
        "image_size": len(rom.data),
        "cartridge_capacity": rom.capacity_bytes,
        "banner_titles": banner.titles() if banner else {},
        "regions": [
            {"name": r.name, "offset": r.offset, "size": r.size} for r in rom.regions()
        ],
        "findings": [f.as_dict() for f in findings],
    }
