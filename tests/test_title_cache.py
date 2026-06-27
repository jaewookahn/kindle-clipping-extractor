import json
from pathlib import Path

import pytest

from kindle.title_cache import (
    load_cache, save_cache, get_cached, put_cached, get_or_extract,
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
    assert hit == {"title": "소년이 온다", "author": "한강"}


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
    assert get_cached(reloaded, kfx) == {"title": "소년이 온다", "author": "한강"}


def test_get_or_extract_caches_first_call(tmp_path):
    kfx = _make_kfx(tmp_path)
    cache = {"version": 1, "books": {}}
    calls = []

    def extractor(p):
        calls.append(p)
        return {"title": "T", "author": "A"}

    r1 = get_or_extract(cache, kfx, extractor)
    r2 = get_or_extract(cache, kfx, extractor)
    assert r1 == r2 == {"title": "T", "author": "A"}
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
