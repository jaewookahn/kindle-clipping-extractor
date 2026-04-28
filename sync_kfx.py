#!/usr/bin/env python3
"""
sync_kfx.py — 킨들 KFX 파일 기반 클리핑 자동 동기화 파이프라인

My Clippings.txt 대신 .sdr 사이드카의 YJR 파일을 직접 파싱한다.
YJR은 KFX 내부 character offset을 그대로 저장하므로 텍스트가 정확히 복원된다.
(My Clippings.txt는 Kindle Location 번호를 저장하며, 한도 초과 시 본문이 누락된다.)

파이프라인:
  1. 킨들 마운트 감지  (또는 --kindle 경로 직접 지정)
  2. documents/ 아래 KFX + 짝꿍 .sdr/*.yjr 쌍 탐색
  3. YJR 파싱 → fingerprint로 신규 항목만 추출
  4. KFX에서 제목·저자 메타데이터, 텍스트·페이지·Kindle Location 번호 추출
  5. 신규 클리핑 저장  (JSON은 책별 구조 + 타입별 카운트)
  6. 상태 파일 업데이트
  7. warning/error 로그 파일 저장 및 요약 출력

사용법:
    python sync_kfx.py -o new_clips.json            # 기본 동기화
    python sync_kfx.py --dry-run                    # 저장 없이 신규 목록만 출력
    python sync_kfx.py --reset -o full.json         # 상태 초기화 후 전체 재동기화
    python sync_kfx.py --kindle /Volumes/Kindle -o out.md
    python sync_kfx.py --list-books                 # KFX 목록만 출력
    python sync_kfx.py --log sync.log -o out.json   # 로그 파일 지정

JSON 출력 구조 (책별):
    {
      "synced_at": "...",
      "books": [
        {
          "title": "채식주의자",
          "author": "한강",
          "total_highlights": 15,
          "total_bookmarks": 3,
          "total_notes": 2,
          "clippings": [...]
        }
      ]
    }
"""

import argparse
import contextlib
import hashlib
import io
import json
import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Set

from tqdm import tqdm

from kindle.device import find_kindle
from kindle.models import Clipping
from kindle.parsers.yjr import parse_yjr
from kindle.ebook import (
    extract_kfx_metadata,
    extract_kfx_info,
    fill_clipping_text,
    fill_clipping_pages,
    fill_clipping_kindle_locations,
)
from kindle.exporters import (
    sync_export_csv,
    sync_export_markdown,
    sync_export_text,
    sync_export_json_grouped,
)
from kindle.notion_export import (
    sync_to_notion,
    DEFAULT_STATE as NOTION_DEFAULT_STATE,
)


# ---------------------------------------------------------------------------
# 로깅
# ---------------------------------------------------------------------------

_log_counts: Counter = Counter()
_current_book: str = ""           # 현재 처리 중인 책 (process_book에서 설정)
_file_handler: "_KindleFileHandler | None" = None

# kfxlib이 대량으로 발생시키는 반복 경고 패턴
_COLLAPSE_PATTERNS = ("position_id map extra", "position_id content extra")


class _KindleFileHandler(logging.FileHandler):
    """
    파일 핸들러 3-in-1:
    1. 책 컨텍스트 주입: kfxlib 등 외부 라이브러리 로그에 현재 책 제목 자동 삽입
    2. 반복 경고 collapse: position_id 경고 수천 건 → 책당 1줄 요약
    3. warning/error 카운팅
    """

    def __init__(self, path: Path) -> None:
        super().__init__(str(path), encoding="utf-8")
        self.setLevel(logging.WARNING)
        self.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        self._pending: Counter = Counter()
        self._pending_book: str = ""

    def emit(self, record: logging.LogRecord) -> None:
        book = _current_book
        msg  = record.getMessage()

        # 반복 패턴: 개별 레코드 억제하고 카운트만 누적
        for pat in _COLLAPSE_PATTERNS:
            if pat in msg:
                if self._pending_book != book and self._pending:
                    self._flush_pending()
                self._pending_book = book
                self._pending[pat] += 1
                return

        # 일반 레코드: 남은 pending flush 후 기록
        if self._pending:
            self._flush_pending()

        # 책 제목 주입 (메시지가 아직 '[...'로 시작하지 않는 경우만)
        if book and not msg.startswith("["):
            record = logging.makeLogRecord(record.__dict__)
            record.msg  = f"[{book}] {record.msg}"
            record.args = ()

        if record.levelno >= logging.ERROR:
            _log_counts["error"] += 1
        elif record.levelno >= logging.WARNING:
            _log_counts["warning"] += 1

        super().emit(record)

    def _flush_pending(self) -> None:
        if not self._pending:
            return
        total    = sum(self._pending.values())
        patterns = ", ".join(f"{k}({v:,})" for k, v in sorted(self._pending.items()))
        r = logging.LogRecord(
            name="kfxlib", level=logging.WARNING,
            pathname="", lineno=0,
            msg=f"[{self._pending_book}] position_id 경고 {total:,}회 (collapse): {patterns}",
            args=(), exc_info=None,
        )
        _log_counts["warning"] += 1
        super().emit(r)
        self._pending.clear()

    def close(self) -> None:
        self._flush_pending()
        super().close()


def setup_logging(log_path: Path) -> None:
    global _file_handler
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    _file_handler = _KindleFileHandler(log_path)
    root.addHandler(_file_handler)


def flush_book_log() -> None:
    """책 처리 완료 후 누적 경고를 즉시 파일에 flush."""
    if _file_handler:
        _file_handler._flush_pending()


logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _capture_stderr_to_log(book_title: str):
    """print(..., file=sys.stderr) 출력을 logger로 전달."""
    buf = io.StringIO()
    old_stderr, sys.stderr = sys.stderr, buf
    try:
        yield
    finally:
        sys.stderr = old_stderr
        for line in buf.getvalue().splitlines():
            line = line.strip()
            if not line:
                continue
            if "[warn]" in line.lower():
                logger.warning("[%s] %s", book_title, line)
            else:
                logger.error("[%s] %s", book_title, line)


# ---------------------------------------------------------------------------
# 상태 파일
# ---------------------------------------------------------------------------

DEFAULT_STATE = Path.home() / ".kindle_kfx_sync.json"
DEFAULT_LOG   = Path("kindle_sync.log")


def load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("상태 파일 로드 실패 (%s): %s", path, e)
    return {"last_sync": None, "total_synced": 0, "seen_keys": []}


def save_state(path: Path, state: dict) -> None:
    try:
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error("상태 파일 저장 실패 (%s): %s", path, e)


def _fingerprint(c: Clipping) -> str:
    key = f"{c.book_title}|{c.clip_type}|{c.location_start}|{c.location_end}"
    return hashlib.sha1(key.encode()).hexdigest()


# ---------------------------------------------------------------------------
# KFX + .sdr 쌍 탐색
# ---------------------------------------------------------------------------

_KFX_EXTS = (".kfx", ".azw3", ".azw", ".mobi")


def find_kfx_sdr_pairs(documents: Path) -> list[tuple[Path, Path, str]]:
    """
    documents/ 아래에서 (kfx_path, sdr_path, file_stem) 쌍을 모두 찾아 반환.
    KFX 없이 .sdr만 있는 책, 또는 YJR이 없는 .sdr은 제외.
    """
    pairs: list[tuple[Path, Path, str]] = []

    for sdr in sorted(documents.rglob("*.sdr")):
        if not sdr.is_dir():
            continue
        stem = sdr.stem

        kfx = None
        for ext in _KFX_EXTS:
            candidate = sdr.parent / (stem + ext)
            if candidate.exists():
                kfx = candidate
                break

        if kfx is None or not list(sdr.glob("*.yjr")):
            continue

        pairs.append((kfx, sdr, stem))

    return pairs


# ---------------------------------------------------------------------------
# 단일 책 처리
# ---------------------------------------------------------------------------

def process_book(
    kfx_path: Path,
    sdr_path: Path,
    file_stem: str,
    seen_keys: Set[str],
    pbar: "tqdm",
) -> tuple[list[Clipping], str, str, int]:
    """
    YJR 파싱 → 신규 필터 → KFX 메타데이터·텍스트·페이지·KL 번호 채우기.

    Returns:
        (new_clippings, real_title, author, skipped_count)
    """
    global _current_book

    # 1. 메타데이터 (실제 제목·저자)
    _current_book = file_stem   # 메타데이터 추출 전: 파일명으로 초기화
    pbar.set_postfix_str(f"{file_stem[:40]}  메타데이터", refresh=True)
    with _capture_stderr_to_log(file_stem):
        meta = extract_kfx_metadata(kfx_path)
    real_title = meta["title"]
    author     = meta["author"]
    _current_book = real_title  # 실제 제목으로 업데이트 → 이후 kfxlib 로그에 반영

    # 2. YJR 파싱 (메타데이터에서 얻은 제목으로 book_title 설정)
    pbar.set_postfix_str(f"{real_title[:40]}  YJR 파싱", refresh=True)
    all_clips: list[Clipping] = []
    for yjr in sdr_path.glob("*.yjr"):
        clips = parse_yjr(yjr, book_title=real_title)
        for c in clips:
            c.author = author
        all_clips.extend(clips)

    if not all_clips:
        return [], real_title, author, 0

    # fingerprint는 실제 제목 기준이 아닌 위치 기준이므로 file_stem도 포함
    new_clips = [c for c in all_clips if _fingerprint(c) not in seen_keys]
    skipped   = len(all_clips) - len(new_clips)

    if not new_clips:
        return [], real_title, author, skipped

    # 3. KFX 텍스트·페이지·KL 번호 추출
    pbar.set_postfix_str(f"{real_title[:40]}  KFX 추출", refresh=True)
    with _capture_stderr_to_log(real_title):
        page_map, kl_offsets, book_text = extract_kfx_info(kfx_path)

    if book_text:
        fill_clipping_text(new_clips, book_text)
    else:
        logger.warning("[%s] book_text 추출 실패 — 하이라이트 내용이 비어있을 수 있음", real_title)

    if page_map:
        fill_clipping_pages(new_clips, page_map)

    if kl_offsets:
        fill_clipping_kindle_locations(new_clips, kl_offsets)
    else:
        logger.warning("[%s] Kindle Location 맵 없음 — location 번호가 raw offset으로 남음", real_title)

    return new_clips, real_title, author, skipped




# ---------------------------------------------------------------------------
# 파이프라인
# ---------------------------------------------------------------------------

def run_pipeline(args) -> int:
    setup_logging(Path(args.log))

    # ── 1. 킨들 마운트 감지 ──────────────────────────────────────────────
    if args.kindle:
        kindle_root = Path(args.kindle)
        if not kindle_root.exists():
            print(f"오류: 경로를 찾을 수 없습니다: {kindle_root}", file=sys.stderr)
            return 1
        print(f"킨들 경로 지정: {kindle_root}")
    else:
        print("킨들 마운트 탐색 중 …")
        kindle_root = find_kindle()
        if not kindle_root:
            print("오류: 마운트된 킨들 디바이스를 찾을 수 없습니다.", file=sys.stderr)
            return 1
        print(f"킨들 감지: {kindle_root}")

    documents = kindle_root / "documents"

    # ── 2. KFX + .sdr 쌍 탐색 ────────────────────────────────────────────
    print("\nKFX + YJR 쌍 탐색 중 …")
    pairs = find_kfx_sdr_pairs(documents)
    print(f"  발견: {len(pairs)}개 책")

    if not pairs:
        print("처리할 책이 없습니다.")
        return 0

    if args.list_books:
        print(f"\n{'#':<4} {'파일명 (stem)':<55} 포맷")
        print("-" * 70)
        for i, (kfx, _, stem) in enumerate(pairs, 1):
            print(f"{i:<4} {stem:<55} {kfx.suffix}")
        return 0

    # ── 3. 상태 로드 ─────────────────────────────────────────────────────
    state_path = Path(args.state)
    if args.reset:
        state = {"last_sync": None, "total_synced": 0, "seen_keys": []}
        print("상태 초기화 — 전체 재동기화")
    else:
        state = load_state(state_path)
        if state["last_sync"]:
            print(f"마지막 동기화: {state['last_sync']}  (누적 {state['total_synced']}개)")

    seen_keys: Set[str] = set(state.get("seen_keys", []))

    # ── 4. 책별 처리 (progress bar) ──────────────────────────────────────
    all_new: list[Clipping] = []
    book_stats: list[dict]  = []

    with tqdm(
        pairs,
        desc="책 처리",
        unit="권",
        dynamic_ncols=True,
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}] {postfix}",
    ) as pbar:
        for kfx, sdr, stem in pbar:
            new_clips, real_title, author, skipped = process_book(
                kfx, sdr, stem, seen_keys, pbar
            )
            flush_book_log()   # 이 책의 누적 경고를 즉시 파일에 기록
            all_new.extend(new_clips)
            book_stats.append({
                "title": real_title, "author": author,
                "new": len(new_clips), "skipped": skipped,
            })
            pbar.set_postfix_str(
                f"{real_title[:35]}  +{len(new_clips)} / skip {skipped}",
                refresh=True,
            )

    print(f"\n총 신규 클리핑: {len(all_new)}개")

    if not all_new:
        print("신규 클리핑이 없습니다.")
        _print_log_summary(Path(args.log))
        return 0

    if args.dry_run:
        print("\n[dry-run] 저장 생략. 신규 항목:")
        for c in all_new:
            loc = f"L{c.location_start}" + (f"-{c.location_end}" if c.location_end else "")
            print(f"  {c.book_title[:40]:<40}  {loc:<14}  {(c.content or '')[:70]}")
        _print_log_summary(Path(args.log))
        return 0

    all_new.sort(key=lambda c: (c.book_title, c.added_date or ""))

    # ── 5a. 파일 출력 (선택) ─────────────────────────────────────────────
    if args.output:
        out_path = Path(args.output)
        fmt = args.format
        if fmt is None:
            ext_map = {".json": "json", ".csv": "csv", ".md": "markdown", ".txt": "text"}
            fmt = ext_map.get(out_path.suffix.lower(), "json")

        meta = {
            "synced_at":   datetime.now().isoformat(),
            "kindle_path": str(kindle_root),
            "new_count":   len(all_new),
            "book_count":  len({c.book_title for c in all_new}),
        }

        if fmt == "json":
            sync_export_json_grouped(all_new, out_path, meta)
        elif fmt == "csv":
            sync_export_csv(all_new, out_path)
        elif fmt == "markdown":
            sync_export_markdown(all_new, out_path, heading="킨들 KFX 클리핑")
        else:
            sync_export_text(all_new, out_path)
        print(f"저장: {out_path}")

    # ── 5b. Notion 업로드 (선택) ─────────────────────────────────────────
    if args.notion_token and args.notion_db:
        print("\nNotion 업로드 중 …")
        result = sync_to_notion(
            all_new,
            notion_token=args.notion_token,
            database_id=args.notion_db,
            state_path=Path(args.notion_state),
            enable_book_cover=not args.no_cover,
        )
        print(
            f"Notion 완료: 추가 {result['added']}개 / skip {result['skipped']}개"
            f"  (신규 책 {result['books_new']}권 / 업데이트 {result['books_updated']}권)"
        )

    # ── 6. 상태 업데이트 ─────────────────────────────────────────────────
    for c in all_new:
        seen_keys.add(_fingerprint(c))

    state["last_sync"]    = datetime.now().isoformat()
    state["total_synced"] = state.get("total_synced", 0) + len(all_new)
    state["seen_keys"]    = sorted(seen_keys)

    save_state(state_path, state)
    print(f"상태 저장: {state_path}  (누적 {state['total_synced']}개)")

    # ── 7. 로그 요약 ─────────────────────────────────────────────────────
    _print_log_summary(Path(args.log))

    return 0


def _print_log_summary(log_path: Path) -> None:
    w = _log_counts["warning"]
    e = _log_counts["error"]
    if w == 0 and e == 0:
        return
    parts = []
    if e:
        parts.append(f"error {e}개")
    if w:
        parts.append(f"warning {w}개")
    print(f"\n{'─'*40}")
    print(f"{'  '.join(parts)} 발생 — 상세: {log_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="킨들 KFX 파일 기반 클리핑 자동 동기화",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--kindle", default=None, metavar="PATH",
                        help="킨들 마운트 경로 (생략 시 자동 감지)")
    parser.add_argument("-o", "--output", default=None, metavar="FILE",
                        help="출력 파일 경로 (--dry-run, --list-books 없이는 필수)")
    parser.add_argument("-f", "--format",
                        choices=["json", "csv", "markdown", "text"], default=None,
                        help="출력 형식 (기본값: 확장자에서 추론)")
    parser.add_argument("--state", default=str(DEFAULT_STATE), metavar="FILE",
                        help=f"상태 파일 경로 (기본값: {DEFAULT_STATE})")
    parser.add_argument("--log", default=str(DEFAULT_LOG), metavar="FILE",
                        help=f"로그 파일 경로 (기본값: {DEFAULT_LOG})")
    parser.add_argument("--dry-run", action="store_true",
                        help="신규 목록만 출력, 파일 저장 및 상태 업데이트 없음")
    parser.add_argument("--reset", action="store_true",
                        help="상태 파일 초기화 후 전체 재동기화")
    parser.add_argument("--list-books", action="store_true",
                        help="KFX + YJR 쌍 목록만 출력하고 종료")
    # Notion
    parser.add_argument("--notion-token", default=None, metavar="TOKEN",
                        help="Notion 통합 토큰 (NOTION_TOKEN 환경변수로도 설정 가능)")
    parser.add_argument("--notion-db", default=None, metavar="DB_ID",
                        help="업로드할 Notion 데이터베이스 ID")
    parser.add_argument("--notion-state", default=str(NOTION_DEFAULT_STATE), metavar="FILE",
                        help=f"Notion 상태 파일 경로 (기본값: {NOTION_DEFAULT_STATE})")
    parser.add_argument("--no-cover", action="store_true",
                        help="Notion 페이지에 책 표지 추가 안 함")
    args = parser.parse_args()

    # NOTION_TOKEN 환경변수 폴백
    if not args.notion_token:
        import os
        args.notion_token = os.environ.get("NOTION_TOKEN")

    has_output = bool(args.output)
    has_notion = bool(args.notion_token and args.notion_db)
    if not args.dry_run and not args.list_books and not has_output and not has_notion:
        parser.error("--output, --notion-token+--notion-db, --dry-run, --list-books 중 하나가 필요합니다.")

    sys.exit(run_pipeline(args))


if __name__ == "__main__":
    main()
