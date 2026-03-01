"""Export clippings to JSON, CSV, Markdown, and plain text formats."""

import csv
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import List

from kindle.models import Clipping, APNXInfo


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
