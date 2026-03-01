"""Parser for Amazon's My Clippings.txt format."""

import re
from pathlib import Path
from typing import List

from kindle.models import Clipping

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
