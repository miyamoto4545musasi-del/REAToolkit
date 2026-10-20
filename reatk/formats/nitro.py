"""The common Nitro container header.

Every first-party DS resource file (NARC, NCLR, NCGR, SDAT, ...) starts with
the same 16-byte header followed by length-prefixed sections.  Section magics
are stored little-endian, so a file written as ``FATB`` reads as ``BTAF``;
:func:`read_sections` accepts either spelling.
"""

from __future__ import annotations

import dataclasses

from .._util import ParseError, u16, u32

HEADER_SIZE = 0x10
BOM_LE = 0xFEFF


@dataclasses.dataclass(frozen=True)
class Section:
    magic: str
    offset: int  # of the section header, not its payload
    size: int

    @property
    def payload_offset(self) -> int:
        return self.offset + 8

    @property
    def payload_size(self) -> int:
        return max(0, self.size - 8)


@dataclasses.dataclass(frozen=True)
class NitroFile:
    magic: str
    version: tuple[int, int]
    size: int
    sections: dict[str, Section]

    def section(self, *names: str) -> Section:
        """The first section matching any of *names* or its reversed spelling."""
        for name in names:
            for candidate in (name, name[::-1]):
                if candidate in self.sections:
                    return self.sections[candidate]
        raise ParseError(f"none of {names} present (have {sorted(self.sections)})")

    def has(self, *names: str) -> bool:
        try:
            self.section(*names)
        except ParseError:
            return False
        return True


def read_header(data: bytes, offset: int = 0) -> NitroFile:
    """Parse the container header and its section table."""
    if offset + HEADER_SIZE > len(data):
        raise ParseError("not enough data for a Nitro container header")
    magic = data[offset : offset + 4].decode("ascii", "replace")
    bom = u16(data, offset + 4)
    if bom != BOM_LE:
        raise ParseError(f"{magic}: unexpected byte order mark 0x{bom:04X}")
    version = (data[offset + 7], data[offset + 6])
    size = u32(data, offset + 8)
    header_size = u16(data, offset + 12)
    count = u16(data, offset + 14)

    sections: dict[str, Section] = {}
    cursor = offset + max(header_size, HEADER_SIZE)
    for _ in range(count):
        if cursor + 8 > len(data):
            raise ParseError(f"{magic}: section table runs past the end of the file")
        name = data[cursor : cursor + 4].decode("ascii", "replace")
        section_size = u32(data, cursor + 4)
        if section_size < 8:
            raise ParseError(f"{magic}: section {name} has an impossible size")
        sections[name] = Section(name, cursor, section_size)
        cursor += section_size
    return NitroFile(magic, version, size, sections)


def write_header(magic: str, sections: list[bytes], version: tuple[int, int] = (1, 0)) -> bytes:
    """Build a container from already-encoded sections.

    Only used by the tests and by plugins that repack a resource, but it keeps
    the layout knowledge in one place.
    """
    body = b"".join(sections)
    total = HEADER_SIZE + len(body)
    head = (
        magic.encode("ascii")
        + BOM_LE.to_bytes(2, "little")
        + bytes([version[1], version[0]])
        + total.to_bytes(4, "little")
        + HEADER_SIZE.to_bytes(2, "little")
        + len(sections).to_bytes(2, "little")
    )
    return head + body


def write_section(magic: str, payload: bytes) -> bytes:
    return magic.encode("ascii") + (len(payload) + 8).to_bytes(4, "little") + payload
