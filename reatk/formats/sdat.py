"""SDAT sound archives.

SDAT does not use the usual Nitro section table: its header carries explicit
offsets for four blocks.

    SYMB  names, per record type (optional - stripped in many retail titles)
    INFO  records, per record type; each starts with the file id it uses
    FAT   offset/size for every file in the archive
    FILE  the files themselves

What comes out is a listing of sequences, banks and wave archives with their
names where present, plus the bytes of each.  Decoding a sequence into notes
is a separate job and not done here.
"""

from __future__ import annotations

import dataclasses

from .._util import ParseError, u16, u32

RECORD_TYPES = (
    "sseq",  # sequences
    "ssar",  # sequence archives
    "sbnk",  # instrument banks
    "swar",  # wave archives
    "player",
    "group",
    "player2",
    "strm",  # streamed audio
)
# Record types whose first field is a file id; the rest only reference others.
FILE_BACKED = {"sseq", "ssar", "sbnk", "swar", "strm"}


@dataclasses.dataclass(frozen=True)
class SdatEntry:
    kind: str
    index: int
    name: str | None
    file_id: int
    offset: int
    size: int

    @property
    def label(self) -> str:
        return self.name or f"{self.kind}_{self.index:04}"


class Sdat:
    def __init__(self, data: bytes):
        if data[:4] != b"SDAT":
            raise ParseError(f"expected an SDAT archive, got {data[:4]!r}")
        if len(data) < 0x40:
            raise ParseError("SDAT header is truncated")
        if u16(data, 4) != 0xFEFF:
            raise ParseError("SDAT has an unexpected byte order mark")
        self.data = data
        self.symb_offset = u32(data, 0x10) or None
        self.info_offset = u32(data, 0x18) or None
        self.fat_offset = u32(data, 0x20) or None
        if self.info_offset is None or self.fat_offset is None:
            raise ParseError("SDAT is missing its INFO or FAT block")
        self.file_count = u32(data, self.fat_offset + 8)

    @classmethod
    def looks_like(cls, data: bytes) -> bool:
        return len(data) >= 0x40 and data[:4] == b"SDAT"

    # -- blocks ----------------------------------------------------------

    def _fat_entry(self, file_id: int) -> tuple[int, int]:
        if file_id >= self.file_count:
            raise ParseError(f"file id {file_id} is past the end of the FAT")
        base = self.fat_offset + 12 + file_id * 16
        return u32(self.data, base), u32(self.data, base + 4)

    def _record_offsets(self, block_offset: int, type_index: int) -> list[int]:
        """Entry offsets for one record type, absolute in the image."""
        pointer = u32(self.data, block_offset + 8 + type_index * 4)
        if not pointer:
            return []
        table = block_offset + pointer
        count = u32(self.data, table)
        return [
            block_offset + value
            for value in (
                u32(self.data, table + 4 + index * 4) for index in range(count)
            )
        ]

    def _names(self, type_index: int) -> list[str | None]:
        if self.symb_offset is None:
            return []
        out: list[str | None] = []
        for offset in self._record_offsets(self.symb_offset, type_index):
            if offset <= self.symb_offset:
                out.append(None)
                continue
            end = self.data.find(b"\x00", offset)
            out.append(self.data[offset:end].decode("ascii", "replace") or None)
        return out

    # -- public ----------------------------------------------------------

    def entries(self) -> list[SdatEntry]:
        """Every file-backed record in the archive."""
        out: list[SdatEntry] = []
        for type_index, kind in enumerate(RECORD_TYPES):
            if kind not in FILE_BACKED:
                continue
            names = self._names(type_index)
            for index, record in enumerate(self._record_offsets(self.info_offset, type_index)):
                if record + 2 > len(self.data):
                    raise ParseError(f"{kind} record {index} is past the end of the file")
                file_id = u16(self.data, record)
                try:
                    offset, size = self._fat_entry(file_id)
                except ParseError:
                    continue  # an unused slot; retail archives have plenty
                out.append(
                    SdatEntry(
                        kind=kind,
                        index=index,
                        name=names[index] if index < len(names) else None,
                        file_id=file_id,
                        offset=offset,
                        size=size,
                    )
                )
        return out

    def read(self, entry: SdatEntry) -> bytes:
        end = entry.offset + entry.size
        if end > len(self.data):
            raise ParseError(f"{entry.label} ends past the end of the archive")
        return self.data[entry.offset : end]

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries():
            counts[entry.kind] = counts.get(entry.kind, 0) + 1
        return counts
