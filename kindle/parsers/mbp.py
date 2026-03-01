"""Parser for Mobipocket annotation (.mbp) files."""

import struct
import sys
from pathlib import Path
from typing import List

from kindle.models import Clipping

# ---------------------------------------------------------------------------
# MBP parser
# ---------------------------------------------------------------------------
# Mobipocket annotation format (.mbp).  Not officially documented; this
# implementation is based on community reverse-engineering.
#
# The file is a sequence of variable-length records:
#
#   [record_type : uint16 BE]
#   [record_length : uint16 BE]   (bytes of payload that follow)
#   [payload : bytes]
#
# Known record types:
#   0x0001  Last reading position  — 4-byte file offset
#   0x0002  Bookmark              — 4-byte offset + optional length-prefixed label
#   0x0003  Highlight             — 8-byte (start_offset uint32, end_offset uint32)
#   0x0004  Annotation / Note     — 8-byte position header + length-prefixed text
#   0x0007  Chapter position      — 4-byte offset
#   0x000E  Reading statistics    — misc bytes (skip)
#
# String encoding: UTF-16LE with a 2-byte length prefix (character count).
# Some older files use a 1-byte length prefix with Latin-1 encoding.

_MBP_RTYPE_LAST_POS  = 0x0001
_MBP_RTYPE_BOOKMARK  = 0x0002
_MBP_RTYPE_HIGHLIGHT = 0x0003
_MBP_RTYPE_NOTE      = 0x0004
# 0x0007 = chapter position (skipped — no user content)


def _read_mbp_string(data: bytes, offset: int) -> tuple[str, int]:
    """
    Read a length-prefixed string from MBP payload.
    Tries UTF-16LE (2-byte length) then falls back to Latin-1 (1-byte length).
    Returns (string, new_offset).
    """
    if offset + 2 > len(data):
        return "", offset
    (char_count,) = struct.unpack_from(">H", data, offset)
    byte_count = char_count * 2
    if offset + 2 + byte_count <= len(data):
        text = data[offset + 2: offset + 2 + byte_count].decode("utf-16-le", errors="replace")
        return text, offset + 2 + byte_count
    # Fallback: try as 1-byte count with Latin-1
    char_count = data[offset]
    if offset + 1 + char_count <= len(data):
        text = data[offset + 1: offset + 1 + char_count].decode("latin-1", errors="replace")
        return text, offset + 1 + char_count
    return "", offset


def parse_mbp(path: Path, book_title: str = "") -> List[Clipping]:
    """
    Parse a Mobipocket annotation (.mbp) file.

    book_title: if provided (e.g. from the sibling .mobi filename) it is
    used to populate Clipping.book_title; otherwise the stem of the .mbp
    file itself is used.
    """
    data = path.read_bytes()
    clippings: List[Clipping] = []
    title = book_title or path.stem
    offset = 0

    # Some MBP files have a small file header before the records.
    # Known signatures:
    #   b"\x00\x01" at byte 0 → version 1, records start at byte 2
    #   b"BOOKMOBI"            → full PalmDB container (not handled here, rare)
    if data[:8] == b"BOOKMOBI":
        print(f"  [mbp] PalmDB container format not yet supported: {path.name}", file=sys.stderr)
        return clippings

    # Skip a 2-byte version prefix if present
    if len(data) >= 2 and data[0] == 0x00 and data[1] in (0x01, 0x02):
        offset = 2

    while offset + 4 <= len(data):
        try:
            rec_type, rec_len = struct.unpack_from(">HH", data, offset)
        except struct.error:
            break
        offset += 4
        payload = data[offset: offset + rec_len]
        offset += rec_len

        if rec_type == _MBP_RTYPE_LAST_POS:
            if len(payload) >= 4:
                (pos,) = struct.unpack_from(">I", payload, 0)
                clippings.append(Clipping(
                    book_title=title,
                    clip_type="last_position",
                    location_start=pos,
                    source_file=str(path),
                ))

        elif rec_type == _MBP_RTYPE_BOOKMARK:
            if len(payload) >= 4:
                (pos,) = struct.unpack_from(">I", payload, 0)
                label, _ = _read_mbp_string(payload, 4)
                clippings.append(Clipping(
                    book_title=title,
                    clip_type="bookmark",
                    location_start=pos,
                    content=label,
                    source_file=str(path),
                ))

        elif rec_type == _MBP_RTYPE_HIGHLIGHT:
            if len(payload) >= 8:
                start, end = struct.unpack_from(">II", payload, 0)
                clippings.append(Clipping(
                    book_title=title,
                    clip_type="highlight",
                    location_start=start,
                    location_end=end,
                    source_file=str(path),
                ))

        elif rec_type == _MBP_RTYPE_NOTE:
            if len(payload) >= 8:
                start, end = struct.unpack_from(">II", payload, 0)
                note_text, _ = _read_mbp_string(payload, 8)
                clippings.append(Clipping(
                    book_title=title,
                    clip_type="note",
                    location_start=start,
                    location_end=end,
                    content=note_text,
                    source_file=str(path),
                ))

        # All other record types are silently skipped

    return clippings
