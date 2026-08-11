"""build_chapter_ranges — 챕터별 페이지·Location 범위 계산 테스트."""

from kindle.ebook import build_chapter_ranges

# extract_kfx_info() 가 돌려주는 형태
# 부모("1장")와 첫 자식("1장 › 1.1")이 같은 offset 을 가리키는 실제 KFX 패턴 포함
TOC = [
    (0,    "서문"),
    (100,  "1장"),
    (100,  "1장 › 1.1 도입"),
    (300,  "1장 › 1.2 본문"),
    (600,  "2장"),
]

# (page_label, char_offset)
PAGE_MAP = [("1", 0), ("2", 50), ("3", 100), ("4", 250), ("5", 400), ("6", 600)]

# index i → Kindle Location (i+1) 시작 offset
KL = [0, 80, 160, 240, 320, 480, 640]


def _by_title(chapters):
    return {c.title: c for c in chapters}


def test_empty_toc_returns_empty():
    assert build_chapter_ranges(None) == []
    assert build_chapter_ranges([]) == []


def test_char_ranges_use_next_non_descendant():
    """부모 챕터는 자식들을 모두 포함해야 한다."""
    ch = _by_title(build_chapter_ranges(TOC, text_len=1000))
    assert (ch["서문"].char_start, ch["서문"].char_end) == (0, 100)
    # "1장" 의 끝은 자식(1.1, 1.2)을 건너뛴 "2장" 의 시작
    assert (ch["1장"].char_start, ch["1장"].char_end) == (100, 600)
    assert (ch["1장 › 1.1 도입"].char_start, ch["1장 › 1.1 도입"].char_end) == (100, 300)
    assert (ch["1장 › 1.2 본문"].char_start, ch["1장 › 1.2 본문"].char_end) == (300, 600)


def test_last_chapter_ends_at_text_len():
    ch = _by_title(build_chapter_ranges(TOC, text_len=1000))
    assert ch["2장"].char_end == 1000


def test_parent_with_shared_offset_is_not_zero_length():
    """부모와 첫 자식이 같은 offset — 단순히 '다음 항목까지'로 자르면 길이 0 이 된다."""
    ch = _by_title(build_chapter_ranges(TOC, text_len=1000))
    assert ch["1장"].char_end > ch["1장"].char_start


def test_page_ranges():
    ch = _by_title(build_chapter_ranges(TOC, page_map=PAGE_MAP, text_len=1000))
    # 서문: char 0–99 → p.1 부터, 마지막 문자 99 는 p.2 구간(50–99)
    assert (ch["서문"].page_start, ch["서문"].page_end) == (1, 2)
    # 1장: char 100–599 → p.3 부터, 마지막 문자 599 는 p.5 구간(400–599)
    assert (ch["1장"].page_start, ch["1장"].page_end) == (3, 5)
    # 2장: char 600–999 → p.6
    assert (ch["2장"].page_start, ch["2장"].page_end) == (6, 6)


def test_page_end_does_not_bleed_into_next_chapter():
    """끝 offset 은 exclusive 이므로 마지막 문자로 조회해야 한다."""
    ch = _by_title(build_chapter_ranges(TOC, page_map=PAGE_MAP, text_len=1000))
    # 1장의 char_end 는 600 = 2장 시작(p.6). 1장 page_end 는 6이 아니라 5여야 함
    assert ch["1장"].page_end == 5


def test_location_ranges():
    ch = _by_title(build_chapter_ranges(TOC, kl_offsets=KL, text_len=1000))
    # KL = [0,80,160,240,320,480,640] → bisect_right
    # 서문 char 0 → KL 1 ; 마지막 문자 99 → KL 2
    assert (ch["서문"].location_start, ch["서문"].location_end) == (1, 2)
    # 2장 char 600 → KL 6 ; 마지막 문자 999 → KL 7
    assert (ch["2장"].location_start, ch["2장"].location_end) == (6, 7)


def test_missing_maps_leave_fields_none():
    ch = build_chapter_ranges(TOC, text_len=1000)
    assert all(c.page_start is None and c.location_start is None for c in ch)


def test_non_numeric_page_label_is_none():
    ch = _by_title(build_chapter_ranges(
        [(0, "머리말")], page_map=[("ix", 0)], text_len=100
    ))
    assert ch["머리말"].page_start is None


def test_level_and_leaf():
    ch = _by_title(build_chapter_ranges(TOC, text_len=1000))
    assert ch["서문"].level == 0
    assert ch["1장 › 1.1 도입"].level == 1
    assert ch["1장 › 1.1 도입"].leaf == "1.1 도입"


def test_no_text_len_falls_back_to_last_offset():
    ch = _by_title(build_chapter_ranges(TOC))
    assert ch["2장"].char_end == 600      # 마지막 TOC offset, 음수 길이 아님
    assert ch["2장"].char_end >= ch["2장"].char_start
