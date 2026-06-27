"""fill_clipping_chapters — TOC breadcrumb 매핑 단위 테스트."""

from kindle.ebook import fill_clipping_chapters
from kindle.models import Clipping


# (char_offset, breadcrumb) — extract_kfx_info 가 돌려주는 형태
TOC = [
    (0,    "서문"),
    (100,  "1장 시작 › 1.1 도입"),
    (300,  "1장 시작 › 1.2 본문"),
    (600,  "2장 전개"),
]


def _clip(loc):
    return Clipping(book_title="책", clip_type="highlight", location_start=loc)


def test_maps_to_last_entry_at_or_before_offset():
    clips = [_clip(0), _clip(150), _clip(350), _clip(700)]
    fill_clipping_chapters(clips, TOC)
    assert [c.chapter for c in clips] == [
        "서문",
        "1장 시작 › 1.1 도입",
        "1장 시작 › 1.2 본문",
        "2장 전개",
    ]


def test_offset_before_first_entry_stays_none():
    # 첫 TOC 항목이 0이 아닐 때, 그 앞 오프셋은 챕터 없음
    toc = [(100, "1장")]
    c = _clip(50)
    fill_clipping_chapters([c], toc)
    assert c.chapter is None


def test_boundary_is_inclusive_on_start():
    c = _clip(300)
    fill_clipping_chapters([c], TOC)
    assert c.chapter == "1장 시작 › 1.2 본문"


def test_none_location_skipped():
    c = Clipping(book_title="책", clip_type="bookmark", location_start=None)
    fill_clipping_chapters([c], TOC)
    assert c.chapter is None


def test_empty_toc_is_noop():
    c = _clip(150)
    fill_clipping_chapters([c], [])
    assert c.chapter is None


def test_existing_chapter_not_overwritten():
    c = _clip(150)
    c.chapter = "수동 지정"
    fill_clipping_chapters([c], TOC)
    assert c.chapter == "수동 지정"
