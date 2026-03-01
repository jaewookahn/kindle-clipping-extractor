from dataclasses import dataclass, field
from typing import Optional, List


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
