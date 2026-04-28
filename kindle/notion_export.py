"""Notion export for Kindle clippings.

State file (~/.kindle_notion_sync.json) tracks synced fingerprints per book,
enabling precise incremental sync from both KFX+YJR and My Clippings sources
without double-counting.
"""

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import notional
from notional.query import TextCondition
from notional.types import Title, RichText, Number, Date, ExternalFile
from notional.blocks import Paragraph

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
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


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


def _split_chunks(text: str, max_len: int = 2000) -> List[str]:
    chunks: List[str] = []
    while len(text) > max_len:
        cut = text.rfind("\n", 0, max_len)
        if cut == -1:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:]
    chunks.append(text)
    return chunks


# ---------------------------------------------------------------------------
# Book cover
# ---------------------------------------------------------------------------

def _get_cover_url(title: str, author: str) -> Optional[str]:
    try:
        from requests import get as http_get
        uri = f"https://www.googleapis.com/books/v1/volumes?q=intitle:{title}"
        if author:
            uri += f"+inauthor:{author}"
        items = http_get(uri, timeout=10).json().get("items", [])
        for item in items:
            thumb = (item.get("volumeInfo", {})
                         .get("imageLinks", {})
                         .get("thumbnail"))
            if thumb:
                return thumb.replace("http://", "https://")
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Notion operations
# ---------------------------------------------------------------------------

def _find_existing_page(notion, database_id: str, title: str) -> Optional[str]:
    """Return page_id if a page with this exact title exists, else None."""
    try:
        query = (
            notion.databases.query(database_id)
            .filter(property="Title", rich_text=TextCondition(equals=title))
            .limit(1)
        )
        data = query.first()
        return data.id if data else None
    except Exception as e:
        print(f"  [경고] 페이지 검색 실패 ({title}): {e}", file=sys.stderr)
        return None


def _create_page(notion, database_id: str, title: str, author: str,
                 highlight_count: int, last_date: Optional[str],
                 enable_book_cover: bool) -> str:
    """Create a new Notion page and return its ID."""
    props: dict = {
        "Title":          Title[title],
        "Author":         RichText[author],
        "Highlights":     Number[highlight_count],
        "Last Synced":    Date[datetime.now().isoformat()],
    }
    if last_date:
        try:
            dt = datetime.strptime(last_date, "%Y-%m-%d %H:%M:%S")
            props["Last Highlighted"] = Date[dt.isoformat()]
        except ValueError:
            pass

    page = notion.pages.create(
        parent=notion.databases.retrieve(database_id),
        properties=props,
        children=[],
    )

    if enable_book_cover and page.cover is None:
        url = _get_cover_url(title, author) or NO_COVER_IMG
        notion.pages.set(page, cover=ExternalFile[url])
        if url == NO_COVER_IMG:
            print(f"  × 표지를 찾을 수 없음 — 플레이스홀더 사용")
        else:
            print(f"  ✓ 표지 추가")

    return str(page.id)


def _append_clippings(notion, page_id: str, formatted: List[str]) -> None:
    """Append formatted clipping text as Paragraph blocks to an existing page."""
    page = notion.pages.retrieve(page_id)
    full_text = "".join(formatted)
    for chunk in _split_chunks(full_text):
        notion.blocks.children.append(page, Paragraph[chunk])


def _update_page_properties(notion, page_id: str, highlight_count: int,
                             last_date: Optional[str]) -> None:
    page = notion.pages.retrieve(page_id)
    page["Highlights"] = Number[highlight_count]
    page["Last Synced"] = Date[datetime.now().isoformat()]
    if last_date:
        try:
            dt = datetime.strptime(last_date, "%Y-%m-%d %H:%M:%S")
            page["Last Highlighted"] = Date[dt.isoformat()]
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def sync_to_notion(
    clippings: List[Clipping],
    notion_token: str,
    database_id: str,
    state_path: Path = DEFAULT_STATE,
    enable_book_cover: bool = True,
) -> dict:
    """Sync clippings to a Notion database.

    Only uploads clippings not yet in the state file.
    Both KFX+YJR and My Clippings sources share the same state file,
    so the same highlight is never uploaded twice regardless of source.

    Returns:
        {"added": int, "skipped": int, "books_new": int, "books_updated": int}
    """
    notion = notional.connect(auth=notion_token)
    state = load_state(state_path)
    books_state: Dict[str, dict] = state.setdefault("books", {})

    # Group by book, preserve order
    by_book: Dict[str, List[Clipping]] = {}
    for c in clippings:
        by_book.setdefault(c.book_title, []).append(c)

    summary = {"added": 0, "skipped": 0, "books_new": 0, "books_updated": 0}

    for title, book_clips in by_book.items():
        book_state = books_state.setdefault(title, {
            "notion_page_id": None,
            "synced_fingerprints": [],
        })
        synced_fps = set(book_state["synced_fingerprints"])

        new_clips = [c for c in book_clips if fingerprint(c) not in synced_fps]
        summary["skipped"] += len(book_clips) - len(new_clips)

        if not new_clips:
            continue

        # Skip bookmark-only updates (no readable text to show)
        content_clips = [c for c in new_clips if c.clip_type != "bookmark"]
        if not content_clips:
            # Still record fingerprints so bookmarks don't re-appear
            for c in new_clips:
                synced_fps.add(fingerprint(c))
            book_state["synced_fingerprints"] = sorted(synced_fps)
            continue

        author = next((c.author for c in book_clips if c.author), "")
        all_highlights = [c for c in book_clips if c.clip_type == "highlight"]
        last_date = max(
            (c.added_date for c in book_clips if c.added_date),
            default=None,
        )

        formatted = [_format_clipping(c) for c in content_clips]

        title_author = f"{title} ({author})" if author else title
        print(title_author)
        print("-" * len(title_author))

        page_id = book_state.get("notion_page_id")

        # Verify the stored page_id still exists
        if page_id:
            try:
                notion.pages.retrieve(page_id)
            except Exception:
                page_id = None
                book_state["notion_page_id"] = None

        if page_id is None:
            # Try to find an existing page by title before creating
            page_id = _find_existing_page(notion, database_id, title)

        if page_id is None:
            page_id = _create_page(
                notion, database_id, title, author,
                len(all_highlights), last_date, enable_book_cover,
            )
            book_state["notion_page_id"] = page_id
            _append_clippings(notion, page_id, formatted)
            summary["books_new"] += 1
            print(f"  ✓ 새 페이지 생성")
        else:
            _append_clippings(notion, page_id, formatted)
            _update_page_properties(notion, page_id, len(all_highlights), last_date)
            book_state["notion_page_id"] = page_id
            summary["books_updated"] += 1

        for c in new_clips:
            synced_fps.add(fingerprint(c))
        book_state["synced_fingerprints"] = sorted(synced_fps)
        summary["added"] += len(new_clips)
        print(f"  → {len(new_clips)}개 추가 (skip {len(book_clips) - len(new_clips)})\n")

    save_state(state_path, state)
    return summary
