"""kindle.keys — book_key / clip_key.

사양: `~/prj/reading_manager/DATA_MODEL.md` §7
JS 정본: `~/prj/highlight-capture/src/utils/{bookKey,clipKey}.js`

**JS 와 한 글자도 다르면 안 된다.** 여기 박아 둔 기대값은 JS 정본을 node 로
실행해 얻은 것이다 (102건 대조, 불일치 0). 공유 벡터 파일이 올라오면
`test_shared_vectors` 가 자동으로 그쪽도 검증한다.
"""

import json
from pathlib import Path

import pytest

from kindle import keys as K


# --------------------------------------------------------------------------
# 정규화 — JS 와 동일해야 한다
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw, want", [
    ("먼저 온 미래", "먼저온미래"),
    ("Sapiens: A Brief History", "sapiens"),          # 부제 절단
    ("데미안 (세계문학전집)", "데미안"),                 # 괄호 주석 제거
    ("Bill Bryson - Notes", "billbryson"),            # 하이픈도 분리자 (JS 그대로)
    ("카프카 – 변신", "카프카"),                        # en dash
    ("제목—부제", "제목"),                              # em dash
    ("Ｈｅｌｌｏ　Ｗｏｒｌｄ", "helloworld"),              # NFKC + 전각 공백
    ("ﾃｽﾄ", "テスト"),                                  # NFKC 반각 카나
    ("", ""),
    (None, ""),
])
def test_normalize_for_key(raw, want):
    assert K.normalize_for_key(raw) == want


@pytest.mark.parametrize("raw, want", [
    ("Hello, World! —— test.", "helloworldtest"),
    ("  spaced  out  ", "spacedout"),
    ("«guillemets» and ±symbols", "guillemetsandsymbols"),   # P·S 카테고리 제거
    ("［전각］괄호", "전각괄호"),
    ("", ""),
    (None, ""),
])
def test_normalize_content(raw, want):
    assert K.normalize_content(raw) == want


@pytest.mark.parametrize("color", ["yellow", "blue", "pink", "orange",
                                   "YELLOW", "Blue", "PiNk"])
def test_color_tag_is_stripped(color):
    """색을 바꿨다고 다른 클리핑이 되면 안 된다. 사양이 정한 4색, 대소문자 무시."""
    assert K.normalize_content(f"[{color}] 본문입니다") == K.normalize_content("본문입니다")


def test_only_leading_tag_is_stripped():
    assert "각주" in K.normalize_content("[yellow] 본문 [각주] 계속")


def test_mid_content_brackets_are_not_treated_as_color_tags():
    """본문 중간의 각주 표시나 대괄호는 색상 태그가 아니다 — 건드리지 않는다."""
    assert K.normalize_content("본문 [1] 계속") == K.normalize_content("본문[1]계속")


def test_dark_blue_is_not_in_the_spec_color_set():
    """⚠️ 실측 불일치: KSDK DB(kindle/ksdk.py)는 실제로 "dark_blue"를 내보내는데
    (tests/test_ksdk.py 참조) 사양(DATA_MODEL.md §7)이 정한 색상 넷은
    yellow·blue·pink·orange 뿐이라 "dark_blue"는 포함되지 않는다. 사양을
    임의로 늘리지 않고 그대로 따랐다 — "dark_blue" 태그는 안 벗겨진다.
    """
    stripped = K.normalize_content("[dark_blue] 본문입니다")
    bare = K.normalize_content("본문입니다")
    assert stripped != bare
    assert "darkblue" in stripped


# --------------------------------------------------------------------------
# book_key
# --------------------------------------------------------------------------

def test_book_key_prefers_isbn():
    assert K.book_key(title="아무개", isbn="978-89-01-12345-x",
                      asin="B001") == "isbn:978890112345X"


def test_book_key_then_asin():
    assert K.book_key(title="아무개", asin="  B000FCK3C8 ") == "asin:B000FCK3C8"


def test_book_key_title_fallback_matches_js():
    # node 로 JS 정본을 실행해 얻은 값 (scratchpad/parity.mjs)
    assert K.book_key(title="먼저 온 미래", author="장강명") == "t:8ada8f4c2f69d2ce"


def test_book_key_ignores_subtitle_and_parens():
    a = K.book_key(title="데미안", author="헤르만 헤세")
    b = K.book_key(title="데미안 (세계문학전집): 개정판", author="헤르만 헤세")
    assert a == b


def test_book_key_empty_is_stable():
    assert K.book_key().startswith("t:")


# --------------------------------------------------------------------------
# clip_key — 갈래별
# --------------------------------------------------------------------------

BK = "t:d9d7c1ad39ae3d21"
LONG = "우리는 '위대함'이 과연 무슨 뜻인지에 대해 다시 생각해 봐야 한다."


def test_branch1_long_text_uses_content_only():
    r = K.compute_clip_key(BK, "highlight", content=LONG, loc_start=5391, loc_end=17951)
    assert r.branch == "1" and not r.needs_review
    # 좌표가 완전히 달라도 같은 키 — 이것이 교차 소스 dedup 의 전부다
    other = K.compute_clip_key(BK, "highlight", content=LONG, loc_start=113, loc_end=358)
    assert r.key == other.key


def test_branch1_matches_js():
    assert K.clip_key(BK, "highlight", content=LONG) == \
        "bc675041cae3c57d10b9ab796b708ef39ff39b45"


def test_pre_kl_and_post_kl_sources_agree():
    """같은 하이라이트를 KFX(PRE-KL, 색상태그)와 My Clippings(POST-KL)에서 읽어도 같은 키."""
    from_kfx = K.clip_key(BK, "highlight", content=f"[yellow] {LONG}",
                          loc_start=5391, loc_end=17951)
    from_mc = K.clip_key(BK, "highlight", content=LONG, loc_start=113, loc_end=358)
    assert from_kfx == from_mc


def test_branch2_short_kindle_uses_location():
    r = K.compute_clip_key(BK, "highlight", content="그", loc_start=113, loc_end=113)
    assert r.branch == "2"
    # 같은 한 글자라도 위치가 다르면 다른 클리핑
    other = K.compute_clip_key(BK, "highlight", content="그", loc_start=999, loc_end=999)
    assert r.key != other.key


def test_branch3_paper_uses_page():
    r = K.compute_clip_key(BK, "highlight", content="짧다", page=42, source="paper")
    assert r.branch == "3"
    assert r.key != K.clip_key(BK, "highlight", content="짧다", page=43, source="paper")


def test_threshold_is_19_vs_20():
    assert K.compute_clip_key(BK, "highlight", content="a" * 19, loc_start=1).branch == "2"
    assert K.compute_clip_key(BK, "highlight", content="a" * 20).branch == "1"


def test_threshold_counts_normalized_length():
    """공백·문장부호는 길이에 안 들어간다."""
    assert K.compute_clip_key(BK, "highlight",
                              content="a b c d e f g h i j!", loc_start=1).branch == "2"


# --------------------------------------------------------------------------
# ④ 북마크 — 회귀하면 이미 동기화한 북마크가 전부 신규로 잡힌다
# --------------------------------------------------------------------------

def test_bookmark_loc_end_is_normalized_away():
    """KSDK 는 end == start 로 채우고 parse_yjr 은 None 을 둔다. 실측 12건이 어긋났다."""
    from_ksdk = K.clip_key(BK, "bookmark", loc_start=5000, loc_end=5000)
    from_yjr = K.clip_key(BK, "bookmark", loc_start=5000, loc_end=None)
    assert from_ksdk == from_yjr


def test_bookmark_ignores_any_loc_end():
    a = K.clip_key(BK, "bookmark", loc_start=5000, loc_end=9999)
    assert a == K.clip_key(BK, "bookmark", loc_start=5000)


def test_missing_value_renders_as_empty_string_not_literal_none():
    """DATA_MODEL.md §7 (2026-09-20 명시): 빈 자리는 "" 다. "None"·"null" 아니다."""
    import hashlib
    want = hashlib.sha1(f"{BK}|bookmark|5000|".encode("utf-8")).hexdigest()
    assert K.clip_key(BK, "bookmark", loc_start=5000, loc_end=None) == want
    # "None" 문자열을 넣은 것과는 달라야 한다 — 그게 회귀다
    wrong = hashlib.sha1(f"{BK}|bookmark|5000|None".encode("utf-8")).hexdigest()
    assert K.clip_key(BK, "bookmark", loc_start=5000, loc_end=None) != wrong


def test_bookmark_ignores_content():
    assert K.clip_key(BK, "bookmark", content="[dark_blue] ", loc_start=7) == \
        K.clip_key(BK, "bookmark", loc_start=7)


def test_bookmark_without_location_is_quarantined():
    r = K.compute_clip_key(BK, "bookmark", loc_start=None)
    assert r.key is None and r.needs_review and r.branch == "4"


def test_bookmark_differs_from_highlight_at_same_location():
    assert K.clip_key(BK, "bookmark", loc_start=100) != \
        K.clip_key(BK, "highlight", content="그", loc_start=100, loc_end=None)


# --------------------------------------------------------------------------
# ⑤ 절단 — ① 과 절대 병합 금지
# --------------------------------------------------------------------------

def test_truncated_never_merges_with_branch1():
    full = K.compute_clip_key(BK, "highlight", content=LONG, loc_start=113, loc_end=358)
    cut = K.compute_clip_key(BK, "highlight", content=LONG, loc_start=113, loc_end=358,
                             truncated=True)
    assert cut.branch == "5" and cut.needs_review
    assert cut.key != full.key


def test_truncated_uses_same_formula_as_branch2():
    cut = K.clip_key(BK, "highlight", content=LONG, loc_start=113, loc_end=358,
                     truncated=True)
    short = K.clip_key(BK, "highlight", content="그", loc_start=113, loc_end=358)
    assert cut == short          # ② 와 같은 식 (본문을 안 쓴다)


# --------------------------------------------------------------------------
# 공유 벡터 — 줄줄이가 올리면 자동으로 걸린다
# --------------------------------------------------------------------------

_VECTORS = Path.home() / "prj" / "reading_manager" / "fixtures" / "clip_key_vectors.json"

# 실제 스키마 (줄줄이 세션이 생성). 이전 판은 스키마를 추측해 넣은 것이라 파일이
# 도착한 뒤 실물 구조({"vectors": [{"section", "id", "input", "expected"}, …]})에
# 맞춰 다시 썼다. `section` 접두로 어느 함수를 태울지 정한다 — book_key.* /
# content_norm / clip_key.branch{1..5}_*.
_VECTOR_SOURCES = ("js-impl", "spec")   # 둘 다 정본 — js-impl 은 실행 결과, spec 은 식 유도


def _vector_cases():
    if not _VECTORS.exists():
        return []
    data = json.loads(_VECTORS.read_text(encoding="utf-8"))
    return data.get("vectors", [])


def _eval_vector(v: dict):
    section, inp = v["section"], v["input"]
    if section.startswith("book_key"):
        return K.book_key(title=inp.get("title", ""), author=inp.get("author", ""),
                          isbn=inp.get("isbn", ""), asin=inp.get("asin", ""))
    if section == "content_norm":
        return K.normalize_content(inp.get("text"))
    if section.startswith("clip_key"):
        kwargs = dict(
            bk=inp.get("bookKey", ""),
            clip_type=inp.get("type", "highlight"),
            content=inp.get("text", "") or "",
            page=inp.get("page"),
            loc_start=inp.get("locStart"),
            loc_end=inp.get("locEnd"),
        )
        if "_paper_" in section:
            kwargs["source"] = "paper"
        if section.endswith("branch5_truncated"):
            kwargs["truncated"] = True   # needsReview 는 해시에 안 들어간다 — 벡터 note 참조
        return K.clip_key(**kwargs)
    raise ValueError(f"모르는 section: {section}")


@pytest.mark.skipif(not _VECTORS.exists(), reason="공유 벡터 파일 아직 없음")
@pytest.mark.parametrize("v", _vector_cases(), ids=lambda v: v.get("id", "?"))
def test_shared_vector(v):
    """`~/prj/reading_manager/fixtures/clip_key_vectors.json` — 줄줄이가 생성.

    이 저장소가 고른 102건 표본과 달리, 경계값(20/19자)·색상 4종 전부·대소문자·
    공백 유무·본문 중간 대괄호 등 **의도된 함정**을 담고 있다. 둘 다 통과해야
    ①이 끝난다 (리딩총괄2 지시).
    """
    assert v.get("source") in _VECTOR_SOURCES, f"모르는 source: {v.get('source')}"
    assert _eval_vector(v) == v["expected"]
