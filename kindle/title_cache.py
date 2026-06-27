"""KFX 메타데이터(제목·저자) 디스크 캐시.

kfxlib 호출은 권당 수백 ms 들기 때문에, 한 번 추출한 결과를 절대 경로 기준으로
JSON 파일에 저장하고 다음 실행 때 mtime+size 가 동일하면 재사용한다.
파일이 갱신되거나 다른 책으로 교체되면 mtime 또는 size 가 달라져 자동으로
재추출된다.

캐시 파일 구조:
    {
      "version": 1,
      "books": {
        "/abs/path/book.kfx": {
          "title":  "...",
          "author": "...",
          "mtime":  1234567890.0,
          "size":   1234567
        },
        ...
      }
    }
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Optional


DEFAULT_PATH = Path.home() / ".kindle_kfx_titles.json"
_VERSION = 1

logger = logging.getLogger(__name__)


def load_cache(path: Path = DEFAULT_PATH) -> dict:
    """캐시 로드. 손상되거나 없으면 빈 캐시 반환."""
    if not path.exists():
        return {"version": _VERSION, "books": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "books" not in data:
            return {"version": _VERSION, "books": {}}
        return data
    except Exception as e:
        logger.warning("title 캐시 로드 실패 (%s): %s", path, e)
        return {"version": _VERSION, "books": {}}


def save_cache(path: Path, cache: dict) -> None:
    try:
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except Exception as e:
        logger.error("title 캐시 저장 실패 (%s): %s", path, e)


def get_cached(cache: dict, kfx_path: Path) -> Optional[dict]:
    """캐시 hit 면 {title, author} 반환, 아니면 None.

    mtime/size 둘 다 일치할 때만 hit. 파일이 사라졌으면 미스 처리.
    """
    key = str(kfx_path.resolve())
    entry = cache.get("books", {}).get(key)
    if not entry:
        return None
    try:
        st = kfx_path.stat()
    except OSError:
        return None
    if entry.get("mtime") != st.st_mtime or entry.get("size") != st.st_size:
        return None
    return {"title": entry["title"], "author": entry.get("author", "")}


def put_cached(cache: dict, kfx_path: Path, title: str, author: str) -> None:
    """캐시에 (title, author) 저장. 파일의 현재 mtime/size 를 같이 기록."""
    try:
        st = kfx_path.stat()
    except OSError:
        return
    cache.setdefault("books", {})[str(kfx_path.resolve())] = {
        "title":  title,
        "author": author,
        "mtime":  st.st_mtime,
        "size":   st.st_size,
    }


def get_or_extract(
    cache: dict,
    kfx_path: Path,
    extractor: Callable[[Path], dict],
    refresh: bool = False,
) -> dict:
    """캐시 hit 면 캐시값, 아니면 extractor(kfx_path) 호출 후 캐시에 저장.

    extractor 는 {"title": str, "author": str} 를 반환해야 한다.
    refresh=True 면 캐시를 무시하고 강제 재추출.
    """
    if not refresh:
        hit = get_cached(cache, kfx_path)
        if hit is not None:
            return hit
    meta = extractor(kfx_path)
    title  = meta.get("title", "") or kfx_path.stem
    author = meta.get("author", "") or ""
    put_cached(cache, kfx_path, title, author)
    return {"title": title, "author": author}
