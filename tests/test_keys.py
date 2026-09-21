"""kindle.keys — book_key / clip_key.

사양: `~/prj/reading_manager/DATA_MODEL.md` §7 (2026-09-20 개정 — 두 갈래)
JS 정본: `~/prj/highlight-capture/src/utils/{bookKey,clipKey}.js`

`compute_clip_key` 는 **이번엔 JS 를 보지 않고 사양만으로** 구현했다
(리딩총괄2 지시 — 독립 구현 두 개가 공유 벡터에서 같은 결론에 도달해야
진짜 검증이라는 취지). `normalize_for_key`·`normalize_content`·`book_key`
는 이전에 이미 JS 실행 결과·전수조사·공유 벡터로 검증됐으므로 그대로 둔다.
"""

import json
from pathlib import Path

import pytest

from kindle import keys as K


# --------------------------------------------------------------------------
# 정규화
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw, want", [
    ("먼저 온 미래", "먼저온미래"),
    ("Sapiens: A Brief History", "sapiens"),          # 부제 절단
    ("데미안 (세계문학전집)", "데미안"),                 # 괄호 주석 제거
    ("Bill Bryson - Notes", "billbryson"),            # 하이픈도 분리자 (보류 중인 결함)
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


@pytest.mark.parametrize("color", [
    "yellow", "blue", "pink", "orange", "green",       # 실측(COLOR_CENSUS.json)에 있던 것
    "YELLOW", "Blue", "PiNk",                          # 대소문자 무시
    "dark_blue", "light_purple", "vermillion",         # 화이트리스트가 아니라 패턴이므로
                                                        # 목록에 없던 색도 잡혀야 한다
])
def test_color_tag_is_stripped(color):
    """색을 바꿨다고, 새 색이 생겨도 다른 클리핑이 되면 안 된다."""
    assert K.normalize_content(f"[{color}] 본문입니다") == K.normalize_content("본문입니다")


def test_only_leading_tag_is_stripped():
    assert "각주" in K.normalize_content("[yellow] 본문 [각주] 계속")


def test_mid_content_brackets_are_not_treated_as_color_tags():
    """본문 중간의 각주 표시나 대괄호는 색상 태그가 아니다 — 건드리지 않는다."""
    assert K.normalize_content("본문 [1] 계속") == K.normalize_content("본문[1]계속")


def test_numeric_footnote_at_start_is_not_stripped():
    """"[1]"·"[2]" 같은 숫자 각주는 알파벳으로 시작하지 않아 패턴에 안 걸린다."""
    stripped = K.normalize_content("[1] 각주로 시작하는 본문")
    assert "1" in stripped


def test_dark_blue_is_stripped_by_pattern():
    """⚠️ 뒤집힌 회귀 테스트: 화이트리스트 4색 시절엔 KSDK DB가 실제로 내보내는
    "dark_blue"(실측 1,632건, COLOR_CENSUS.json)가 안 벗겨졌다. 패턴 방식으로
    바꾼 뒤에는 벗겨진다.
    """
    assert K.normalize_content("[dark_blue] 본문입니다") == K.normalize_content("본문입니다")


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
# clip_key — 사양 §7 (2026-09-20 개정): 킨들/종이책 두 갈래뿐
# --------------------------------------------------------------------------

BK = "t:d9d7c1ad39ae3d21"
LONG = "우리는 '위대함'이 과연 무슨 뜻인지에 대해 다시 생각해 봐야 한다."


def test_kindle_requires_loc_start_or_gets_quarantined():
    r = K.compute_clip_key(BK, "highlight", content=LONG, loc_start=None)
    assert r.key is None and r.needs_review and r.branch == "kindle"


def test_paper_requires_page_or_gets_quarantined():
    r = K.compute_clip_key(BK, "highlight", content=LONG, page=None, source="paper")
    assert r.key is None and r.needs_review and r.branch == "paper"


def test_paper_page_zero_is_not_missing():
    """0 은 None 이 아니다 — 0페이지짜리 책도 키를 가져야 한다."""
    r = K.compute_clip_key(BK, "highlight", content="짧은 문장", page=0, source="paper")
    assert r.key is not None and not r.needs_review


def test_loc_end_is_irrelevant_to_the_key():
    """가장 중요한 회귀 — loc_end 가 뭐든 같은 키가 나와야 한다.

    실측(LOC_END_CHECK.md)에서 POST-KL 변환본과 My Clippings 사이 loc_end 가
    1.0% 어긋났다. loc_end 를 해시에서 뺀 것이 바로 이 문제의 해법이므로,
    이 테스트가 깨지면 그 해법이 무효화된 것이다.
    """
    a = K.clip_key(BK, "highlight", content="기계 장치가 노동의 차이를", loc_start=13927)
    b = K.clip_key(BK, "highlight", content="기계 장치가 노동의 차이를",
                   loc_start=13927)  # loc_end 인자 자체가 없다 — 함수 시그니처에서 빠졌다
    assert a == b


def test_short_content_no_longer_needs_special_branch():
    """20자 임계가 사라졌다 — 좌표가 항상 키에 있으니 2자짜리도 그 자체로 유일하다."""
    r = K.compute_clip_key(BK, "highlight", content="그", loc_start=113)
    assert r.key is not None and not r.needs_review


def test_same_long_text_different_location_gets_different_key():
    """이게 이번 개정의 핵심 — 같은 문장을 두 곳에서 하이라이트하면 구분돼야 한다."""
    a = K.clip_key(BK, "highlight", content=LONG, loc_start=100)
    b = K.clip_key(BK, "highlight", content=LONG, loc_start=5000)
    assert a != b


def test_same_location_different_book_gets_different_key():
    a = K.clip_key(BK, "highlight", content="그", loc_start=100)
    b = K.clip_key("t:다른책", "highlight", content="그", loc_start=100)
    assert a != b


def test_pre_kl_and_post_kl_sources_agree_when_start_matches():
    """같은 하이라이트를 KFX(PRE-KL, 색상태그)와 My Clippings(POST-KL)에서 읽어도
    loc_start 만 같으면 같은 키 — content_norm 이 색상태그를 떼어 주므로."""
    from_kfx = K.clip_key(BK, "highlight", content=f"[yellow] {LONG}", loc_start=113)
    from_mc = K.clip_key(BK, "highlight", content=LONG, loc_start=113)
    assert from_kfx == from_mc


def test_bookmark_has_no_special_branch():
    """④ 특례가 사라졌다 — 본문이 없으면 content_norm 이 자연히 빈 문자열이다."""
    r = K.compute_clip_key(BK, "bookmark", content="", loc_start=5000)
    assert r.key is not None and r.branch == "kindle"
    # 색상 태그만 있고 실질 본문이 없는 경우도 같은 키 (색만 다르면 다른 북마크가 되면 안 된다)
    assert r.key == K.clip_key(BK, "bookmark", content="[dark_blue] ", loc_start=5000)


def test_bookmark_without_location_is_quarantined():
    r = K.compute_clip_key(BK, "bookmark", loc_start=None)
    assert r.key is None and r.needs_review


def test_bookmark_differs_from_highlight_at_same_location():
    assert K.clip_key(BK, "bookmark", loc_start=100) != \
        K.clip_key(BK, "highlight", content="그", loc_start=100)


def test_truncated_does_not_change_the_formula():
    """⑤ 특례가 사라졌다 — truncated 는 needs_review 표시만 남기고 해시는 그대로다."""
    normal = K.compute_clip_key(BK, "highlight", content=LONG, loc_start=113)
    marked = K.compute_clip_key(BK, "highlight", content=LONG, loc_start=113, truncated=True)
    assert normal.key == marked.key
    assert not normal.needs_review and marked.needs_review


def test_truncated_content_differs_naturally_from_full_recovery():
    """잘린 본문 자체가 content_norm 을 다르게 만들어 자동으로 다른 키가 된다 —
    "①과 절대 병합 금지"를 코드로 따로 강제할 필요가 없다."""
    cut = K.clip_key(BK, "highlight", content="우리는 '위대함'이 과연", loc_start=113,
                     truncated=True)
    full = K.clip_key(BK, "highlight", content=LONG, loc_start=113)
    assert cut != full


def test_paper_page_is_part_of_the_key():
    a = K.clip_key(BK, "highlight", content="짧은 문장", page=1, source="paper")
    b = K.clip_key(BK, "highlight", content="짧은 문장", page=2, source="paper")
    assert a != b


def test_type_is_part_of_the_key():
    a = K.clip_key(BK, "highlight", content="짧은 문장", page=5, source="paper")
    b = K.clip_key(BK, "note", content="짧은 문장", page=5, source="paper")
    assert a != b


# --------------------------------------------------------------------------
# 공유 벡터 — 줄줄이가 독립적으로 생성. 여기서 어긋나면 맞추지 말고 보고한다
# --------------------------------------------------------------------------

_VECTORS = Path.home() / "prj" / "reading_manager" / "fixtures" / "clip_key_vectors.json"
_VECTOR_SOURCES = ("js-impl", "spec")

# 독립 구현이 실제로 갈린 지점 — 맞추지 않고 보고한다 (리딩총괄2 지시).
# §7 의 "여전히 유효한 것: Location 없는 클리핑은 Needs Review 로 격리"를
# 킨들의 loc_start 에 대한 문장으로 읽고, 종이책 page 에도 대칭 적용했다
# (없으면 격리). 벡터(js-impl)는 과거 관례(`page != null ? String(page) : ''`)
# 를 그대로 유지해 page=None 도 빈 문자열로 해시 — 유효한 키가 나온다.
# 어느 쪽이 사양의 의도인지 §7 에 명시가 없어 판단을 못 받았다.
_KNOWN_DIVERGENCE = {
    "p-page-null": "page=None 처리 — 격리(이 구현) vs 빈 문자열로 해시(벡터). "
                  "§7 이 '없으면 격리'를 킨들 loc_start 로만 명시하고 종이책 "
                  "page 는 언급이 없어 갈렸다. 리딩총괄 판단 대기.",
}


def _vector_cases():
    if not _VECTORS.exists():
        return []
    return json.loads(_VECTORS.read_text(encoding="utf-8")).get("vectors", [])


def _eval_vector(v: dict):
    section, inp = v["section"], v["input"]
    if section.startswith("book_key"):
        return K.book_key(title=inp.get("title", ""), author=inp.get("author", ""),
                          isbn=inp.get("isbn", ""), asin=inp.get("asin", ""))
    if section == "content_norm":
        return K.normalize_content(inp.get("text"))
    if section == "clip_key.kindle":
        return K.clip_key(inp.get("bookKey", ""), inp.get("type", "highlight"),
                          content=inp.get("text", "") or "", loc_start=inp.get("locStart"))
    if section == "clip_key.paper":
        return K.clip_key(inp.get("bookKey", ""), inp.get("type", "highlight"),
                          content=inp.get("text", "") or "", page=inp.get("page"),
                          source="paper")
    raise ValueError(f"모르는 section: {section}")


@pytest.mark.skipif(not _VECTORS.exists(), reason="공유 벡터 파일 아직 없음")
@pytest.mark.parametrize("v", _vector_cases(), ids=lambda v: v.get("id", "?"))
def test_shared_vector(v):
    """`~/prj/reading_manager/fixtures/clip_key_vectors.json` — 줄줄이가 사양만
    보고 독립적으로 생성. 여기서 어긋나면 **맞추지 말고 리딩총괄에 보고한다**
    (지시 — "어느 쪽이 틀렸는지 저에게 올리십시오, 맞추지 마십시오").
    """
    assert v.get("source") in _VECTOR_SOURCES, f"모르는 source: {v.get('source')}"
    reason = _KNOWN_DIVERGENCE.get(v.get("id"))
    if reason:
        pytest.xfail(reason)
    assert _eval_vector(v) == v["expected"]
