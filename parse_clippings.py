#!/usr/bin/env python3
"""
Kindle / Mobipocket clippings parser.

Supports:
  My Clippings.txt  — plain-text highlights/notes written by every Kindle device
  .apnx             — Amazon Page Number Index (page-map metadata, no text content)
  .mbp              — Mobipocket annotation binary (bookmarks, highlights, notes)
  .yjr              — Kindle sidecar annotation records (highlights, bookmarks, notes)
  .yjf              — Kindle sidecar fast-data (last reading position, timer stats)
  .sdr/             — Kindle sidecar directory (scanned recursively for yjr/yjf)

Export formats: json, csv, markdown, text

Usage examples:
  python parse_clippings.py "My Clippings.txt" -o clippings.json
  python parse_clippings.py book.mbp -o book_notes.md -f markdown
  python parse_clippings.py book.apnx -o pages.json
  python parse_clippings.py "book.sdr/" -o clippings.md -f markdown
  python parse_clippings.py clippings/ -o all.csv -f csv   # scan a directory
"""

import struct
import json
import csv
import re
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, List


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Clipping:
    book_title: str = ""
    author: str = ""
    clip_type: str = ""          # highlight | note | bookmark | last_position
    page: Optional[int] = None
    location_start: Optional[int] = None
    location_end: Optional[int] = None
    added_date: Optional[str] = None
    content: str = ""
    source_file: str = ""


@dataclass
class APNXInfo:
    """Not a clipping source — carries page-number index metadata."""
    asin: str = ""
    content_guid: str = ""
    page_count: int = 0
    page_offsets: List[int] = field(default_factory=list)
    source_file: str = ""


# ---------------------------------------------------------------------------
# My Clippings.txt parser
# ---------------------------------------------------------------------------
# Each entry looks like:
#
#   Book Title (Author Name)
#   - Your Highlight on page 42 | Location 512-530 | Added on Monday, January 1, 2024 10:00:00 AM
#
#   Highlighted text goes here.
#   ==========
#
# Notes and bookmarks follow the same pattern but the second line says
# "Your Note" or "Your Bookmark".

_CLIPPINGS_SEP = "=========="

_META_RE = re.compile(
    r"-\s+Your\s+(?P<type>Highlight|Note|Bookmark)"
    r"(?:\s+on\s+(?:page\s+(?P<page>\d+)|[^|]+))?"
    r"(?:\s*\|\s*Location\s+(?P<loc_start>\d+)(?:-(?P<loc_end>\d+))?)?"
    r"(?:\s*\|\s*Added\s+on\s+(?P<date>.+))?",
    re.IGNORECASE,
)

_TITLE_AUTHOR_RE = re.compile(r"^(?P<title>.+?)\s+\((?P<author>[^)]+)\)\s*$")


def parse_my_clippings(path: Path) -> List[Clipping]:
    """Parse Amazon's My Clippings.txt format."""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    entries = text.split(_CLIPPINGS_SEP)
    clippings: List[Clipping] = []

    for raw in entries:
        lines = [l.rstrip() for l in raw.strip().splitlines()]
        if len(lines) < 2:
            continue

        # Line 0 — title (author)
        title_line = lines[0].lstrip("\ufeff").strip()
        ta_match = _TITLE_AUTHOR_RE.match(title_line)
        if ta_match:
            title = ta_match.group("title").strip()
            author = ta_match.group("author").strip()
        else:
            title = title_line
            author = ""

        # Line 1 — metadata
        meta_line = lines[1].strip()
        m = _META_RE.search(meta_line)
        if not m:
            continue

        clip_type = m.group("type").lower() if m.group("type") else "highlight"
        page = int(m.group("page")) if m.group("page") else None
        loc_start = int(m.group("loc_start")) if m.group("loc_start") else None
        loc_end = int(m.group("loc_end")) if m.group("loc_end") else None
        date_str = m.group("date").strip() if m.group("date") else None

        # Remaining non-empty lines — the clipped content
        content_lines = [l for l in lines[2:] if l.strip()]
        content = " ".join(content_lines).strip()

        clippings.append(Clipping(
            book_title=title,
            author=author,
            clip_type=clip_type,
            page=page,
            location_start=loc_start,
            location_end=loc_end,
            added_date=date_str,
            content=content,
            source_file=str(path),
        ))

    return clippings


# ---------------------------------------------------------------------------
# APNX parser
# ---------------------------------------------------------------------------
# Binary layout (two known versions):
#
# Version 1 (older):
#   Offset  Size  Description
#   0       4     version (little-endian uint32, == 1)
#   4       4     offset to page data from start of file (uint32 LE)
#   8       4     page count (uint32 LE)
#   12      var   JSON metadata string (ends at page-data offset - 4)
#   ?       4     page count again (redundant uint32 LE)
#   ?       4*N   page offsets (uint32 LE each)
#
# Version 2 (newer):
#   Offset  Size  Description
#   0       4     version (uint32 LE, == 2)
#   4       4     header length / offset to JSON  (uint32 LE)
#   8       var   JSON metadata
#   ?       4     page count
#   ?       4*N   page offsets

def parse_apnx(path: Path) -> APNXInfo:
    """Parse an Amazon Page Number Index (.apnx) file."""
    data = path.read_bytes()
    info = APNXInfo(source_file=str(path))

    if len(data) < 8:
        raise ValueError("APNX file too short")

    version, data_offset = struct.unpack_from("<II", data, 0)

    # --- locate the JSON header ---
    json_str = ""
    if version in (1, 2) and data_offset < len(data):
        # JSON sits between byte 8 (or 12) and data_offset
        json_start = 8 if version == 1 else 8
        # scan forward from json_start for '{' in case there's padding
        for i in range(json_start, min(data_offset, json_start + 256)):
            if data[i:i+1] == b"{":
                json_start = i
                break
        json_bytes = data[json_start:data_offset]
        try:
            json_str = json_bytes.decode("utf-8", errors="replace").rstrip("\x00")
            meta = json.loads(json_str)
            info.asin = meta.get("asin", "")
            info.content_guid = meta.get("contentGuid", "")
            info.page_count = meta.get("pageCount", 0)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    # --- read page offsets ---
    # After the JSON header there is an optional 4-byte page count then the offsets
    offset = data_offset
    if offset + 4 <= len(data):
        (page_count_check,) = struct.unpack_from("<I", data, offset)
        # sanity: if it looks like a count (not a huge offset), use it
        if page_count_check < 100_000:
            if info.page_count == 0:
                info.page_count = page_count_check
            offset += 4

    while offset + 4 <= len(data):
        (page_offset,) = struct.unpack_from("<I", data, offset)
        info.page_offsets.append(page_offset)
        offset += 4

    return info


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


# ---------------------------------------------------------------------------
# YJR / YJF parser  (Kindle .sdr sidecar format)
# ---------------------------------------------------------------------------
#
# Both files share the same TLV binary structure:
#
#   [16-byte file header]
#   [records...]
#
# Each record:
#   0xFE  [3-byte big-endian key length]  [key bytes]   — starts a named record
#   followed by zero or more typed values:
#     0x01  [4 bytes big-endian]  → uint32 integer
#     0x02  [8 bytes big-endian]  → uint64 timestamp (ms since Unix epoch)
#     0x03  [3-byte big-endian length]  [data bytes]  → variable bytes / string
#   0xFF  — ends the current record
#
# Annotation keys (found in .yjr):
#   annotation.personal.highlight  — field order: start_pos, end_pos, [5-byte marker], color
#   annotation.personal.bookmark   — field order: pos, pos(same), [5-byte marker], color
#   annotation.personal.note       — field order: pos, pos(same), [5-byte marker], note_text
#
# Position string format: "[base64_cfi]:[kindle_location]"
#   e.g.  "AT4EAABpAAAA:13927"  — location 13927 in the ebook
#
# Last-read-position key (found in .yjf):
#   lpr  — contains a position string for where reading stopped

_YJR_ANNOTATION_KEYS = {
    b"annotation.personal.highlight": "highlight",
    b"annotation.personal.bookmark":  "bookmark",
    b"annotation.personal.note":      "note",
}
_YJR_LPR_KEY = b"lpr"
# 5-byte separator between position fields and content/color fields
_YJR_MARKER_LEN = 5


def _yjr_read_values(data: bytes, offset: int) -> tuple[list, int]:
    """
    Read typed TLV values starting at `offset` until 0xFF or 0xFE.
    Returns (values_list, new_offset).
    Each value is (type_char, raw_bytes).
    """
    values: list = []
    end = len(data)
    while offset < end:
        b = data[offset]
        if b == 0xFF or b == 0xFE:
            break
        elif b == 0x01:          # uint32
            offset += 1
            if offset + 4 > end:
                break
            values.append(("int", data[offset: offset + 4]))
            offset += 4
        elif b == 0x02:          # uint64 timestamp
            offset += 1
            if offset + 8 > end:
                break
            values.append(("ts", data[offset: offset + 8]))
            offset += 8
        elif b == 0x03:          # variable-length bytes
            offset += 1
            if offset + 3 > end:
                break
            vlen = (data[offset] << 16) | (data[offset + 1] << 8) | data[offset + 2]
            offset += 3
            if vlen > 65536 or offset + vlen > end:
                break           # sanity guard
            values.append(("bytes", data[offset: offset + vlen]))
            offset += vlen
        elif b == 0x07:          # compound container — next byte is item count,
            offset += 2          # then items follow using the same TLV encoding.
                                 # Skipping the 2-byte header lets inner items be
                                 # parsed transparently in subsequent loop iterations.
        else:
            offset += 1         # skip unknown byte
    return values, offset


def _yjr_location(pos_str: str) -> Optional[int]:
    """Extract the integer Kindle location from 'base64cfi:location'."""
    if ":" in pos_str:
        try:
            return int(pos_str.rsplit(":", 1)[-1])
        except ValueError:
            pass
    return None


def _yjr_timestamp(raw8: bytes) -> Optional[str]:
    """Decode 8-byte big-endian ms-since-epoch to ISO-8601 UTC string."""
    try:
        ms = int.from_bytes(raw8, "big")
        if ms == 0 or ms > 4_000_000_000_000:
            return None
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (OSError, OverflowError, ValueError):
        return None


def _yjr_find_all(data: bytes, key: bytes) -> list[int]:
    """Return offsets of all TLV record starts for the given key."""
    key_len = len(key)
    prefix = b"\xfe" + key_len.to_bytes(3, "big") + key
    offsets = []
    start = 0
    while True:
        idx = data.find(prefix, start)
        if idx == -1:
            break
        offsets.append(idx + len(prefix))   # values start right after key
        start = idx + 1
    return offsets


def parse_yjr(path: Path, book_title: str = "") -> List[Clipping]:
    """
    Parse a Kindle sidecar annotation file (.yjr).
    Returns Clipping objects for highlights, bookmarks, and notes.
    book_title: used for Clipping.book_title; defaults to the .sdr parent folder stem.
    """
    data = path.read_bytes()
    title = book_title or path.stem
    clippings: List[Clipping] = []

    for key_bytes, annot_type in _YJR_ANNOTATION_KEYS.items():
        for val_offset in _yjr_find_all(data, key_bytes):
            values, _ = _yjr_read_values(data, val_offset)

            strings  = [v for t, v in values if t == "bytes"]
            timestamps = [v for t, v in values if t == "ts"]

            if len(strings) < 2:
                continue

            start_str = strings[0].decode("utf-8", errors="replace")
            end_str   = strings[1].decode("utf-8", errors="replace")
            loc_start = _yjr_location(start_str)
            loc_end   = _yjr_location(end_str)
            if loc_end == loc_start:
                loc_end = None      # bookmark: start==end, keep only start

            # strings[2] is the 5-byte marker; strings[3] is color or note text
            content = ""
            color   = ""
            if len(strings) >= 4:
                marker_candidate = strings[2]
                payload          = strings[3].decode("utf-8", errors="replace")
                if len(marker_candidate) == _YJR_MARKER_LEN:
                    if annot_type == "note":
                        content = payload
                    else:
                        color = payload
                else:
                    # Unexpected layout — use payload as content
                    content = strings[2].decode("utf-8", errors="replace")
            elif len(strings) == 3:
                third = strings[2]
                if len(third) == _YJR_MARKER_LEN:
                    pass            # only marker, no color/content
                else:
                    decoded = third.decode("utf-8", errors="replace")
                    if annot_type == "note":
                        content = decoded
                    else:
                        color = decoded

            created = _yjr_timestamp(timestamps[0]) if timestamps else None

            extra = {}
            if color:
                extra["color"] = color

            clippings.append(Clipping(
                book_title=title,
                clip_type=annot_type,
                location_start=loc_start,
                location_end=loc_end,
                added_date=created,
                content=content,
                source_file=str(path),
            ))
            # stash color in a field we can surface later
            if color:
                clippings[-1].content = (f"[{color}] " + content).strip()

    return clippings


def parse_yjf(path: Path, book_title: str = "") -> List[Clipping]:
    """
    Parse a Kindle sidecar fast-data file (.yjf).
    Currently extracts only the last-read position (lpr key).
    """
    data = path.read_bytes()
    title = book_title or path.stem
    clippings: List[Clipping] = []

    for val_offset in _yjr_find_all(data, _YJR_LPR_KEY):
        values, _ = _yjr_read_values(data, val_offset)
        strings    = [v for t, v in values if t == "bytes"]
        timestamps = [v for t, v in values if t == "ts"]

        # The lpr record may have a small leading int before the position string.
        # Find the first string that looks like a position ("…:digits").
        pos_str = None
        for s in strings:
            try:
                decoded = s.decode("utf-8")
                if re.search(r":\d+$", decoded):
                    pos_str = decoded
                    break
            except UnicodeDecodeError:
                pass

        if not pos_str:
            continue

        loc = _yjr_location(pos_str)
        created = _yjr_timestamp(timestamps[0]) if timestamps else None

        clippings.append(Clipping(
            book_title=title,
            clip_type="last_position",
            location_start=loc,
            added_date=created,
            source_file=str(path),
        ))

    return clippings


def _sdr_book_title(sdr_path: Path) -> str:
    """
    Derive a human-readable book title from the .sdr folder name.
    E.g. "My Book - Author.sdr" → "My Book - Author"
    The long hash suffix in the filenames inside is stripped automatically
    since we use the folder name, not the file name.
    """
    name = sdr_path.name
    if name.endswith(".sdr"):
        name = name[:-4]
    return name


# ---------------------------------------------------------------------------
# KFX text extraction (for resolving highlight locations to actual text)
# ---------------------------------------------------------------------------
#
# Kindle location numbers in YJR annotation files are Unicode character offsets
# into the full book text.  We use Calibre's ebook-convert to produce a plain
# text rendition, then slice [loc_start:loc_end] to get the highlighted text.

_CALIBRE_PATHS = [
    "/Applications/calibre.app/Contents/MacOS/ebook-convert",
    "/usr/bin/ebook-convert",
    "/usr/local/bin/ebook-convert",
]


def _find_ebook_convert() -> Optional[str]:
    for p in _CALIBRE_PATHS:
        if Path(p).exists():
            return p
    return None


def extract_book_text(ebook_path: Path) -> Optional[str]:
    """
    Convert an ebook (KFX, MOBI, AZW3, EPUB …) to plain text using Calibre
    and return the full Unicode string.  Returns None if Calibre is not found
    or conversion fails.
    """
    converter = _find_ebook_convert()
    if not converter:
        return None

    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        result = subprocess.run(
            [converter, str(ebook_path), str(tmp_path)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            print(f"  [warn] ebook-convert failed: {result.stderr[-200:]}", file=sys.stderr)
            return None
        return tmp_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        print(f"  [warn] ebook-convert error: {exc}", file=sys.stderr)
        return None
    finally:
        tmp_path.unlink(missing_ok=True)


def find_paired_ebook(sdr_path: Path) -> Optional[Path]:
    """
    Look for an ebook file next to a .sdr folder that shares the same stem.
    E.g.  "My Book.sdr/"  →  "My Book.kfx" / "My Book.azw3" / etc.
    """
    stem = sdr_path.stem          # strip .sdr
    parent = sdr_path.parent
    for ext in (".kfx", ".azw3", ".mobi", ".epub", ".azw", ".prc"):
        candidate = parent / (stem + ext)
        if candidate.exists():
            return candidate
    return None


def _find_sdr_parent(source_file: Path) -> Optional[Path]:
    """Walk up the directory tree to find the nearest .sdr ancestor."""
    for parent in source_file.parents:
        if parent.suffix.lower() == ".sdr":
            return parent
    return None


def fill_clipping_text(clippings: List["Clipping"], book_text: str) -> None:
    """
    For YJR annotations that have location numbers but no content text,
    fill in the actual highlighted / bookmarked text from *book_text*.

    Kindle location numbers are Unicode character offsets into the book text.
    """
    for c in clippings:
        if c.content and not c.content.startswith("["):
            continue          # already has real text (e.g. notes)
        if c.location_start is None:
            continue

        loc_s = c.location_start
        loc_e = c.location_end if c.location_end else loc_s

        # Guard against out-of-range positions
        if loc_s >= len(book_text):
            continue

        snippet = book_text[loc_s:min(loc_e, len(book_text))].strip()
        # Collapse internal newlines/whitespace runs to single spaces
        snippet = re.sub(r"\s+", " ", snippet)

        if snippet:
            # Preserve color tag that was already in content, if any
            color_tag = ""
            if c.content and c.content.startswith("["):
                m = re.match(r"(\[[^\]]+\])", c.content)
                if m:
                    color_tag = m.group(1) + " "
            c.content = color_tag + snippet


# ---------------------------------------------------------------------------
# KFX page-map extraction (requires Calibre KFX Input plugin)
# ---------------------------------------------------------------------------
#
# Page numbers in KFX are stored in the Ion binary data.  We use the kfxlib
# library bundled with Calibre's "KFX Input" plugin to decode them.
#
# Returns a sorted list of (page_label: str, char_offset: int) tuples.
# char_offset is the same absolute Unicode character offset used by the
# Kindle location numbers in .yjr annotation files.

_KFX_PLUGIN_PATHS = [
    "~/Library/Preferences/calibre/plugins/KFX Input.zip",
]


def _find_kfx_plugin() -> Optional[str]:
    for p in _KFX_PLUGIN_PATHS:
        expanded = Path(p).expanduser()
        if expanded.exists():
            return str(expanded)
    return None


def extract_kfx_info(kfx_path: Path) -> tuple[Optional[List[tuple]], Optional[List[int]], Optional[str]]:
    """
    Extract page map, Kindle Location boundaries, and book text from a KFX file in one pass.

    Returns:
        (page_map, kindle_loc_offsets, book_text)
        page_map: sorted [(page_label, char_offset), …]  or None
        kindle_loc_offsets: sorted [char_offset_for_kl1, char_offset_for_kl2, …]
            Index i (0-based) holds the char offset where Kindle Location (i+1) starts.
            Use bisect_right(kindle_loc_offsets, char_offset) to get the KL number.
            None if extraction fails.
        book_text: full Unicode text of the book with KFX-internal char offsets preserved,
            so book_text[char_offset_start:char_offset_end] gives the exact highlighted text.
            None if extraction fails.

    Requires the Calibre "KFX Input" plugin to be installed.
    """
    plugin_zip = _find_kfx_plugin()
    if not plugin_zip:
        return None, None, None

    import zipfile
    import sys

    tmpdir: Optional[Path] = None
    try:
        tmpdir = Path(tempfile.mkdtemp())
        with zipfile.ZipFile(plugin_zip) as z:
            for name in z.namelist():
                if name.startswith("kfxlib/") and not name.endswith("/"):
                    dest = tmpdir / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(z.read(name))

        kfxlib_dir = str(tmpdir)
        if kfxlib_dir not in sys.path:
            sys.path.insert(0, kfxlib_dir)

        from kfxlib import yj_book                              # type: ignore
        from kfxlib.ion import unannotated, ion_type, IonSymbol # type: ignore

        book = yj_book.YJ_Book(str(kfx_path))
        book.decode_book(set_metadata=None)
        pos_info = book.collect_position_map_info()

        # --- Kindle Location boundaries ---
        loc_info = book.collect_location_map_info(pos_info)
        kindle_loc_offsets: Optional[List[int]] = (
            [entry.pid for entry in loc_info] if loc_info else None
        )

        # --- Publisher page map ---
        page_map: List[tuple] = []
        nav_fragment = book.fragments.get("$389")
        if nav_fragment is not None:
            for book_navigation in nav_fragment.value:
                for nav_container in book_navigation.get("$392", []):
                    if ion_type(nav_container) is IonSymbol:
                        nav_container = book.fragments.get(ftype="$391", fid=nav_container)
                    if nav_container is None:
                        continue
                    nav_container = unannotated(nav_container)
                    if nav_container.get("$235", None) != "$237":  # $237 = page list
                        continue
                    for entry in nav_container.get("$247", []):
                        ep = unannotated(entry)
                        label = ep.get("$241", {}).get("$244", "")
                        pos   = ep.get("$246", {})
                        eid   = pos.get("$155")
                        eid_offset = pos.get("$143", 0)
                        pid = book.pid_for_eid(eid, eid_offset, pos_info)
                        if pid is not None and label:
                            page_map.append((label, pid))
            page_map.sort(key=lambda x: x[1])

        # --- Book text with correct KFX char offsets ---
        # collect_content_position_info() returns ContentChunk objects where
        # chunk.pid is the absolute char offset and chunk.text is the actual text.
        # Building the text this way preserves the exact positions stored in YJR annotations.
        book_text: Optional[str] = None
        try:
            content_chunks = book.collect_content_position_info()
            chunks_with_text = sorted(
                [c for c in content_chunks if c.text],
                key=lambda c: c.pid,
            )
            if chunks_with_text:
                parts: List[str] = []
                pos = 0
                for c in chunks_with_text:
                    if c.pid > pos:
                        parts.append(" " * (c.pid - pos))   # fill gap
                    parts.append(c.text)
                    pos = c.pid + c.length
                book_text = "".join(parts)
        except Exception as exc:
            print(f"  [warn] KFX text extraction failed: {exc}", file=sys.stderr)

        return page_map if page_map else None, kindle_loc_offsets, book_text

    except Exception as exc:
        print(f"  [warn] extract_kfx_info failed: {exc}", file=sys.stderr)
        return None, None, None
    finally:
        import shutil
        shutil.rmtree(str(tmpdir), ignore_errors=True)


# Keep backward-compatible alias
def extract_page_map(kfx_path: Path) -> Optional[List[tuple]]:
    page_map, *_ = extract_kfx_info(kfx_path)
    return page_map


def fill_clipping_pages(clippings: List["Clipping"], page_map: List[tuple]) -> None:
    """
    Assign page numbers to clippings using the KFX page map.

    page_map: sorted list of (page_label, char_offset) from extract_page_map().
    For each clipping with a location_start, finds the largest page whose
    char_offset ≤ location_start.
    NOTE: location_start must still be a char_offset here (call this BEFORE
    fill_clipping_kindle_locations).
    """
    import bisect
    offsets = [offset for _, offset in page_map]
    labels  = [label  for label, _ in page_map]

    for c in clippings:
        if c.location_start is None or c.page is not None:
            continue
        idx = bisect.bisect_right(offsets, c.location_start) - 1
        if idx >= 0:
            label = labels[idx]
            try:
                c.page = int(label)
            except ValueError:
                pass   # non-numeric page labels (e.g. "ix") — skip


def fill_clipping_kindle_locations(
    clippings: List["Clipping"], kindle_loc_offsets: List[int]
) -> None:
    """
    Convert location_start / location_end from raw KFX char offsets to
    Kindle Location numbers (the small integers shown in the Kindle reader UI).

    kindle_loc_offsets: sorted list where index i holds the char offset at
        which Kindle Location (i+1) begins.  Obtained from extract_kfx_info().

    IMPORTANT: call this AFTER fill_clipping_text() and fill_clipping_pages(),
    because those functions expect char offsets in location_start/end.
    After this call, location_start/end hold Kindle Location numbers.
    """
    import bisect
    for c in clippings:
        if c.location_start is not None:
            kl = bisect.bisect_right(kindle_loc_offsets, c.location_start)
            c.location_start = kl if kl > 0 else 1
        if c.location_end is not None:
            kl = bisect.bisect_right(kindle_loc_offsets, c.location_end)
            c.location_end = kl if kl > 0 else 1
            if c.location_end == c.location_start:
                c.location_end = None  # collapse identical start/end


# ---------------------------------------------------------------------------
# Directory scanner
# ---------------------------------------------------------------------------

def scan_path(input_path: Path) -> tuple[List[Clipping], List[APNXInfo]]:
    """Recursively find and parse all supported files under input_path."""
    clippings: List[Clipping] = []
    apnx_infos: List[APNXInfo] = []

    # Collect files to process; if a .sdr directory is given, scan inside it
    if input_path.is_file():
        files = [input_path]
    else:
        files = list(input_path.rglob("*"))

    # Track which .sdr directories we've seen so we can derive the book title once
    seen_sdr: dict[Path, str] = {}
    for f in files:
        if f.is_file() and f.suffix.lower() in (".yjr", ".yjf"):
            # Walk up to find the nearest .sdr ancestor
            for parent in f.parents:
                if parent.suffix.lower() == ".sdr":
                    if parent not in seen_sdr:
                        seen_sdr[parent] = _sdr_book_title(parent)
                    break

    def _book_title_for(f: Path) -> str:
        for parent in f.parents:
            if parent in seen_sdr:
                return seen_sdr[parent]
        return f.stem

    for f in files:
        if not f.is_file():
            continue
        suffix = f.suffix.lower()
        name = f.name.lower()

        try:
            if name == "my clippings.txt" or (suffix == ".txt" and "clipping" in name):
                print(f"  Parsing My Clippings.txt: {f}")
                clippings.extend(parse_my_clippings(f))

            elif suffix == ".apnx":
                print(f"  Parsing APNX: {f}")
                apnx_infos.append(parse_apnx(f))

            elif suffix == ".mbp":
                print(f"  Parsing MBP: {f}")
                clippings.extend(parse_mbp(f, book_title=_book_title_for(f)))

            elif suffix == ".yjr" and not name.endswith(".bad_file"):
                print(f"  Parsing YJR: {f}")
                clippings.extend(parse_yjr(f, book_title=_book_title_for(f)))

            elif suffix == ".yjf":
                print(f"  Parsing YJF: {f}")
                clippings.extend(parse_yjf(f, book_title=_book_title_for(f)))

        except Exception as exc:
            print(f"  [warn] Failed to parse {f}: {exc}", file=sys.stderr)

    return clippings, apnx_infos


# ---------------------------------------------------------------------------
# Exporters
# ---------------------------------------------------------------------------

def _clipping_to_dict(c: Clipping) -> dict:
    d = asdict(c)
    # Remove None values for cleaner JSON/CSV
    return {k: v for k, v in d.items() if v is not None and v != ""}


def export_json(clippings: List[Clipping], apnx_infos: List[APNXInfo], out: Path):
    payload = {
        "clippings": [_clipping_to_dict(c) for c in clippings],
        "page_indexes": [asdict(a) for a in apnx_infos],
        "exported_at": datetime.now().isoformat(),
        "total_clippings": len(clippings),
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(clippings)} clippings → {out}")


def export_csv(clippings: List[Clipping], out: Path):
    if not clippings:
        print("No clippings to export.")
        return
    fieldnames = [
        "book_title", "author", "clip_type", "page",
        "location_start", "location_end", "added_date", "content", "source_file",
    ]
    with out.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for c in clippings:
            writer.writerow(asdict(c))
    print(f"Wrote {len(clippings)} clippings → {out}")


def export_markdown(clippings: List[Clipping], out: Path):
    lines = ["# Kindle Clippings\n"]
    # Group by book
    books: dict[str, List[Clipping]] = {}
    for c in clippings:
        books.setdefault(c.book_title, []).append(c)

    for title, clips in books.items():
        author = clips[0].author
        header = f"## {title}"
        if author:
            header += f"  \n*{author}*"
        lines.append(header)
        lines.append("")

        for c in clips:
            if c.clip_type == "last_position":
                continue
            meta_parts = []
            if c.page:
                meta_parts.append(f"p. {c.page}")
            if c.location_start:
                loc = str(c.location_start)
                if c.location_end:
                    loc += f"–{c.location_end}"
                meta_parts.append(f"loc. {loc}")
            if c.added_date:
                meta_parts.append(c.added_date)
            meta = " | ".join(meta_parts)

            if c.clip_type == "highlight":
                lines.append(f"> {c.content}")
                if meta:
                    lines.append(f"> *— {meta}*")
            elif c.clip_type == "note":
                lines.append(f"**Note** ({meta}): {c.content}")
            elif c.clip_type == "bookmark":
                label = f": {c.content}" if c.content else ""
                lines.append(f"*Bookmark* ({meta}){label}")
            lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {len(clippings)} clippings → {out}")


def export_text(clippings: List[Clipping], out: Path):
    lines = []
    current_book = None
    for c in clippings:
        if c.clip_type == "last_position":
            continue
        if c.book_title != current_book:
            current_book = c.book_title
            lines.append("=" * 60)
            lines.append(c.book_title)
            if c.author:
                lines.append(f"by {c.author}")
            lines.append("=" * 60)
            lines.append("")

        loc = ""
        if c.page:
            loc += f"Page {c.page}  "
        if c.location_start:
            loc += f"Loc {c.location_start}"
            if c.location_end:
                loc += f"-{c.location_end}"
        if c.added_date:
            loc += f"  ({c.added_date})"
        if loc:
            lines.append(f"[{c.clip_type.upper()}] {loc.strip()}")
        if c.content:
            lines.append(c.content)
        lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {len(clippings)} clippings → {out}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Parse Kindle/Mobipocket clippings and export them.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "input",
        help="Input file or directory to scan (My Clippings.txt, *.mbp, *.apnx)",
    )
    parser.add_argument(
        "-o", "--output",
        required=True,
        help="Output file path",
    )
    parser.add_argument(
        "-f", "--format",
        choices=["json", "csv", "markdown", "text"],
        default=None,
        help="Output format (default: inferred from output file extension)",
    )
    parser.add_argument(
        "--ebook",
        default=None,
        metavar="EBOOK",
        help="Ebook file (KFX, AZW3, MOBI, EPUB …) to extract highlight text and "
             "page numbers from.  If omitted, the script tries to auto-detect a "
             "sibling ebook next to every .sdr folder it finds.",
    )
    parser.add_argument(
        "--no-text",
        action="store_true",
        help="Skip highlight text extraction (useful if Calibre is slow or unavailable).",
    )
    parser.add_argument(
        "--no-pages",
        action="store_true",
        help="Skip page-number extraction (requires KFX Input Calibre plugin).",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.output)

    # Infer format from extension if not specified
    fmt = args.format
    if fmt is None:
        ext_map = {".json": "json", ".csv": "csv", ".md": "markdown", ".txt": "text"}
        fmt = ext_map.get(out_path.suffix.lower(), "json")
        print(f"Output format: {fmt} (inferred from extension)")

    print(f"Scanning: {input_path}")
    clippings, apnx_infos = scan_path(input_path)
    print(f"Found {len(clippings)} clippings, {len(apnx_infos)} APNX page indexes")

    # --- Text extraction + page number population ---
    ebook_override = Path(args.ebook) if args.ebook else None

    # Group YJR/YJF clippings by their .sdr parent so we can find the paired ebook.
    sdr_to_clippings: dict[Path, List[Clipping]] = {}
    for c in clippings:
        src = Path(c.source_file)
        sdr = _find_sdr_parent(src)
        if sdr:
            sdr_to_clippings.setdefault(sdr, []).append(c)

    # If no .sdr groupings (e.g. My Clippings.txt only) but --ebook was given,
    # apply text extraction to all clippings.
    if not sdr_to_clippings and ebook_override:
        sdr_to_clippings[Path(".")] = clippings

    for sdr, group in sdr_to_clippings.items():
        ebook = ebook_override or (find_paired_ebook(sdr) if sdr != Path(".") else None)
        if not ebook:
            print(f"  [info] No ebook found next to {sdr.name} — skipping text/page extraction")
            continue
        if not ebook.exists():
            print(f"  [warn] Ebook not found: {ebook}", file=sys.stderr)
            continue

        if ebook.suffix.lower() == ".kfx":
            # For KFX files: use kfxlib for text, pages, and Kindle Locations in one pass.
            # kfxlib preserves the exact KFX internal char offsets used by YJR annotations,
            # whereas Calibre's ebook-convert introduces position drift.
            print(f"  Extracting text, page map, and Kindle Locations from: {ebook.name} …")
            page_map, kl_offsets, book_text = extract_kfx_info(ebook)

            if not args.no_text and book_text:
                before = sum(1 for c in group if c.content and c.content.startswith("["))
                fill_clipping_text(group, book_text)
                after  = sum(1 for c in group if c.content and not c.content.endswith("]"))
                print(f"  Filled text for {after - (len(group) - before)} highlights")
            elif not args.no_text:
                print(f"  [warn] Text extraction failed for {ebook.name}", file=sys.stderr)

            # fill_clipping_pages must use char offsets → call before KL conversion
            if not args.no_pages and page_map:
                fill_clipping_pages(group, page_map)
                paged = sum(1 for c in group if c.page is not None)
                print(f"  Assigned page numbers to {paged} clippings "
                      f"(pp. {page_map[0][0]}–{page_map[-1][0]})")

            # Convert char offsets → Kindle Location numbers for display
            if not args.no_pages and kl_offsets:
                fill_clipping_kindle_locations(group, kl_offsets)
                print(f"  Converted locations to Kindle Location numbers "
                      f"(1–{len(kl_offsets)})")
            elif not args.no_pages:
                print(f"  [info] No location map found in {ebook.name} (plugin may be missing)")

        else:
            # For non-KFX formats: use Calibre's ebook-convert for text extraction.
            # Page numbers and Kindle Locations are not available for these formats.
            if not args.no_text:
                print(f"  Extracting highlight text from: {ebook.name} …")
                book_text = extract_book_text(ebook)
                if book_text:
                    before = sum(1 for c in group if c.content and c.content.startswith("["))
                    fill_clipping_text(group, book_text)
                    after  = sum(1 for c in group if c.content and not c.content.endswith("]"))
                    print(f"  Filled text for {after - (len(group) - before)} highlights")
                else:
                    print(f"  [warn] Text extraction failed for {ebook.name}", file=sys.stderr)

    clippings.sort(key=lambda c: c.added_date or "")

    if fmt == "json":
        export_json(clippings, apnx_infos, out_path)
    elif fmt == "csv":
        export_csv(clippings, out_path)
    elif fmt == "markdown":
        export_markdown(clippings, out_path)
    elif fmt == "text":
        export_text(clippings, out_path)


if __name__ == "__main__":
    main()
