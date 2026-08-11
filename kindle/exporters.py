"""Export clippings to JSON, CSV, Markdown, and plain text formats."""

import csv
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from kindle.models import Clipping, APNXInfo, Chapter


def _chapter_dicts(chapters: List[Chapter]) -> List[dict]:
    """Chapter → JSON 직렬화용 dict (None 필드는 제거)."""
    out: List[dict] = []
    for c in chapters:
        d = {
            "title":  c.title,      # breadcrumb — 계층이 그대로 들어 있음
            "leaf":   c.leaf,
            "level":  c.level,
            "page_start":     c.page_start,
            "page_end":       c.page_end,
            "location_start": c.location_start,
            "location_end":   c.location_end,
        }
        out.append({k: v for k, v in d.items() if v is not None})
    return out

# ---------------------------------------------------------------------------
# Sync-pipeline export helpers  (used by sync_kfx.py and sync_clippings.py)
# ---------------------------------------------------------------------------


def _sync_clip_dict(c: Clipping, *, strip_keys: tuple = ()) -> dict:
    d = asdict(c)
    for k in strip_keys:
        d.pop(k, None)
    return {k: v for k, v in d.items() if v is not None and v != "" and v is not False}


def _chapter_outline_md(chapters: Optional[List[Chapter]]) -> List[str]:
    """책 제목 아래에 넣을 목차 블록 (중첩 목록). 챕터가 없으면 빈 리스트."""
    if not chapters:
        return []
    out = ["<details>", "<summary>목차 — 챕터별 범위</summary>", ""]
    for c in chapters:
        rng: List[str] = []
        if c.page_start is not None:
            r = str(c.page_start)
            if c.page_end is not None and c.page_end != c.page_start:
                r += f"–{c.page_end}"
            rng.append(f"p.{r}")
        if c.location_start is not None:
            r = str(c.location_start)
            if c.location_end is not None and c.location_end != c.location_start:
                r += f"–{c.location_end}"
            rng.append(f"Loc {r}")
        suffix = f" — {' · '.join(rng)}" if rng else ""
        out.append(f"{'  ' * c.level}- {c.leaf}{suffix}")
    out += ["", "</details>", ""]
    return out


def sync_export_csv(clippings: List[Clipping], out: Path) -> None:
    fields = ["book_title", "author", "clip_type", "page",
              "location_start", "location_end", "added_date", "content"]
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for c in clippings:
            w.writerow({k: getattr(c, k, "") or "" for k in fields})


def sync_export_markdown(clippings: List[Clipping], out: Path,
                         heading: str = "킨들 클리핑",
                         chapters_by_book: Optional[Dict[str, List[Chapter]]] = None
                         ) -> None:
    lines = [f"# {heading}  ·  {datetime.now():%Y-%m-%d %H:%M}\n"]
    current_book = None
    for c in clippings:
        if c.book_title != current_book:
            current_book = c.book_title
            lines.append(f"\n## {c.book_title}")
            if c.author:
                lines.append(f"*{c.author}*\n")
            for line in _chapter_outline_md((chapters_by_book or {}).get(c.book_title)):
                lines.append(line)
        loc = f"Location {c.location_start}"
        if c.location_end and c.location_end != c.location_start:
            loc += f"–{c.location_end}"
        label = f"*{c.clip_type} · {loc}"
        if c.page:
            label += f" · p.{c.page}"
        if c.added_date:
            label += f" · {c.added_date}"
        lines.append(label + "*\n")
        lines.append(f"> {c.content or '(내용 없음)'}\n")
    out.write_text("\n".join(lines), encoding="utf-8")


def sync_export_text(clippings: List[Clipping], out: Path) -> None:
    lines: List[str] = []
    for c in clippings:
        loc = f"Location {c.location_start}"
        if c.location_end and c.location_end != c.location_start:
            loc += f"–{c.location_end}"
        header = f"[{c.book_title}]  {loc}"
        if c.page:
            header += f"  p.{c.page}"
        if c.added_date:
            header += f"  ({c.added_date})"
        lines.append(header)
        lines.append(c.content or "(내용 없음)")
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")


def sync_export_json_flat(clippings: List[Clipping], out: Path, meta: dict) -> None:
    """Flat clipping list — used by sync_clippings.py."""
    payload = {
        **meta,
        "clippings": [_sync_clip_dict(c) for c in clippings],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def sync_export_json_grouped(clippings: List[Clipping], out: Path, meta: dict,
                             chapters_by_book: Optional[Dict[str, List[Chapter]]] = None
                             ) -> None:
    """Book-grouped with per-type counts — used by sync_kfx.py.

    chapters_by_book 가 있으면 각 책에 "chapters" (챕터별 페이지·Location
    범위) 를 넣는다. 클리핑을 나중에 정리할 때 어느 장에 속하는지 되짚는 근거.
    """
    strip = ("book_title", "author", "source_file")
    books: dict[str, dict] = {}
    for c in clippings:
        if c.book_title not in books:
            books[c.book_title] = {
                "title":            c.book_title,
                "author":           c.author or "",
                "total_highlights": 0,
                "total_bookmarks":  0,
                "total_notes":      0,
                "clippings":        [],
            }
        b = books[c.book_title]
        if c.clip_type == "highlight":
            b["total_highlights"] += 1
        elif c.clip_type == "bookmark":
            b["total_bookmarks"]  += 1
        elif c.clip_type == "note":
            b["total_notes"]      += 1
        b["clippings"].append(_sync_clip_dict(c, strip_keys=strip))

    book_list = []
    for title, b in books.items():
        if not b["author"]:
            del b["author"]
        chapters = (chapters_by_book or {}).get(title)
        if chapters:
            # clippings 앞에 오도록 키 순서를 다시 잡는다
            clips = b.pop("clippings")
            b["chapters"]  = _chapter_dicts(chapters)
            b["clippings"] = clips
        book_list.append(b)

    payload = {**meta, "books": book_list}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _clipping_to_dict(c: Clipping) -> dict:
    d = asdict(c)
    # Remove None values for cleaner JSON/CSV
    return {k: v for k, v in d.items() if v is not None and v != ""}


def export_json(clippings: List[Clipping], apnx_infos: List[APNXInfo], out: Path):
    payload = {
        "clippings": [_clipping_to_dict(c) for c in clippings],
        "page_indexes": [asdict(a) for a in apnx_infos],
        "exported_at": datetime.now().isoformat(),
        "total_clippings": len(clippings),
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(clippings)} clippings → {out}")


def export_csv(clippings: List[Clipping], out: Path):
    if not clippings:
        print("No clippings to export.")
        return
    fieldnames = [
        "book_title", "author", "clip_type", "page",
        "location_start", "location_end", "added_date", "content", "source_file",
    ]
    with out.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for c in clippings:
            writer.writerow(asdict(c))
    print(f"Wrote {len(clippings)} clippings → {out}")


def export_markdown(clippings: List[Clipping], out: Path):
    lines = ["# Kindle Clippings\n"]
    # Group by book
    books: dict[str, List[Clipping]] = {}
    for c in clippings:
        books.setdefault(c.book_title, []).append(c)

    for title, clips in books.items():
        author = clips[0].author
        header = f"## {title}"
        if author:
            header += f"  \n*{author}*"
        lines.append(header)
        lines.append("")

        for c in clips:
            if c.clip_type == "last_position":
                continue
            meta_parts = []
            if c.page:
                meta_parts.append(f"p. {c.page}")
            if c.location_start:
                loc = str(c.location_start)
                if c.location_end:
                    loc += f"–{c.location_end}"
                meta_parts.append(f"loc. {loc}")
            if c.added_date:
                meta_parts.append(c.added_date)
            meta = " | ".join(meta_parts)

            if c.clip_type == "highlight":
                lines.append(f"> {c.content}")
                if meta:
                    lines.append(f"> *— {meta}*")
            elif c.clip_type == "note":
                lines.append(f"**Note** ({meta}): {c.content}")
            elif c.clip_type == "bookmark":
                label = f": {c.content}" if c.content else ""
                lines.append(f"*Bookmark* ({meta}){label}")
            lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {len(clippings)} clippings → {out}")


def export_text(clippings: List[Clipping], out: Path):
    lines = []
    current_book = None
    for c in clippings:
        if c.clip_type == "last_position":
            continue
        if c.book_title != current_book:
            current_book = c.book_title
            lines.append("=" * 60)
            lines.append(c.book_title)
            if c.author:
                lines.append(f"by {c.author}")
            lines.append("=" * 60)
            lines.append("")

        loc = ""
        if c.page:
            loc += f"Page {c.page}  "
        if c.location_start:
            loc += f"Loc {c.location_start}"
            if c.location_end:
                loc += f"-{c.location_end}"
        if c.added_date:
            loc += f"  ({c.added_date})"
        if loc:
            lines.append(f"[{c.clip_type.upper()}] {loc.strip()}")
        if c.content:
            lines.append(c.content)
        lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {len(clippings)} clippings → {out}")
