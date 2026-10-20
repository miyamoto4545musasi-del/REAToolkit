"""NARC archives: a NitroFS tree packed into a single file.

DS titles keep most of their content in these rather than at the top level of
the cartridge, so walking a ROM without opening its NARCs sees only a handful
of files.
"""

from __future__ import annotations

import dataclasses

from .._util import ParseError, u16
from . import nitro
from .nitrofs import NitroFs


@dataclasses.dataclass(frozen=True)
class _Region:
    """Matches the region shape :class:`NitroFs` expects."""

    name: str
    offset: int
    size: int

    @property
    def end(self) -> int:
        return self.offset + self.size


class Narc:
    """A parsed NARC archive."""

    def __init__(self, data: bytes):
        self.data = data
        self.container = nitro.read_header(data)
        if self.container.magic != "NARC":
            raise ParseError(f"expected a NARC archive, got {self.container.magic!r}")

        fat = self.container.section("FATB", "BTAF")
        fnt = self.container.section("FNTB", "BTNF")
        img = self.container.section("FIMG", "GMIF")

        self.file_count = u16(data, fat.payload_offset)
        self.filesystem = NitroFs(
            data,
            fnt=_Region("fnt", fnt.payload_offset, fnt.payload_size),
            # The first four payload bytes are the file count and padding.
            fat=_Region("fat", fat.payload_offset + 4, fat.payload_size - 4),
            data_base=img.payload_offset,
        )

    @classmethod
    def looks_like(cls, data: bytes) -> bool:
        return len(data) >= 0x10 and data[:4] == b"NARC"

    def entries(self):
        """Named entries when the archive carries a name table.

        Plenty of NARCs ship with an empty one; :meth:`flat_entries` is the
        fallback for those.
        """
        return self.filesystem.entries()

    def flat_entries(self):
        """Entries by index, for archives without names."""
        from pathlib import PurePosixPath

        from .nitrofs import FsEntry

        out = []
        for file_id in range(self.file_count):
            offset, size = self.filesystem.fat_entry(file_id)
            out.append(
                FsEntry(PurePosixPath(f"/{file_id:04}.bin"), file_id, offset, size)
            )
        return out

    def list(self):
        """Named entries if there are any, otherwise indexed ones."""
        try:
            named = self.entries()
        except ParseError:
            named = []
        return named or self.flat_entries()

    def read(self, entry) -> bytes:
        return self.filesystem.read(entry)
