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
    recovered: bool = False      # True if content was recovered from ebook file
    chapter: Optional[str] = None  # TOC breadcrumb (e.g. "1장 › 1. 서론"), KFX only


@dataclass
class Chapter:
    """한 챕터(TOC 항목)가 차지하는 범위.

    KFX TOC($212)는 챕터 시작 위치만 알려준다. 끝 위치는 "다음에 오는,
    자기 자손이 아닌 항목"의 시작으로 잡는다. 이렇게 해야 부모 챕터가
    자식들을 모두 포함하는 범위를 갖는다 (부모와 첫 자식은 보통 같은
    char offset 을 가리키므로, 단순히 다음 항목까지로 자르면 부모 범위가
    길이 0 이 되어 버린다).

    title 은 breadcrumb (예: "1장 시작 › 1.1 도입") — 중첩 경로가 그대로
    들어 있어 계층이 모호하지 않다.
    """
    title: str                          # breadcrumb, " › " 로 연결
    char_start: int = 0                 # KFX 내부 char offset (raw)
    char_end: int = 0                   # exclusive
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    location_start: Optional[int] = None   # Kindle Location 번호
    location_end: Optional[int] = None

    @property
    def level(self) -> int:
        """중첩 깊이 (최상위 = 0)."""
        return self.title.count(" › ")

    @property
    def leaf(self) -> str:
        """breadcrumb 의 마지막 조각 (그 챕터 자신의 제목)."""
        return self.title.rsplit(" › ", 1)[-1]


@dataclass
class APNXInfo:
    """Not a clipping source — carries page-number index metadata."""
    asin: str = ""
    content_guid: str = ""
    page_count: int = 0
    page_offsets: List[int] = field(default_factory=list)
    source_file: str = ""
