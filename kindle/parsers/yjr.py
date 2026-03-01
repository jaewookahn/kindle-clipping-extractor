"""Parser for Kindle sidecar annotation files (.yjr, .yjf)."""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

from kindle.models import Clipping

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
    """Decode 8-byte big-endian ms-since-epoch to local-time string."""
    try:
        ms = int.from_bytes(raw8, "big")
        if ms == 0 or ms > 4_000_000_000_000:
            return None
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S")
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
