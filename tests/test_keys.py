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


def test_color_tag_is_stripped():
    """색을 바꿨다고 다른 클리핑이 되면 안 된다."""
    assert K.normalize_content("[yellow] 본문입니다") == K.normalize_content("본문입니다")
    assert K.normalize_content("[dark_blue] 본문입니다") == K.normalize_content("본문입니다")


def test_only_leading_tag_is_stripped():
    assert "각주" in K.normalize_content("[yellow] 본문 [각주] 계속")


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


@pytest.mark.skipif(not _VECTORS.exists(), reason="공유 벡터 파일 아직 없음")
def test_shared_vectors():
    data = json.loads(_VECTORS.read_text(encoding="utf-8"))
    bad = []
    for case in data.get("cases", data if isinstance(data, list) else []):
        want = case.get("clip_key") or case.get("expected")
        if not want:
            continue
        got = K.clip_key(
            case.get("book_key", ""),
            case.get("type", "highlight"),
            content=case.get("content", "") or case.get("text", "") or "",
            page=case.get("page"),
            loc_start=case.get("loc_start"),
            loc_end=case.get("loc_end"),
            source=case.get("source", "kindle"),
            truncated=bool(case.get("truncated")),
        )
        if got != want:
            bad.append((case, want, got))
    assert not bad, f"{len(bad)}건 불일치: {bad[:3]}"
