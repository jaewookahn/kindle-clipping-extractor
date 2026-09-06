"""책 표지 조회·캐싱 — tui.py(Textual)와 kindle_gui(PyQt6) 공유.

우선순위: ① KFX 임베디드 표지(오프라인·정확·고해상도) → ② 외부 검색
(알라딘 → Yes24 → Google Books, kindle.notion_export._get_cover_url).
디스크 캐시(~/.cache/kindle_covers/)에 받아두고 경로만 돌려준다 — 실제
이미지를 어떻게 그릴지(터미널 프로토콜 vs QPixmap)는 호출부 책임이다.
"""

import hashlib
from pathlib import Path
from typing import Optional, Tuple

from kindle.ebook import extract_kfx_cover
from kindle.notion_export import _get_cover_url

COVER_DIR = Path.home() / ".cache" / "kindle_covers"


def hi_res(url: str) -> str:
    """알라딘 cover200(200px) → cover500(500px) 으로 승격해 선명도 향상."""
    return url.replace("/cover200/", "/cover500/") if "/cover200/" in url else url


def cached_download(url: str) -> Optional[Path]:
    """url 을 디스크 캐시에 받아 경로 반환. 캐시 hit 시 네트워크 생략."""
    import requests
    COVER_DIR.mkdir(parents=True, exist_ok=True)
    ext = (Path(url).suffix or ".jpg").split("?")[0]
    dest = COVER_DIR / (hashlib.sha1(url.encode()).hexdigest() + ext)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def embedded_cover_path(kfx: Path) -> Optional[Path]:
    """KFX 임베디드 표지를 디스크 캐시에 추출해 경로 반환 (mtime·size 키)."""
    COVER_DIR.mkdir(parents=True, exist_ok=True)
    try:
        st = kfx.stat()
    except OSError:
        return None
    key = hashlib.sha1(
        f"{kfx.resolve()}|{st.st_mtime_ns}|{st.st_size}".encode()
    ).hexdigest()
    for ext in ("jpg", "jpeg", "png", "webp", "gif"):
        p = COVER_DIR / f"kfx_{key}.{ext}"
        if p.exists() and p.stat().st_size > 0:
            return p
    res = extract_kfx_cover(kfx)
    if not res:
        return None
    ext, raw = res
    ext = {"jpeg": "jpg"}.get(ext, ext) or "jpg"
    p = COVER_DIR / f"kfx_{key}.{ext}"
    p.write_bytes(raw)
    return p


def load_book_cover(book: dict) -> Tuple[Optional[Path], str]:
    """책 하나의 표지를 구해 (경로, 캡션) 을 반환.

    실패해도 예외를 던지지 않는다 — 경로가 None 이면 두 번째 값은 사용자에게
    보여줄 안내/에러 메시지다. "실패"가 포함되면 에러, 아니면 정보성 메시지로
    간주해 호출부가 스타일(빨강/흐림)을 정하면 된다.

    book: "kfx"(Path), "title", "author" 키를 쓴다.
    """
    kfx = book.get("kfx")
    if kfx and Path(kfx).exists():
        try:
            p = embedded_cover_path(Path(kfx))
            if p:
                return p, "KFX 내장 표지"
        except Exception:
            pass

    try:
        url = _get_cover_url(book["title"], book["author"])
    except Exception as e:
        return None, f"검색 실패: {e}"
    if not url:
        return None, "(표지를 찾지 못함)"

    # 고해상도(cover500) 우선, 실패 시 원본(cover200)로 폴백
    path = None
    for candidate in (hi_res(url), url):
        try:
            path = cached_download(candidate)
            if path:
                url = candidate
                break
        except Exception:
            continue
    if path is None:
        return None, "표지 다운로드 실패"
    return path, url
