"""Notion export for Kindle clippings.

State file (~/.kindle_notion_sync.json) tracks synced fingerprints per book,
enabling precise incremental sync from both KFX+YJR and My Clippings sources
without double-counting.
"""

import hashlib
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests

from kindle.models import Clipping

DEFAULT_STATE = Path.home() / ".kindle_notion_sync.json"
NO_COVER_IMG = "https://via.placeholder.com/150x200?text=No+Cover"


# ---------------------------------------------------------------------------
# Fingerprint (shared with sync_kfx / sync_clippings_to_notion)
# ---------------------------------------------------------------------------

def fingerprint(c: Clipping) -> str:
    """Stable ID based on position — source-agnostic so KFX and My Clippings
    entries for the same highlight collapse to the same key."""
    key = f"{c.book_title}|{c.clip_type}|{c.location_start}|{c.location_end}"
    return hashlib.sha1(key.encode()).hexdigest()


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------

def load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"books": {}}


def save_state(path: Path, state: dict) -> None:
    # default=str — UUID 등 비-JSON 타입 자동 문자열화 (재진입 시에도 정상)
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Text formatting
# ---------------------------------------------------------------------------

def _format_clipping(c: Clipping) -> str:
    """Format one clipping as a text block (Paragraph-compatible)."""
    content = c.content or "(내용 없음)"

    # Strip color tag from content — show it in meta line instead
    color = ""
    m = re.match(r"^\[(\w+)\]\s*", content)
    if m:
        color = m.group(1)
        content = content[m.end():]

    prefix = "> NOTE:\n" if c.clip_type == "note" else ""

    meta_parts: List[str] = []
    if c.chapter:
        meta_parts.append(c.chapter)
    if c.location_start:
        loc = str(c.location_start)
        if c.location_end:
            loc += f"–{c.location_end}"
        meta_parts.append(f"Location: {loc}")
    if c.page:
        meta_parts.append(f"Page: {c.page}")
    if c.added_date:
        meta_parts.append(c.added_date)
    if color:
        meta_parts.append(color)

    meta = ("* " + ", ".join(meta_parts)) if meta_parts else ""
    return f"{prefix}{content}\n{meta}\n\n".strip() + "\n\n"


def format_chapter_range(c: "Chapter") -> str:
    """한 챕터의 범위를 'p.12–62 · Loc 110–760' 형태로."""
    parts: List[str] = []
    if c.page_start is not None:
        rng = str(c.page_start)
        if c.page_end is not None and c.page_end != c.page_start:
            rng += f"–{c.page_end}"
        parts.append(f"p.{rng}")
    if c.location_start is not None:
        rng = str(c.location_start)
        if c.location_end is not None and c.location_end != c.location_start:
            rng += f"–{c.location_end}"
        parts.append(f"Loc {rng}")
    return " · ".join(parts)


def format_chapter_outline(chapters: List["Chapter"]) -> str:
    """챕터 목차를 클리핑 본문 앞에 넣을 텍스트 블록으로.

    중첩은 들여쓰기로 표현하고 제목은 breadcrumb 의 마지막 조각만 쓴다
    (전체 경로를 매 줄에 반복하면 길어지기만 한다). 이 블록만 읽어도
    어떤 하이라이트가 어느 챕터에 속하는지 페이지·Location 으로 되짚을 수 있다.
    """
    if not chapters:
        return ""
    lines = ["【목차 — 챕터별 범위】"]
    for c in chapters:
        indent = "  " * c.level
        rng = format_chapter_range(c)
        lines.append(f"{indent}{c.leaf}" + (f"  ·  {rng}" if rng else ""))
    return "\n".join(lines) + "\n\n"


def _split_chunks(text: str, max_len: int = 2000) -> List[str]:
    chunks: List[str] = []
    while len(text) > max_len:
        cut = text.rfind("\n", 0, max_len)
        if cut <= 0:          # rfind 실패(-1) 또는 첫 문자가 \n(0) → 강제 분할
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:]
    chunks.append(text)
    return chunks


# ---------------------------------------------------------------------------
# Book cover
# ---------------------------------------------------------------------------
#
# 한국 책 위주라 Google Books 만으로는 적중률이 낮다.
# 알라딘 → Yes24 → Google Books 순서로 시도하고 첫 hit 사용.
# 모두 공개 검색 페이지 HTML 을 가볍게 파싱한다 (API key 불필요).

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"}

# 표지 URL 디스크 캐시 — 세션 간 유지. 매번 알라딘 검색하면 느리므로
# (title|author) → url 을 저장해 두 번째부터는 네트워크 없이 즉시 반환.
_COVER_CACHE_PATH = Path.home() / ".kindle_cover_cache.json"


def _load_cover_cache() -> dict:
    try:
        return json.loads(_COVER_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cover_cache() -> None:
    try:
        _COVER_CACHE_PATH.write_text(
            json.dumps(_COVER_CACHE, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


_COVER_CACHE: dict[str, Optional[str]] = _load_cover_cache()


def _try_aladin(title: str, author: str) -> Optional[str]:
    """알라딘 검색 결과 첫 책의 정면 커버 URL.

    검색 페이지에는 작은 `SpineShelf` (책등) 이미지가 먼저 나오므로,
    같은 product 경로의 `/cover/` 버전으로 정면 표지를 잡는다.
    """
    import urllib.parse, re, requests
    q = title + (" " + author if author else "")
    url = ("https://www.aladin.co.kr/search/wsearchresult.aspx?"
           f"SearchTarget=Book&KeyWord={urllib.parse.quote(q)}")
    try:
        html = requests.get(url, timeout=6, headers=_UA).text
    except Exception:
        return None
    # 알라딘 검색결과 HTML 에는 cover200 (200px 정면 표지) 경로가 정확히 들어있음
    m = re.search(
        r'(/product/\d+/\d+/cover200/[^"\'\s]+?\.(?:jpg|jpeg|png|webp))',
        html, re.IGNORECASE,
    )
    if m:
        return "https://image.aladin.co.kr" + m.group(1)
    return None


def _try_yes24(title: str, author: str) -> Optional[str]:
    """Yes24 검색 결과 첫 책의 커버 URL 추출."""
    import urllib.parse, re, requests
    q = title + (" " + author if author else "")
    url = ("https://www.yes24.com/Product/Search?domain=BOOK&"
           f"query={urllib.parse.quote(q)}")
    try:
        html = requests.get(url, timeout=6, headers=_UA).text
    except Exception:
        return None
    # Yes24 커버 호스트 — image.yes24.com 또는 i.yes24.com
    m = re.search(
        r'(https?://(?:image|i)\.yes24\.com/goods/\d+/[^"\'\s]+?\.(?:jpg|jpeg|png|webp))',
        html, re.IGNORECASE,
    )
    if m:
        return m.group(1).replace("http://", "https://")
    return None


def _try_google_books(title: str, author: str) -> Optional[str]:
    """Google Books API 표지 (영문 책 위주)."""
    import requests
    try:
        uri = f"https://www.googleapis.com/books/v1/volumes?q=intitle:{title}"
        if author:
            uri += f"+inauthor:{author}"
        items = requests.get(uri, timeout=6, headers=_UA).json().get("items", [])
        for item in items:
            thumb = (item.get("volumeInfo", {})
                         .get("imageLinks", {})
                         .get("thumbnail"))
            if thumb:
                return thumb.replace("http://", "https://")
    except Exception:
        pass
    return None


def _get_cover_url(title: str, author: str) -> Optional[str]:
    """알라딘 → Yes24 → Google Books 순. 결과는 메모리+디스크 캐싱.

    성공한 URL 만 디스크에 저장(미스는 메모리만 → 다음 세션에 재시도).
    """
    key = f"{title}|{author}"
    if key in _COVER_CACHE:
        return _COVER_CACHE[key]
    for fn in (_try_aladin, _try_yes24, _try_google_books):
        try:
            url = fn(title, author)
        except Exception:
            url = None
        if url:
            _COVER_CACHE[key] = url
            _save_cover_cache()      # hit 만 디스크 영속화
            return url
    _COVER_CACHE[key] = None         # 미스는 메모리만 (다음 세션 재시도)
    return None


# ---------------------------------------------------------------------------
# Notion REST client
# ---------------------------------------------------------------------------
#
# notional 은 pydantic v1 (`from pydantic.main import ModelMetaclass`) 에 묶여
# 있어 pydantic 2 가 깔린 환경에서는 import 조차 실패한다. 이 프로젝트가 쓰는
# Notion 호출은 아래 6종뿐이라 requests 로 직접 부르고 의존성을 걷어냈다.
#
# Notion 은 integration 당 평균 3 req/s 로 제한하며 초과 시 429 + Retry-After
# 를 준다. 책 한 권을 rewrite 하면 블록 삭제만 수백 건이라 재시도가 없으면
# 중간에 그대로 실패한다 → _NotionAPI 가 429/5xx 를 백오프 재시도한다.

_NOTION_API_VERSION = "2022-06-28"
_NOTION_BASE = "https://api.notion.com/v1"


class _NotionAPI:
    """토큰 하나로 묶인 Notion REST 세션 (429/5xx 자동 재시도)."""

    def __init__(self, token: str, max_retries: int = 5, timeout: int = 30) -> None:
        if not token:
            raise RuntimeError("Notion 토큰 누락 — NOTION_TOKEN 또는 --notion-token 확인")
        self._timeout = timeout
        self._max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization":  f"Bearer {token}",
            "Notion-Version": _NOTION_API_VERSION,
            "Content-Type":   "application/json",
        })

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        """path 는 '/pages/xxx' 처럼 _NOTION_BASE 이후 부분."""
        url = f"{_NOTION_BASE}{path}"
        delay = 1.0
        for attempt in range(self._max_retries + 1):
            r = self._session.request(method, url, timeout=self._timeout, **kwargs)
            if r.status_code != 429 and r.status_code < 500:
                return r
            if attempt == self._max_retries:
                return r
            # 429 는 Retry-After(초) 를 신뢰하고, 5xx 는 지수 백오프 + 지터
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", delay))
            else:
                wait = delay + random.uniform(0, 0.3)
            time.sleep(min(wait, 30.0))
            delay = min(delay * 2, 30.0)
        return r   # 도달 불가 — 루프에서 반환됨

    def get(self, path, **kw):    return self.request("GET", path, **kw)
    def post(self, path, **kw):   return self.request("POST", path, **kw)
    def patch(self, path, **kw):  return self.request("PATCH", path, **kw)
    def delete(self, path, **kw): return self.request("DELETE", path, **kw)


def _clean_id(raw: str) -> str:
    return str(raw).replace("-", "")


def _fail(r: requests.Response, what: str) -> None:
    raise RuntimeError(f"{what} 실패 ({r.status_code}): {r.text[:300]}")


# ---------------------------------------------------------------------------
# Notion operations
# ---------------------------------------------------------------------------

def _page_exists(api: _NotionAPI, page_id: str) -> bool:
    """저장된 page_id 가 아직 살아 있는지 확인 (삭제된 페이지 감지)."""
    try:
        r = api.get(f"/pages/{_clean_id(page_id)}")
    except Exception:
        return False
    if r.status_code >= 400:
        return False
    # 휴지통에 들어간 페이지는 200 이지만 archived/in_trash 가 True
    try:
        data = r.json()
    except ValueError:
        return False
    return not (data.get("archived") or data.get("in_trash"))


def _find_existing_page(api: _NotionAPI, database_id: str, title: str) -> Optional[str]:
    """Return page_id if a page with this exact title exists, else None."""
    try:
        r = api.post(
            f"/databases/{_clean_id(database_id)}/query",
            json={
                # Title 은 title 타입 속성 — rich_text 필터로는 매칭되지 않는다
                "filter": {"property": "Title", "title": {"equals": title}},
                "page_size": 1,
            },
        )
        if r.status_code >= 400:
            print(f"  [경고] 페이지 검색 실패 ({title}): "
                  f"{r.status_code} {r.text[:120]}", file=sys.stderr)
            return None
        results = r.json().get("results", [])
        return results[0]["id"] if results else None
    except Exception as e:
        print(f"  [경고] 페이지 검색 실패 ({title}): {e}", file=sys.stderr)
        return None


def _build_properties(title: Optional[str] = None, author: Optional[str] = None,
                      highlight_count: Optional[int] = None,
                      last_date: Optional[str] = None,
                      touch_synced: bool = True) -> dict:
    """None 이 아닌 값만 담은 Notion properties payload."""
    props: dict = {}
    if title is not None:
        props["Title"] = {"title": [{"type": "text", "text": {"content": title}}]}
    if author is not None:
        props["Author"] = {"rich_text": [{"type": "text", "text": {"content": author}}]}
    if highlight_count is not None:
        props["Highlights"] = {"number": highlight_count}
    if touch_synced:
        props["Last Synced"] = {"date": {"start": datetime.now().isoformat()}}
    if last_date:
        try:
            dt = datetime.strptime(last_date, "%Y-%m-%d %H:%M:%S")
            props["Last Highlighted"] = {"date": {"start": dt.isoformat()}}
        except ValueError:
            # My Clippings.txt 는 로캘 문자열("Monday, January 1, 2024 …")이라
            # 이 포맷으로 파싱되지 않는다 — 속성만 건너뛴다.
            pass
    return props


def _create_page(api: _NotionAPI, database_id: str, title: str, author: str,
                 highlight_count: int, last_date: Optional[str],
                 enable_book_cover: bool) -> str:
    """Create a new Notion page and return its ID."""
    payload: dict = {
        "parent":     {"database_id": _clean_id(database_id)},
        "properties": _build_properties(
            title=title, author=author,
            highlight_count=highlight_count, last_date=last_date,
        ),
        "children":   [],
    }
    if enable_book_cover:
        url = _get_cover_url(title, author) or NO_COVER_IMG
        payload["cover"] = {"type": "external", "external": {"url": url}}
        if url == NO_COVER_IMG:
            print(f"  × 표지를 찾을 수 없음 — 플레이스홀더 사용", flush=True)
        else:
            print(f"  ✓ 표지 추가 ({url[:60]}…)", flush=True)

    r = api.post("/pages", json=payload)
    if r.status_code >= 400:
        _fail(r, "페이지 생성")
    return r.json()["id"]


def _append_clippings(api: _NotionAPI, page_id: str, formatted: List[str]) -> None:
    """Append formatted clipping text as Paragraph blocks to an existing page."""
    pid = _clean_id(page_id)
    full_text = "".join(formatted)
    for chunk in _split_chunks(full_text):
        payload = {
            "children": [{
                "object": "block",
                "type":   "paragraph",
                "paragraph": {
                    "rich_text": [{
                        "type": "text",
                        "text": {"content": chunk},
                    }],
                },
            }],
        }
        r = api.patch(f"/blocks/{pid}/children", json=payload)
        if r.status_code >= 400:
            _fail(r, "blocks.children.append")


def _rewrite_page_body(api: _NotionAPI, page_id: str, formatted: List[str]) -> None:
    """Delete all existing child blocks of a page, then re-append `formatted`.

    본문 전체를 새로 쓴다. 클리핑 텍스트 포맷이 바뀌었을 때(예: 챕터 정보
    추가) 기존 페이지를 갱신하는 용도. 표지·속성·page_id 는 보존된다.
    """
    pid = _clean_id(page_id)

    # 1. 기존 자식 블록 ID 수집 (페이지네이션)
    block_ids: List[str] = []
    cursor = None
    while True:
        path = f"/blocks/{pid}/children?page_size=100"
        if cursor:
            path += f"&start_cursor={cursor}"
        r = api.get(path)
        if r.status_code >= 400:
            _fail(r, "블록 조회")
        data = r.json()
        block_ids.extend(b["id"] for b in data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    # 2. 전부 삭제
    for bid in block_ids:
        dr = api.delete(f"/blocks/{_clean_id(bid)}")
        if dr.status_code >= 400:
            _fail(dr, "블록 삭제")

    # 3. 새 본문 추가
    _append_clippings(api, page_id, formatted)


def _update_page_properties(api: _NotionAPI, page_id: str,
                            highlight_count: Optional[int],
                            last_date: Optional[str]) -> None:
    """Notion 페이지 속성 업데이트.

    highlight_count=None 이면 Highlights 를 갱신하지 않는다.
    증분 sync 에서 새 클리핑만 세면 기존 카운트를 잘못 덮어쓰므로, 전체
    클리핑 수를 아는 경우(페이지 신규 생성 / rewrite)에만 None 이 아닌 값을 전달.
    """
    props = _build_properties(highlight_count=highlight_count, last_date=last_date)
    r = api.patch(f"/pages/{_clean_id(page_id)}", json={"properties": props})
    if r.status_code >= 400:
        print(f"  [경고] 페이지 속성 업데이트 실패 ({r.status_code}): {r.text[:120]}",
              file=sys.stderr)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def sync_to_notion(
    clippings: List[Clipping],
    notion_token: str,
    database_id: str,
    state_path: Path = DEFAULT_STATE,
    enable_book_cover: bool = True,
    rewrite: bool = False,
    clip_fps: Optional[List[str]] = None,
    chapters_by_book: Optional[Dict[str, list]] = None,
) -> dict:
    """Sync clippings to a Notion database.

    Only uploads clippings not yet in the state file.
    Both KFX+YJR and My Clippings sources share the same state file,
    so the same highlight is never uploaded twice regardless of source.

    rewrite=True 이면 fingerprint dedup 을 무시하고, 각 책의 기존 Notion
    페이지 본문을 통째로 지운 뒤 전달된 모든 클리핑으로 다시 채운다.
    클리핑 포맷이 바뀌었을 때(예: 챕터 정보 추가) 백필용. fingerprint
    상태는 위치 기반이라 그대로 유지된다.

    chapters_by_book: {책 제목: [Chapter, …]} — 있으면 본문 맨 앞에 챕터
    목차(페이지·Location 범위)를 쓴다. Notion append 는 끝에만 붙일 수 있어
    앞에 끼워 넣을 수 없으므로, 페이지를 새로 만들 때와 rewrite 때만 반영된다.
    기존 페이지에 목차를 넣으려면 --rewrite-bodies 로 다시 쓸 것.

    Returns:
        {"added": int, "skipped": int, "books_new": int, "books_updated": int}
    """
    api = _NotionAPI(notion_token)
    state = load_state(state_path)
    books_state: Dict[str, dict] = state.setdefault("books", {})

    # Group by book, preserve order
    by_book: Dict[str, List[Clipping]] = {}
    for c in clippings:
        by_book.setdefault(c.book_title, []).append(c)

    # clip_fps: sync_kfx.py가 PRE-KL 시점에 미리 계산한 fingerprint 목록.
    # 제공되면 synced_fingerprints도 PRE-KL 기준으로 기록돼 seen_keys와 동일 공간 사용.
    # None이면 (My Clippings 등) clipping 객체에서 직접 계산.
    #
    # clippings[i] ↔ clip_fps[i] 위치 대응이 전제다. 호출부에서 clippings 만
    # 정렬하면 짝이 어긋나 잘못된 fingerprint 가 저장되고 dedup 이 깨지므로,
    # 길이가 다르면 조용히 zip 으로 자르지 말고 즉시 알린다.
    if clip_fps is not None and len(clip_fps) != len(clippings):
        raise ValueError(
            f"clip_fps 길이 불일치: clippings={len(clippings)}, clip_fps={len(clip_fps)} "
            "— 두 리스트는 같은 순서로 유지돼야 한다 (함께 정렬할 것)"
        )
    _fp_map: Optional[Dict[int, str]] = (
        {id(c): fp for c, fp in zip(clippings, clip_fps)} if clip_fps else None
    )

    def _clip_fp(c: Clipping) -> str:
        return _fp_map[id(c)] if _fp_map else fingerprint(c)

    summary = {"added": 0, "skipped": 0, "books_new": 0, "books_updated": 0}

    for title, book_clips in by_book.items():
        book_state = books_state.setdefault(title, {
            "notion_page_id": None,
            "synced_fingerprints": [],
        })
        synced_fps = set(book_state["synced_fingerprints"])

        if rewrite:
            new_clips = list(book_clips)   # 전체 재작성 — dedup 무시
        else:
            new_clips = [c for c in book_clips if _clip_fp(c) not in synced_fps]
        summary["skipped"] += len(book_clips) - len(new_clips)

        if not new_clips:
            continue

        # Skip bookmark-only updates (no readable text to show)
        content_clips = [c for c in new_clips if c.clip_type != "bookmark"]
        if not content_clips:
            # Still record fingerprints so bookmarks don't re-appear
            for c in new_clips:
                synced_fps.add(_clip_fp(c))
            book_state["synced_fingerprints"] = sorted(synced_fps)
            continue

        author = next((c.author for c in book_clips if c.author), "")
        last_date = max(
            (c.added_date for c in book_clips if c.added_date),
            default=None,
        )
        # rewrite 시 book_clips = 전체 클리핑 → 총 하이라이트 수 정확.
        # 증분 sync 시 book_clips = 새 클리핑만 → 전체 수 알 수 없으므로 None 전달.
        highlight_count_for_props = (
            len([c for c in book_clips if c.clip_type == "highlight"])
            if rewrite else None
        )

        formatted = [_format_clipping(c) for c in content_clips]

        # 챕터 목차는 본문 맨 앞에. append 는 끝에만 붙으므로 신규 생성/rewrite 에서만.
        outline = ""
        if chapters_by_book:
            outline = format_chapter_outline(chapters_by_book.get(title) or [])

        title_author = f"{title} ({author})" if author else title
        print(title_author, flush=True)
        print("-" * len(title_author), flush=True)

        page_id = book_state.get("notion_page_id")

        # Verify the stored page_id still exists
        if page_id and not _page_exists(api, page_id):
            page_id = None
            book_state["notion_page_id"] = None

        if page_id is None:
            # Try to find an existing page by title before creating
            page_id = _find_existing_page(api, database_id, title)

        if page_id is None:
            page_id = _create_page(
                api, database_id, title, author,
                len([c for c in book_clips if c.clip_type == "highlight"]),
                last_date, enable_book_cover,
            )
            book_state["notion_page_id"] = page_id
            _append_clippings(api, page_id, ([outline] if outline else []) + formatted)
            summary["books_new"] += 1
            print(f"  ✓ 새 페이지 생성", flush=True)
        elif rewrite:
            _rewrite_page_body(api, page_id, ([outline] if outline else []) + formatted)
            _update_page_properties(api, page_id, highlight_count_for_props, last_date)
            book_state["notion_page_id"] = page_id
            summary["books_updated"] += 1
            print(f"  ↻ 본문 재작성", flush=True)
        else:
            _append_clippings(api, page_id, formatted)
            _update_page_properties(api, page_id, highlight_count_for_props, last_date)
            book_state["notion_page_id"] = page_id
            summary["books_updated"] += 1

        for c in new_clips:
            synced_fps.add(_clip_fp(c))
        book_state["synced_fingerprints"] = sorted(synced_fps)
        summary["added"] += len(content_clips)   # bookmark는 Notion에 추가되지 않으므로 제외
        verb = "재작성" if rewrite else "추가"
        print(f"  → {len(content_clips)}개 {verb} (skip {len(book_clips) - len(new_clips)})\n", flush=True)
        save_state(state_path, state)   # 책마다 중간 저장 → 중단돼도 진전분 보존

    save_state(state_path, state)
    return summary
