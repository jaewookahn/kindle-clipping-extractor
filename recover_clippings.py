#!/usr/bin/env python3
"""
recover_clippings.py — My Clippings.txt 클리핑 한도 초과 텍스트 복구

킨들은 저작권 보호를 위해 책 당 일정 비율 이상은 My Clippings.txt에 본문을 저장하지 않는다.
한도를 초과한 항목은 content가 비거나 "<You have reached the clipping limit...>" 메시지만 남는다.

이 스크립트는 KFX 전자책에서 추출한 Kindle Location 맵을 이용해
Location 번호 → 문자 오프셋 → 원문 텍스트 순서로 복구한다.

사용법:
    python recover_clippings.py "My Clippings.txt" book.kfx -o recovered.json
    python recover_clippings.py "My Clippings.txt" book.kfx --book "책 제목" -o out.md
    python recover_clippings.py "My Clippings.txt" book.kfx --all -o out.csv

요구사항:
    - Calibre (ebook-convert)
    - Calibre KFX Input 플러그인 (KFX 형식 처리 시)

My Clippings.txt Location 숫자 vs KFX character offset
--------------------------------------------------------
YJR 주석 파일은 KFX 내부 character offset을 직접 저장하지만,
My Clippings.txt는 킨들 UI에 표시되는 Kindle Location 번호를 저장한다.

KFX에서 추출한 kindle_loc_offsets 배열:
    kindle_loc_offsets[i]  = KL(i+1)이 시작하는 character offset

따라서 KL N의 텍스트 범위:
    char_start = kindle_loc_offsets[N - 1]
    char_end   = kindle_loc_offsets[N]  (N < len 일 때)
"""

import argparse
import csv
import json
import re
import sys
import tempfile
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Clipping:
    book_title: str = ""
    author: str = ""
    clip_type: str = ""
    page: Optional[int] = None
    location_start: Optional[int] = None
    location_end: Optional[int] = None
    added_date: Optional[str] = None
    content: str = ""
    recovered: bool = False   # True if content was recovered from ebook


# ---------------------------------------------------------------------------
# My Clippings.txt parser
# ---------------------------------------------------------------------------

_SEP = "=========="

_META_RE = re.compile(
    r"-\s+Your\s+(?P<type>Highlight|Note|Bookmark)"
    r"(?:\s+on\s+(?:page\s+(?P<page>\d+)|[^|]+))?"
    r"(?:\s*\|\s*Location\s+(?P<loc_start>\d+)(?:-(?P<loc_end>\d+))?)?"
    r"(?:\s*\|\s*Added\s+on\s+(?P<date>.+))?",
    re.IGNORECASE,
)

_TITLE_AUTHOR_RE = re.compile(r"^(?P<title>.+?)\s+\((?P<author>[^)]+)\)\s*$")


def parse_my_clippings(path: Path) -> List[Clipping]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    clippings: List[Clipping] = []

    for raw in text.split(_SEP):
        lines = [l.rstrip() for l in raw.strip().splitlines()]
        if len(lines) < 2:
            continue

        title_line = lines[0].lstrip("\ufeff").strip()
        ta = _TITLE_AUTHOR_RE.match(title_line)
        title  = ta.group("title").strip()  if ta else title_line
        author = ta.group("author").strip() if ta else ""

        m = _META_RE.search(lines[1])
        if not m:
            continue

        clip_type = (m.group("type") or "highlight").lower()
        page      = int(m.group("page"))      if m.group("page")      else None
        loc_s     = int(m.group("loc_start")) if m.group("loc_start") else None
        loc_e     = int(m.group("loc_end"))   if m.group("loc_end")   else None
        date_str  = m.group("date").strip()   if m.group("date")      else None

        content_lines = [l for l in lines[2:] if l.strip()]
        content = " ".join(content_lines).strip()

        clippings.append(Clipping(
            book_title=title, author=author, clip_type=clip_type,
            page=page, location_start=loc_s, location_end=loc_e,
            added_date=date_str, content=content,
        ))

    return clippings


# ---------------------------------------------------------------------------
# "한도 초과" 감지
# ---------------------------------------------------------------------------

_LIMIT_PREFIXES = (
    "<you have reached the clipping limit",
    "<the clipping limit",
    "<이 항목의 클리핑 한도",
)


def _is_limit_exceeded(content: str) -> bool:
    if not content or not content.strip():
        return True
    return content.strip().lower().startswith(_LIMIT_PREFIXES)


# ---------------------------------------------------------------------------
# KFX 텍스트 + Kindle Location 맵 추출 (Calibre KFX Input 플러그인 사용)
# ---------------------------------------------------------------------------

_KFX_PLUGIN_PATHS = [
    "~/Library/Preferences/calibre/plugins/KFX Input.zip",
    "~/.config/calibre/plugins/KFX Input.zip",
]


def _find_kfx_plugin() -> Optional[str]:
    for p in _KFX_PLUGIN_PATHS:
        expanded = Path(p).expanduser()
        if expanded.exists():
            return str(expanded)
    return None


def extract_kfx_data(kfx_path: Path):
    """
    KFX 파일에서 (kindle_loc_offsets, book_text) 추출.

    kindle_loc_offsets[i] = Kindle Location (i+1)의 시작 character offset
    book_text = KFX 내부 character offset이 보존된 전체 텍스트

    Returns (None, None) on failure.
    """
    plugin_zip = _find_kfx_plugin()
    if not plugin_zip:
        print("  [오류] Calibre KFX Input 플러그인을 찾을 수 없습니다.", file=sys.stderr)
        print("  설치: Calibre → 환경설정 → 플러그인 → 'KFX Input' 검색 후 설치", file=sys.stderr)
        return None, None

    tmpdir: Optional[Path] = None
    try:
        tmpdir = Path(tempfile.mkdtemp())
        with zipfile.ZipFile(plugin_zip) as z:
            for name in z.namelist():
                if name.startswith("kfxlib/") and not name.endswith("/"):
                    dest = tmpdir / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(z.read(name))

        if str(tmpdir) not in sys.path:
            sys.path.insert(0, str(tmpdir))

        from kfxlib import yj_book  # type: ignore

        book = yj_book.YJ_Book(str(kfx_path))
        book.decode_book(set_metadata=None)
        pos_info = book.collect_position_map_info()

        # Kindle Location 맵
        loc_info = book.collect_location_map_info(pos_info)
        kindle_loc_offsets: Optional[List[int]] = (
            [entry.pid for entry in loc_info] if loc_info else None
        )

        # 책 전체 텍스트 (KFX character offset 보존)
        book_text: Optional[str] = None
        try:
            chunks = sorted(
                [c for c in book.collect_content_position_info() if c.text],
                key=lambda c: c.pid,
            )
            if chunks:
                parts: List[str] = []
                pos = 0
                for c in chunks:
                    if c.pid > pos:
                        parts.append(" " * (c.pid - pos))
                    parts.append(c.text)
                    pos = c.pid + c.length
                book_text = "".join(parts)
        except Exception as exc:
            print(f"  [경고] 텍스트 추출 실패: {exc}", file=sys.stderr)

        return kindle_loc_offsets, book_text

    except Exception as exc:
        print(f"  [오류] KFX 파싱 실패: {exc}", file=sys.stderr)
        return None, None
    finally:
        if tmpdir:
            import shutil
            shutil.rmtree(str(tmpdir), ignore_errors=True)


# ---------------------------------------------------------------------------
# KL → 텍스트 복구
# ---------------------------------------------------------------------------

def recover_text(
    clippings: List[Clipping],
    kindle_loc_offsets: List[int],
    book_text: str,
    force: bool = False,
) -> int:
    """
    Kindle Location 번호를 이용해 한도 초과 클리핑의 텍스트를 복구한다.

    kindle_loc_offsets[i] = KL(i+1) 시작 character offset

    Args:
        force: True면 content가 있어도 전자책 텍스트로 덮어쓴다.

    Returns:
        복구된 클리핑 수
    """
    recovered = 0
    n = len(kindle_loc_offsets)

    for c in clippings:
        if c.clip_type not in ("highlight", "bookmark"):
            continue
        if not force and not _is_limit_exceeded(c.content):
            continue
        if c.location_start is None:
            continue

        kl_s = c.location_start
        kl_e = c.location_end if c.location_end else kl_s

        if kl_s < 1 or kl_s > n:
            continue

        char_start = kindle_loc_offsets[kl_s - 1]
        char_end   = kindle_loc_offsets[kl_e] if kl_e < n else len(book_text)

        if char_start >= len(book_text):
            continue

        snippet = book_text[char_start : min(char_end, len(book_text))].strip()
        snippet = re.sub(r"\s+", " ", snippet)

        if snippet:
            c.content   = snippet
            c.recovered = True
            recovered  += 1

    return recovered


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------

def _to_dict(c: Clipping) -> dict:
    d = asdict(c)
    return {k: v for k, v in d.items() if v is not None and v != "" and v is not False}


def export_json(clippings: List[Clipping], out: Path) -> None:
    payload = {
        "clippings": [_to_dict(c) for c in clippings],
        "exported_at": datetime.now().isoformat(),
        "total": len(clippings),
        "recovered": sum(1 for c in clippings if c.recovered),
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {out}  ({len(clippings)}개 클리핑, {payload['recovered']}개 복구)")


def export_csv(clippings: List[Clipping], out: Path) -> None:
    fields = ["book_title", "author", "clip_type", "page",
              "location_start", "location_end", "added_date", "content", "recovered"]
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for c in clippings:
            w.writerow(asdict(c))
    print(f"저장: {out}  ({len(clippings)}개)")


def export_markdown(clippings: List[Clipping], out: Path) -> None:
    lines: List[str] = []
    current_book = None
    for c in clippings:
        if c.book_title != current_book:
            current_book = c.book_title
            lines.append(f"\n## {c.book_title}")
            if c.author:
                lines.append(f"*{c.author}*\n")
        loc = f"Location {c.location_start}"
        if c.location_end and c.location_end != c.location_start:
            loc += f"–{c.location_end}"
        meta = f"*{c.clip_type} · {loc}"
        if c.page:
            meta += f" · p.{c.page}"
        meta += "*"
        if c.recovered:
            meta += " *(복구됨)*"
        lines.append(meta)
        lines.append(f"\n> {c.content}\n")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"저장: {out}  ({len(clippings)}개)")


def export_text(clippings: List[Clipping], out: Path) -> None:
    lines: List[str] = []
    for c in clippings:
        lines.append(f"[{c.book_title}]  Location {c.location_start}"
                     + (f"–{c.location_end}" if c.location_end else "")
                     + ("  [복구됨]" if c.recovered else ""))
        lines.append(c.content)
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"저장: {out}  ({len(clippings)}개)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="My Clippings.txt 클리핑 한도 초과 텍스트 복구",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("clippings", help="My Clippings.txt 경로")
    parser.add_argument("ebook",     help="KFX 전자책 경로")
    parser.add_argument("-o", "--output", required=True, help="출력 파일 경로")
    parser.add_argument(
        "--book", default=None, metavar="TITLE",
        help="처리할 책 제목 (부분 일치, 생략 시 전체)",
    )
    parser.add_argument(
        "-f", "--format", choices=["json", "csv", "markdown", "text"], default=None,
        help="출력 형식 (기본값: 확장자에서 추론)",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="한도 초과 여부와 무관하게 모든 하이라이트를 전자책 텍스트로 재추출",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="복구 통계만 출력하고 종료 (파일 저장 안 함)",
    )
    args = parser.parse_args()

    clippings_path = Path(args.clippings)
    ebook_path     = Path(args.ebook)

    if not clippings_path.exists():
        print(f"오류: {clippings_path} 를 찾을 수 없습니다.", file=sys.stderr)
        sys.exit(1)
    if not ebook_path.exists():
        print(f"오류: {ebook_path} 를 찾을 수 없습니다.", file=sys.stderr)
        sys.exit(1)
    if ebook_path.suffix.lower() != ".kfx":
        print("오류: 현재 KFX 형식만 지원합니다.", file=sys.stderr)
        print("  (KFX가 아닌 경우 킨들 Location → 문자 오프셋 변환이 불가합니다.)", file=sys.stderr)
        sys.exit(1)

    # 1. 파싱
    print(f"파싱: {clippings_path}")
    all_clips = parse_my_clippings(clippings_path)
    print(f"  총 {len(all_clips)}개 클리핑")

    if args.book:
        q = args.book.lower()
        clips = [c for c in all_clips if q in c.book_title.lower()]
        print(f"  '{args.book}' 필터 후: {len(clips)}개")
    else:
        clips = all_clips

    limit_clips = [c for c in clips
                   if c.clip_type in ("highlight", "bookmark") and _is_limit_exceeded(c.content)]
    print(f"  한도 초과 항목: {len(limit_clips)}개")

    if args.stats:
        sys.exit(0)

    if not clips:
        print("처리할 클리핑이 없습니다.")
        sys.exit(0)

    # 2. KFX 분석
    print(f"\nKFX 분석: {ebook_path.name} …")
    kl_offsets, book_text = extract_kfx_data(ebook_path)

    if not kl_offsets or not book_text:
        print("텍스트 또는 Location 맵 추출 실패. 종료합니다.", file=sys.stderr)
        sys.exit(1)

    print(f"  Kindle Location 수: {len(kl_offsets)}")
    print(f"  책 텍스트 길이: {len(book_text):,} 문자")

    # 3. 복구
    n = recover_text(clips, kl_offsets, book_text, force=args.all)
    print(f"\n복구 완료: {n}개 / {len(limit_clips)}개 한도 초과 항목")

    # 4. 출력
    fmt = args.format
    if fmt is None:
        ext_map = {".json": "json", ".csv": "csv", ".md": "markdown", ".txt": "text"}
        fmt = ext_map.get(Path(args.output).suffix.lower(), "json")

    clips.sort(key=lambda c: c.added_date or "")
    out_path = Path(args.output)

    if fmt == "json":
        export_json(clips, out_path)
    elif fmt == "csv":
        export_csv(clips, out_path)
    elif fmt == "markdown":
        export_markdown(clips, out_path)
    else:
        export_text(clips, out_path)


if __name__ == "__main__":
    main()
