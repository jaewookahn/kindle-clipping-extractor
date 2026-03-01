"""Parser for Amazon Page Number Index (.apnx) files."""

import json
import struct
from pathlib import Path

from kindle.models import APNXInfo

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
