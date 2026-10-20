"""NitroFS: the directory tree embedded in a DS cartridge.

Two tables describe it.  The file name table (FNT) holds one 8-byte directory
record per directory followed by name sub-tables; the file allocation table
(FAT) holds one start/end pair per file id.  Directory ids start at 0xF000,
file ids at 0.
"""

from __future__ import annotations

import dataclasses
from pathlib import PurePosixPath

from .._util import ParseError, u16, u32

ROOT_DIR_ID = 0xF000
DIR_RECORD_SIZE = 8


@dataclasses.dataclass(frozen=True)
class FsEntry:
    """One file inside the embedded filesystem."""

    path: PurePosixPath
    file_id: int
    offset: int
    size: int

    @property
    def end(self) -> int:
        return self.offset + self.size


class NitroFs:
    """Random-access view over the cartridge filesystem."""

    def __init__(self, data: bytes, fnt, fat, data_base: int = 0):
        """*data_base* is added to every allocation-table offset.

        Zero for a cartridge image, where the table holds absolute offsets, and
        the start of the data chunk for a NARC archive, where it does not.
        """
        self.data = data
        self.fnt = fnt
        self.fat = fat
        self.data_base = data_base
        self._entries: list[FsEntry] | None = None

    # -- low level -------------------------------------------------------

    def _fat_entry(self, file_id: int) -> tuple[int, int]:
        off = self.fat.offset + file_id * 8
        if off + 8 > len(self.data) or off + 8 > self.fat.end:
            raise ParseError(f"file id {file_id} is outside the allocation table")
        start, end = u32(self.data, off), u32(self.data, off + 4)
        if end < start:
            raise ParseError(f"file id {file_id} has a negative length")
        return start + self.data_base, end - start

    def fat_entry(self, file_id: int) -> tuple[int, int]:
        """``(offset, size)`` for *file_id*, for archives without a name table."""
        return self._fat_entry(file_id)

    def _walk(self, dir_id: int, prefix: PurePosixPath, seen: set[int], out: list[FsEntry]) -> None:
        if dir_id in seen:
            raise ParseError(f"directory {dir_id:#x} is its own ancestor")
        seen.add(dir_id)

        record = self.fnt.offset + (dir_id & 0x0FFF) * DIR_RECORD_SIZE
        if record + DIR_RECORD_SIZE > len(self.data):
            raise ParseError(f"directory record {dir_id:#x} is past the image")
        sub_offset = self.fnt.offset + u32(self.data, record)
        file_id = u16(self.data, record + 4)

        cursor = sub_offset
        while True:
            if cursor >= len(self.data):
                raise ParseError("name sub-table runs past the end of the image")
            kind = self.data[cursor]
            cursor += 1
            if kind == 0:  # end of this directory
                return
            name_len = kind & 0x7F
            name = self.data[cursor : cursor + name_len].decode("shift_jis", "replace")
            cursor += name_len
            if kind & 0x80:  # sub-directory: the id follows the name
                child_id = u16(self.data, cursor)
                cursor += 2
                self._walk(child_id, prefix / name, set(seen), out)
            else:
                offset, size = self._fat_entry(file_id)
                out.append(FsEntry(prefix / name, file_id, offset, size))
                file_id += 1

    # -- public ----------------------------------------------------------

    def entries(self) -> list[FsEntry]:
        """Every file in the tree, cached after the first walk."""
        if self._entries is None:
            if not self.fnt.size or not self.fat.size:
                self._entries = []
            else:
                out: list[FsEntry] = []
                self._walk(ROOT_DIR_ID, PurePosixPath("/"), set(), out)
                self._entries = out
        return self._entries

    def read(self, entry: FsEntry) -> bytes:
        if entry.end > len(self.data):
            raise ParseError(f"{entry.path} ends past the image")
        return self.data[entry.offset : entry.end]

    def find(self, pattern: str) -> list[FsEntry]:
        """Entries whose path matches a glob such as ``*/sound/*.sdat``."""
        return [e for e in self.entries() if e.path.match(pattern)]

    def total_size(self) -> int:
        return sum(e.size for e in self.entries())

    def __len__(self) -> int:
        return len(self.entries())

    def __iter__(self):
        return iter(self.entries())
