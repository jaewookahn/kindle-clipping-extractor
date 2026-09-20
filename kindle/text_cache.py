"""KFX 추출 결과(본문·페이지맵·KL맵·TOC) 디스크 캐시.

## 왜 필요한가

펌웨어 5.19.x 이후 하이라이트 본문은 **어디에도 저장되지 않는다.** KSDK DB 는
좌표(`shortPosition`)와 색만 담고, 본문은 KFX 에서 그 좌표를 잘라내는 수밖에
없다 (`kindle/ksdk.py` 참조 — DB 전수 조사에서 본문 필드는 노트뿐이었다).

즉 **클리핑 하나를 채울 때마다 책 파일이 필요하다.** 그런데 책 파일을 매번
얻는 경로는 전부 약하다:

- MacDroid 마운트 — `stat` 은 크기를 답하는데 실제 읽기는 타임아웃한다
  (실측). 그러면 `extract_kfx_info()` 가 조용히 빈 결과를 돌려주고, 위치만
  있는 껍데기가 그대로 업로드된다 (2026-09-19 사고, 157건)
- WiFi 수신 — 책당 수십 MB. 매번 받을 수는 없다
- USB(MTP) — 빠르지만 케이블이 필요하다

책은 안 변하고 어노테이션만 변한다. 그러니 **책당 한 번만** 추출하고 결과를
보관하면 이후에는 원본이 없어도 된다.

## 경로가 아니라 내용을 담는다

`title_cache` 는 경로→메타데이터 맵이라 원본이 사라지면 미스가 난다. 여기서는
**추출된 텍스트 자체**를 담는다. 칼리버 라이브러리에서 책을 지우든, 기기에서
빼든, 케이블이 없든 이미 받아둔 책은 계속 동작한다.

    ~/.kindle_kfx_text/<key>.json.gz
        {"version":1, "asin":…, "title":…, "source_name":…, "source_size":…,
         "text":…, "page_map":[[label, off], …], "kl_offsets":[…],
         "toc":[[off, breadcrumb], …], "saved_at":…}

실측 크기: KFX 29.9MB → 이 JSON gzip **248KB** (120배). 147권이어도 ~36MB.

## 무효화

`source_size` 가 현재 파일과 다르면 책이 교체된 것으로 보고 재추출한다.
sha1 이 아니라 크기를 쓰는 이유는 **검증 때문에 30MB 를 다시 읽으면 캐시의
의미가 없기 때문**이다. 그리고 판정이 애매할 때는 항상 캐시 쪽으로 기운다 —
잘못 재추출하면 위 사고처럼 빈 본문을 얻지만, 캐시를 쓰면 최악이라도 옛 본문이다.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

__all__ = [
    "DEFAULT_DIR",
    "cache_key",
    "entry_path",
    "load",
    "store",
    "has",
    "get_or_extract",
]

DEFAULT_DIR = Path.home() / ".kindle_kfx_text"
_VERSION = 1

logger = logging.getLogger(__name__)

# 추출 결과 4-tuple: (page_map, kl_offsets, book_text, toc)
Extraction = Tuple[Optional[list], Optional[List[int]], Optional[str], Optional[list]]

_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def cache_key(asin: str = "", kfx_path: Optional[Path] = None) -> str:
    """캐시 키. ASIN 이 있으면 그것, 없으면 경로 해시.

    ASIN 을 1순위로 두는 이유는 같은 책이 마운트·MTP 사본·임시 루트 등
    **여러 경로로 나타나기 때문**이다. 경로로 키를 잡으면 같은 책을 몇 번이고
    다시 추출하게 된다.
    """
    asin = (asin or "").strip()
    if asin:
        return _SAFE.sub("_", asin)[:64]
    if kfx_path is None:
        raise ValueError("asin 과 kfx_path 가 모두 비었습니다")
    return "p_" + hashlib.sha1(str(Path(kfx_path).resolve()).encode()).hexdigest()[:24]


def entry_path(key: str, cache_dir: Path = DEFAULT_DIR) -> Path:
    return Path(cache_dir) / f"{key}.json.gz"


def load(key: str, cache_dir: Path = DEFAULT_DIR) -> Optional[dict]:
    """캐시 항목 로드. 없거나 손상됐으면 None."""
    p = entry_path(key, cache_dir)
    if not p.exists():
        return None
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("본문 캐시 로드 실패 (%s): %s", p, e)
        return None
    if not isinstance(data, dict) or data.get("version") != _VERSION:
        return None
    if not data.get("text"):
        return None                     # 빈 본문은 캐시할 가치가 없다
    return data


def has(key: str, cache_dir: Path = DEFAULT_DIR) -> bool:
    return load(key, cache_dir) is not None


def store(key: str, extraction: Extraction, kfx_path: Optional[Path] = None,
          asin: str = "", title: str = "", cache_dir: Path = DEFAULT_DIR) -> bool:
    """추출 결과 저장. 본문이 비었으면 저장하지 않고 False 를 돌려준다.

    빈 본문을 저장하면 **실패한 추출이 캐시에 굳어** 이후 실행이 전부 그 빈
    결과를 재사용한다. 2026-09-19 사고를 영구화하는 셈이라 막는다.
    """
    page_map, kl_offsets, text, toc = extraction
    if not text:
        return False
    try:
        size = Path(kfx_path).stat().st_size if kfx_path else None
    except OSError:
        size = None
    payload = {
        "version":     _VERSION,
        "asin":        asin,
        "title":       title,
        "source_name": Path(kfx_path).name if kfx_path else "",
        "source_size": size,
        "text":        text,
        "page_map":    [list(x) for x in (page_map or [])],
        "kl_offsets":  list(kl_offsets or []),
        "toc":         [list(x) for x in (toc or [])],
        "saved_at":    datetime.now().isoformat(timespec="seconds"),
    }
    p = entry_path(key, cache_dir)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        tmp.replace(p)                  # 부분 기록된 캐시가 남지 않도록 원자적 교체
    except Exception as e:
        logger.error("본문 캐시 저장 실패 (%s): %s", p, e)
        return False
    return True


def _as_extraction(entry: dict) -> Extraction:
    """저장된 dict → `extract_kfx_info()` 와 같은 4-tuple.

    JSON 은 튜플을 리스트로 만든다. 호출부(`fill_clipping_pages`,
    `build_chapter_ranges`)가 원래 튜플을 받으므로 되돌려 준다.
    """
    page_map = [tuple(x) for x in entry.get("page_map") or []] or None
    kl = list(entry.get("kl_offsets") or []) or None
    toc = [tuple(x) for x in entry.get("toc") or []] or None
    return page_map, kl, entry.get("text") or None, toc


def get_or_extract(
    kfx_path: Optional[Path],
    extractor: Callable[[Path], Extraction],
    asin: str = "",
    title: str = "",
    refresh: bool = False,
    cache_dir: Path = DEFAULT_DIR,
) -> Tuple[Extraction, str]:
    """캐시 hit 면 캐시값, 아니면 `extractor(kfx_path)` 후 저장.

    반환: `((page_map, kl_offsets, text, toc), source)`.
    `source` 는 `"cache"` / `"extract"` / `"miss"` — 호출부가 로그에 쓴다.

    판정:
      - `refresh=True`            → 무조건 재추출
      - 캐시 있음 + 파일 크기 일치 → 캐시 (추출 안 함)
      - 캐시 있음 + 파일 없음/못 읽음 → 캐시 (**이게 이 캐시의 존재 이유**)
      - 캐시 있음 + 크기 다름      → 책이 교체된 것으로 보고 재추출
      - 캐시 없음                  → 추출
    """
    key = cache_key(asin, kfx_path)
    entry = None if refresh else load(key, cache_dir)

    if entry is not None:
        cached_size = entry.get("source_size")
        cur_size = None
        if kfx_path is not None:
            try:
                cur_size = Path(kfx_path).stat().st_size
            except OSError:
                cur_size = None
        # 크기를 알 수 없으면(파일 없음·마운트 죽음) 캐시를 믿는다.
        if cur_size is None or cached_size is None or cur_size == cached_size:
            return _as_extraction(entry), "cache"
        logger.info("본문 캐시 무효 — 크기 %s → %s (%s)", cached_size, cur_size, key)

    if kfx_path is None:
        return (None, None, None, None), "miss"

    extraction = extractor(Path(kfx_path))
    if extraction and extraction[2]:
        store(key, extraction, kfx_path=kfx_path, asin=asin, title=title,
              cache_dir=cache_dir)
        return extraction, "extract"

    # 추출 실패. 캐시가 있으면 그거라도 쓰는 게 빈 본문보다 낫다.
    if entry is not None:
        logger.warning("본문 추출 실패 — 캐시로 대체 (%s)", key)
        return _as_extraction(entry), "cache"
    return (None, None, None, None), "miss"
