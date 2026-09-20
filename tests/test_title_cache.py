import json
from pathlib import Path

import pytest

from kindle.title_cache import (
    load_cache, save_cache, get_cached, put_cached, get_or_extract, get_stale,
)


def _make_kfx(tmp_path: Path, name: str = "book.kfx", content: bytes = b"x" * 100) -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


def test_load_missing_returns_empty(tmp_path):
    cache = load_cache(tmp_path / "missing.json")
    assert cache == {"version": 1, "books": {}}


def test_load_corrupt_returns_empty(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json {{{")
    assert load_cache(p) == {"version": 1, "books": {}}


def test_put_and_get(tmp_path):
    kfx = _make_kfx(tmp_path)
    cache = {"version": 1, "books": {}}
    put_cached(cache, kfx, "소년이 온다", "한강")
    hit = get_cached(cache, kfx)
    assert hit == {"title": "소년이 온다", "author": "한강", "asin": ""}


def test_get_miss_when_size_changes(tmp_path):
    kfx = _make_kfx(tmp_path)
    cache = {"version": 1, "books": {}}
    put_cached(cache, kfx, "T", "A")
    # 파일 내용 변경 → size 달라짐
    kfx.write_bytes(b"y" * 200)
    assert get_cached(cache, kfx) is None


def test_get_miss_when_file_deleted(tmp_path):
    kfx = _make_kfx(tmp_path)
    cache = {"version": 1, "books": {}}
    put_cached(cache, kfx, "T", "A")
    kfx.unlink()
    assert get_cached(cache, kfx) is None


def test_save_and_reload_roundtrip(tmp_path):
    kfx = _make_kfx(tmp_path)
    cache = {"version": 1, "books": {}}
    put_cached(cache, kfx, "소년이 온다", "한강")
    out = tmp_path / "cache.json"
    save_cache(out, cache)

    raw = json.loads(out.read_text(encoding="utf-8"))
    assert "books" in raw

    reloaded = load_cache(out)
    assert get_cached(reloaded, kfx) == {"title": "소년이 온다", "author": "한강", "asin": ""}


def test_get_or_extract_caches_first_call(tmp_path):
    kfx = _make_kfx(tmp_path)
    cache = {"version": 1, "books": {}}
    calls = []

    def extractor(p):
        calls.append(p)
        return {"title": "T", "author": "A"}

    r1 = get_or_extract(cache, kfx, extractor)
    r2 = get_or_extract(cache, kfx, extractor)
    assert r1 == r2 == {"title": "T", "author": "A", "asin": ""}
    assert len(calls) == 1   # 두 번째는 캐시 hit


def test_get_or_extract_refresh_bypasses_cache(tmp_path):
    kfx = _make_kfx(tmp_path)
    cache = {"version": 1, "books": {}}
    counter = [0]

    def extractor(p):
        counter[0] += 1
        return {"title": f"T{counter[0]}", "author": "A"}

    get_or_extract(cache, kfx, extractor)
    r = get_or_extract(cache, kfx, extractor, refresh=True)
    assert counter[0] == 2
    assert r["title"] == "T2"


def test_asin_roundtrip(tmp_path):
    """asin 은 KSDK 클리핑을 책에 매칭하는 열쇠다 — 캐시에 살아남아야 한다."""
    kfx = _make_kfx(tmp_path)
    cache = {"version": 1, "books": {}}
    put_cached(cache, kfx, "제목", "저자", "W48DPCW9ADHS48KRFPLEY7215EJYUXYA")
    assert get_cached(cache, kfx)["asin"] == "W48DPCW9ADHS48KRFPLEY7215EJYUXYA"
    out = tmp_path / "c.json"
    save_cache(out, cache)
    assert get_cached(load_cache(out), kfx)["asin"] == "W48DPCW9ADHS48KRFPLEY7215EJYUXYA"


def test_old_cache_entry_without_asin(tmp_path):
    """asin 필드가 없던 시절의 캐시 항목도 깨지지 않고 읽혀야 한다."""
    kfx = _make_kfx(tmp_path)
    st = kfx.stat()
    cache = {"version": 1, "books": {str(kfx.resolve()): {
        "title": "옛항목", "author": "저자", "mtime": st.st_mtime, "size": st.st_size}}}
    assert get_cached(cache, kfx) == {"title": "옛항목", "author": "저자", "asin": ""}


def test_get_or_extract_stores_asin(tmp_path):
    kfx = _make_kfx(tmp_path)
    cache = {"version": 1, "books": {}}
    r = get_or_extract(cache, kfx, lambda p: {"title": "T", "author": "A", "asin": "X1"})
    assert r["asin"] == "X1"
    assert get_cached(cache, kfx)["asin"] == "X1"


# --------------------------------------------------------------------------
# 추출 실패 대비 — KFX 를 못 읽을 때 ASIN 을 잃으면 책이 통째로 사라진다
# --------------------------------------------------------------------------

def test_get_stale_ignores_mtime_and_size(tmp_path):
    kfx = _make_kfx(tmp_path)
    cache = {"version": 1, "books": {}}
    put_cached(cache, kfx, "제목", "저자", "ASIN1")
    kfx.write_bytes(b"y" * 999)                 # mtime·size 둘 다 달라진다
    assert get_cached(cache, kfx) is None       # 정상 조회는 미스
    assert get_stale(cache, kfx)["asin"] == "ASIN1"


def test_get_stale_missing_entry(tmp_path):
    assert get_stale({"version": 1, "books": {}}, _make_kfx(tmp_path)) is None


def test_failed_extract_does_not_overwrite_good_entry(tmp_path):
    """추출 실패는 제목=파일명·저자·ASIN 공백으로 나타난다. 캐시를 덮으면 안 된다."""
    kfx = _make_kfx(tmp_path)
    cache = {"version": 1, "books": {}}
    put_cached(cache, kfx, "진짜 제목", "진짜 저자", "ASIN1")
    kfx.write_bytes(b"broken")                  # 재추출을 유발
    r = get_or_extract(cache, kfx, lambda p: {"title": "", "author": "", "asin": ""})
    assert r == {"title": "진짜 제목", "author": "진짜 저자", "asin": "ASIN1"}
    assert get_stale(cache, kfx)["asin"] == "ASIN1"


def test_failed_extract_without_previous_entry_is_stored(tmp_path):
    """기존 항목이 없으면 평소대로 저장한다 (제목은 파일명 폴백)."""
    kfx = _make_kfx(tmp_path)
    cache = {"version": 1, "books": {}}
    r = get_or_extract(cache, kfx, lambda p: {"title": "", "author": "", "asin": ""})
    assert r["title"] == kfx.stem
