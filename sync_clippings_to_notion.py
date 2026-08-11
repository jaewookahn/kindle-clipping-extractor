#!/usr/bin/env python3
"""
sync_clippings_to_notion.py — My Clippings.txt → Notion 보충 동기화

KFX+YJR 파이프라인(sync_kfx.py)이 처리하지 못한 책을 보충하기 위한
수동 실행 스크립트.

- sync_kfx.py와 동일한 Notion 상태 파일을 공유하므로
  KFX+YJR로 이미 올라간 클리핑은 자동으로 중복 방지된다.
- KFX Input 플러그인이 있으면 한도 초과 텍스트를 자동 복구한다.

사용법:
    python sync_clippings_to_notion.py "My Clippings.txt" \\
        --notion-token TOKEN --notion-db DB_ID

    # 환경변수 사용
    export NOTION_TOKEN=secret_xxx
    python sync_clippings_to_notion.py "My Clippings.txt" --notion-db DB_ID

    # 특정 책만
    python sync_clippings_to_notion.py "My Clippings.txt" \\
        --notion-db DB_ID --book "채식주의자"

    # 상태 파일 확인
    python sync_clippings_to_notion.py "My Clippings.txt" --stats
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from kindle.parsers.my_clippings import parse_my_clippings, is_limit_exceeded
from kindle.ebook import _find_kfx_plugin, extract_kfx_info
from kindle.models import Clipping
from kindle.notion_export import sync_to_notion, DEFAULT_STATE, load_state

from typing import List


# ---------------------------------------------------------------------------
# KFX 텍스트 복구 (킨들 마운트 없이 경로 직접 지정)
# ---------------------------------------------------------------------------

def _recover_from_kfx(clippings: List[Clipping], kfx_path: Path) -> int:
    """한도 초과 클리핑을 KFX 파일에서 복구. 복구 수 반환."""
    import re

    _, kl_offsets, book_text, _ = extract_kfx_info(kfx_path)
    if not kl_offsets or not book_text:
        return 0

    recovered = 0
    n = len(kl_offsets)
    for c in clippings:
        if c.clip_type not in ("highlight", "bookmark"):
            continue
        if not is_limit_exceeded(c.content):
            continue
        if c.location_start is None:
            continue
        kl_s = c.location_start
        kl_e = c.location_end if c.location_end else kl_s
        if kl_s < 1 or kl_s > n:
            continue
        char_start = kl_offsets[kl_s - 1]
        char_end   = kl_offsets[kl_e] if kl_e < n else len(book_text)
        if char_start >= len(book_text):
            continue
        snippet = book_text[char_start:min(char_end, len(book_text))].strip()
        snippet = re.sub(r"\s+", " ", snippet)
        if snippet:
            c.content   = snippet
            c.recovered = True
            recovered  += 1
    return recovered


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="My Clippings.txt → Notion 보충 동기화",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("clippings_file", help="My Clippings.txt 경로")
    parser.add_argument("--notion-token", default=None, metavar="TOKEN",
                        help="Notion 통합 토큰 (NOTION_TOKEN 환경변수로도 설정 가능)")
    parser.add_argument("--notion-db", default=None, metavar="DB_ID",
                        help="업로드할 Notion 데이터베이스 ID (NOTION_DB 환경변수로도 설정 가능)")
    parser.add_argument("--notion-state", default=str(DEFAULT_STATE), metavar="FILE",
                        help=f"Notion 상태 파일 경로 (기본값: {DEFAULT_STATE})")
    parser.add_argument("--book", default=None, metavar="TITLE",
                        help="처리할 책 제목 (부분 일치, 생략 시 전체)")
    parser.add_argument("--kfx", default=None, metavar="PATH",
                        help="한도 초과 텍스트 복구에 쓸 KFX 파일 경로")
    parser.add_argument("--no-cover", action="store_true",
                        help="Notion 페이지에 책 표지 추가 안 함")
    parser.add_argument("--stats", action="store_true",
                        help="상태 파일의 동기화 현황만 출력하고 종료")
    parser.add_argument("--dry-run", action="store_true",
                        help="업로드 없이 신규 항목 목록만 출력")
    args = parser.parse_args()

    # NOTION_TOKEN / NOTION_DB 환경변수 폴백 (.env 포함)
    notion_token = args.notion_token or os.environ.get("NOTION_TOKEN")
    if not args.notion_db:
        args.notion_db = os.environ.get("NOTION_DB")

    # --stats: 상태 파일 현황 출력
    if args.stats:
        state = load_state(Path(args.notion_state))
        books = state.get("books", {})
        print(f"Notion 동기화 현황  ({args.notion_state})")
        print(f"{'책 수'}: {len(books)}권")
        for title, info in sorted(books.items()):
            fps = len(info.get("synced_fingerprints", []))
            page_id = info.get("notion_page_id", "미생성")
            print(f"  {title[:50]:<50}  {fps:>4}개  page={page_id[:8]}…")
        return

    if not notion_token:
        parser.error("--notion-token 또는 NOTION_TOKEN 환경변수가 필요합니다.")
    if not args.notion_db:
        parser.error("--notion-db 가 필요합니다.")

    clippings_path = Path(args.clippings_file)
    if not clippings_path.exists():
        print(f"오류: {clippings_path} 를 찾을 수 없습니다.", file=sys.stderr)
        sys.exit(1)

    # 1. 파싱
    print(f"파싱: {clippings_path}")
    all_clips = parse_my_clippings(clippings_path)
    print(f"  총 {len(all_clips)}개 클리핑")

    if args.book:
        q = args.book.lower()
        all_clips = [c for c in all_clips if q in c.book_title.lower()]
        print(f"  '{args.book}' 필터 후: {len(all_clips)}개")

    if not all_clips:
        print("처리할 클리핑이 없습니다.")
        return

    # 2. 한도 초과 복구 (KFX 지정 시)
    if args.kfx:
        kfx_path = Path(args.kfx)
        if not kfx_path.exists():
            print(f"[경고] KFX 파일 없음: {kfx_path}", file=sys.stderr)
        elif not _find_kfx_plugin():
            print("[경고] KFX Input 플러그인 없음 — 복구 건너뜀", file=sys.stderr)
        else:
            print(f"KFX 복구: {kfx_path.name} …")
            n = _recover_from_kfx(all_clips, kfx_path)
            print(f"  → {n}개 복구")

    # 3. dry-run
    if args.dry_run:
        from kindle.notion_export import fingerprint, load_state as _load
        state = _load(Path(args.notion_state))
        synced: set = set()
        for info in state.get("books", {}).values():
            synced.update(info.get("synced_fingerprints", []))
        new_clips = [c for c in all_clips if fingerprint(c) not in synced]
        print(f"\n[dry-run] 신규 {len(new_clips)}개 / 전체 {len(all_clips)}개")
        for c in new_clips:
            loc = f"L{c.location_start}" + (f"-{c.location_end}" if c.location_end else "")
            print(f"  {c.book_title[:40]:<40}  {loc:<12}  {(c.content or '')[:60]}")
        return

    # 4. Notion 업로드
    print("\nNotion 업로드 중 …")
    result = sync_to_notion(
        all_clips,
        notion_token=notion_token,
        database_id=args.notion_db,
        state_path=Path(args.notion_state),
        enable_book_cover=not args.no_cover,
    )
    print(
        f"\nNotion 완료: 추가 {result['added']}개 / skip {result['skipped']}개"
        f"  (신규 책 {result['books_new']}권 / 업데이트 {result['books_updated']}권)"
    )


if __name__ == "__main__":
    main()
