"""kindle.text_cache — KFX 추출 결과 디스크 캐시.

실제 KFX 없이 돈다. 추출기는 가짜 함수로 주입한다.
"""

import gzip
import json

import pytest

from kindle import text_cache as tc


EXTRACTION = (
    [("13", 100), ("14", 200)],      # page_map
    [0, 50, 100, 150],               # kl_offsets
    "가나다라마바사아자차",            # book_text
    [(0, "1장"), (5, "1장 › 1.1")],   # toc
)


@pytest.fixture
def cache_dir(tmp_path):
    return tmp_path / "textcache"


@pytest.fixture
def kfx(tmp_path):
    p = tmp_path / "book.kfx"
    p.write_bytes(b"x" * 1234)
    return p


# --------------------------------------------------------------------------
# 키
# --------------------------------------------------------------------------

def test_key_prefers_asin(kfx):
    assert tc.cache_key("ASIN1", kfx) == "ASIN1"


def test_key_falls_back_to_path_hash(kfx):
    k = tc.cache_key("", kfx)
    assert k.startswith("p_") and len(k) > 4


def test_same_book_different_paths_share_key(tmp_path):
    """같은 ASIN 이면 경로가 달라도 한 항목 — 마운트·MTP 사본을 따로 추출하지 않는다."""
    a = tmp_path / "a" / "book.kfx"
    b = tmp_path / "b" / "book.kfx"
    assert tc.cache_key("ASIN1", a) == tc.cache_key("ASIN1", b)


def test_key_rejects_empty_everything():
    with pytest.raises(ValueError):
        tc.cache_key("", None)


def test_key_sanitizes_weird_asin(kfx):
    assert "/" not in tc.cache_key("A/B C", kfx)


# --------------------------------------------------------------------------
# 저장·로드
# --------------------------------------------------------------------------

def test_roundtrip(cache_dir, kfx):
    assert tc.store("K", EXTRACTION, kfx_path=kfx, asin="ASIN1", cache_dir=cache_dir)
    pm, kl, text, toc = tc._as_extraction(tc.load("K", cache_dir))
    assert text == EXTRACTION[2]
    assert kl == EXTRACTION[1]
    assert pm == EXTRACTION[0]          # 리스트가 아니라 튜플로 복원된다
    assert toc == EXTRACTION[3]


def test_tuples_restored_not_lists(cache_dir, kfx):
    tc.store("K", EXTRACTION, kfx_path=kfx, cache_dir=cache_dir)
    pm, _, _, toc = tc._as_extraction(tc.load("K", cache_dir))
    assert isinstance(pm[0], tuple) and isinstance(toc[0], tuple)


def test_records_source_size(cache_dir, kfx):
    tc.store("K", EXTRACTION, kfx_path=kfx, cache_dir=cache_dir)
    assert tc.load("K", cache_dir)["source_size"] == 1234


def test_empty_text_is_not_stored(cache_dir, kfx):
    """빈 추출을 캐시하면 실패가 영구화된다 (2026-09-19 사고)."""
    assert tc.store("K", (None, None, "", None), kfx_path=kfx, cache_dir=cache_dir) is False
    assert tc.load("K", cache_dir) is None


def test_load_missing_returns_none(cache_dir):
    assert tc.load("없음", cache_dir) is None


def test_corrupt_entry_returns_none(cache_dir):
    p = tc.entry_path("K", cache_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"not gzip")
    assert tc.load("K", cache_dir) is None


def test_version_mismatch_returns_none(cache_dir, kfx):
    tc.store("K", EXTRACTION, kfx_path=kfx, cache_dir=cache_dir)
    p = tc.entry_path("K", cache_dir)
    with gzip.open(p, "rt", encoding="utf-8") as f:
        d = json.load(f)
    d["version"] = 99
    with gzip.open(p, "wt", encoding="utf-8") as f:
        json.dump(d, f)
    assert tc.load("K", cache_dir) is None


def test_no_tmp_file_left_behind(cache_dir, kfx):
    tc.store("K", EXTRACTION, kfx_path=kfx, cache_dir=cache_dir)
    assert not list(cache_dir.glob("*.tmp"))


# --------------------------------------------------------------------------
# get_or_extract — 판정 규칙
# --------------------------------------------------------------------------

def _counting_extractor(result=EXTRACTION):
    calls = []

    def ex(path):
        calls.append(path)
        return result
    ex.calls = calls
    return ex


def test_first_call_extracts_then_caches(cache_dir, kfx):
    ex = _counting_extractor()
    r1, s1 = tc.get_or_extract(kfx, ex, asin="ASIN1", cache_dir=cache_dir)
    r2, s2 = tc.get_or_extract(kfx, ex, asin="ASIN1", cache_dir=cache_dir)
    assert (s1, s2) == ("extract", "cache")
    assert len(ex.calls) == 1            # 두 번째는 추출하지 않는다
    assert r2[2] == EXTRACTION[2]


def test_missing_file_still_serves_cache(cache_dir, kfx):
    """이 캐시의 존재 이유 — 원본이 사라져도 본문이 나와야 한다."""
    ex = _counting_extractor()
    tc.get_or_extract(kfx, ex, asin="ASIN1", cache_dir=cache_dir)
    kfx.unlink()
    r, src = tc.get_or_extract(kfx, ex, asin="ASIN1", cache_dir=cache_dir)
    assert src == "cache" and r[2] == EXTRACTION[2]
    assert len(ex.calls) == 1


def test_size_change_triggers_reextract(cache_dir, kfx):
    ex = _counting_extractor()
    tc.get_or_extract(kfx, ex, asin="ASIN1", cache_dir=cache_dir)
    kfx.write_bytes(b"y" * 4321)          # 다른 판본으로 교체된 상황
    _, src = tc.get_or_extract(kfx, ex, asin="ASIN1", cache_dir=cache_dir)
    assert src == "extract" and len(ex.calls) == 2


def test_refresh_forces_reextract(cache_dir, kfx):
    ex = _counting_extractor()
    tc.get_or_extract(kfx, ex, asin="ASIN1", cache_dir=cache_dir)
    _, src = tc.get_or_extract(kfx, ex, asin="ASIN1", refresh=True, cache_dir=cache_dir)
    assert src == "extract" and len(ex.calls) == 2


def test_failed_extract_falls_back_to_cache(cache_dir, kfx):
    """마운트가 죽어 추출이 빈 결과를 주면 캐시로 메운다."""
    good = _counting_extractor()
    tc.get_or_extract(kfx, good, asin="ASIN1", cache_dir=cache_dir)
    kfx.write_bytes(b"z" * 999)           # 크기가 바뀌어 재추출을 시도하게 만든다
    dead = _counting_extractor((None, None, None, None))
    r, src = tc.get_or_extract(kfx, dead, asin="ASIN1", cache_dir=cache_dir)
    assert src == "cache" and r[2] == EXTRACTION[2]


def test_failed_extract_without_cache_is_miss(cache_dir, kfx):
    dead = _counting_extractor((None, None, None, None))
    r, src = tc.get_or_extract(kfx, dead, asin="ASIN1", cache_dir=cache_dir)
    assert src == "miss" and r == (None, None, None, None)


def test_no_path_and_no_cache_is_miss(cache_dir):
    ex = _counting_extractor()
    r, src = tc.get_or_extract(None, ex, asin="ASIN1", cache_dir=cache_dir)
    assert src == "miss" and r[2] is None
    assert not ex.calls


def test_no_path_but_cached_serves_cache(cache_dir, kfx):
    """WiFi 로 아직 못 받은 책이라도 캐시가 있으면 파일 없이 동작한다."""
    ex = _counting_extractor()
    tc.get_or_extract(kfx, ex, asin="ASIN1", cache_dir=cache_dir)
    r, src = tc.get_or_extract(None, ex, asin="ASIN1", cache_dir=cache_dir)
    assert src == "cache" and r[2] == EXTRACTION[2]


def test_has(cache_dir, kfx):
    assert tc.has("K", cache_dir) is False
    tc.store("K", EXTRACTION, kfx_path=kfx, cache_dir=cache_dir)
    assert tc.has("K", cache_dir) is True
