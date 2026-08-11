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
    python sync_kfx.py --book gongsandang -o out.json  # 특정 책만 (stem substring, 대소문자 무시)
    python sync_kfx.py --book A --book B --dry-run     # 여러 책 지정
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
from typing import Optional, Set

from dotenv import load_dotenv
load_dotenv()

from tqdm import tqdm

from kindle.device import find_kindle
from kindle.models import Clipping
from kindle.parsers.yjr import parse_yjr
from kindle.ebook import (
    extract_kfx_metadata,
    extract_kfx_info,
    fill_clipping_text,
    fill_clipping_pages,
    fill_clipping_chapters,
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
from kindle.title_cache import (
    load_cache as load_title_cache,
    save_cache as save_title_cache,
    get_or_extract as get_or_extract_title,
    DEFAULT_PATH as DEFAULT_TITLE_CACHE,
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


def _book_status(b: dict) -> str:
    if b["yjr_count"] > 0:
        return f"{b['yjr_count']} YJR"
    if b["sdr"] is not None:
        return "SDR만"
    return "없음"


def _vis_pad(s: str, width: int) -> str:
    """CJK 문자(전각)는 2칸으로 계산해 시각적 정렬을 맞춤. 초과 시 truncate."""
    import unicodedata
    def w(c: str) -> int:
        return 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
    out, cur = "", 0
    for c in s:
        cw = w(c)
        if cur + cw > width:
            break
        out += c
        cur += cw
    return out + " " * (width - cur)


def list_kfx_books(documents: Path) -> list[dict]:
    """
    documents/ 직속 KFX 파일을 모두 나열하고 .sdr/YJR 상태를 함께 반환.

    Kindle은 .sdr 폴더와 책 파일이 documents/ 직속에 평탄하게 놓여 있으므로
    rglob 대신 한 단계 iterdir만 사용한다. (MacDroid FileProvider 같은 가상
    마운트에서 rglob은 모든 .sdr 내부까지 MTP로 재귀 탐색하여 매우 느리다.)

    Returns:
        list of {"stem", "kfx": Path, "sdr": Path|None, "yjr_count": int}, sorted by stem.
    """
    ebooks_by_stem: dict[str, Path] = {}   # 동일 stem에 여러 확장자가 있으면 _KFX_EXTS 순서로 우선
    sdrs_by_stem: dict[str, Path] = {}
    try:
        for entry in documents.iterdir():
            name_lower = entry.name.lower()
            if name_lower.endswith(".sdr") and entry.is_dir():
                sdrs_by_stem[entry.stem] = entry
            else:
                ext = entry.suffix.lower()
                if ext in _KFX_EXTS and entry.is_file():
                    existing = ebooks_by_stem.get(entry.stem)
                    if existing is None or _KFX_EXTS.index(ext) < _KFX_EXTS.index(existing.suffix.lower()):
                        ebooks_by_stem[entry.stem] = entry
    except (PermissionError, OSError):
        return []

    results: list[dict] = []
    for stem in sorted(ebooks_by_stem):
        sdr = sdrs_by_stem.get(stem)
        yjr_count = 0
        last_mtime: float = 0.0
        kfx_path = ebooks_by_stem[stem]
        try:
            last_mtime = kfx_path.stat().st_mtime
        except OSError:
            pass
        if sdr is not None:
            try:
                for f in sdr.iterdir():
                    if f.suffix.lower() == ".yjr":
                        yjr_count += 1
                    try:
                        m = f.stat().st_mtime
                        if m > last_mtime:
                            last_mtime = m
                    except OSError:
                        pass
            except (PermissionError, OSError):
                pass
        results.append({
            "stem": stem,
            "kfx": kfx_path,
            "sdr": sdr,
            "yjr_count": yjr_count,
            "last_mtime": last_mtime,
        })
    return results


def find_kfx_sdr_pairs(documents: Path) -> list[tuple[Path, Path, str]]:
    """클리핑(YJR)이 있는 KFX+SDR 쌍만 반환. 처리 파이프라인 입력."""
    return [
        (b["kfx"], b["sdr"], b["stem"])
        for b in list_kfx_books(documents)
        if b["sdr"] is not None and b["yjr_count"] > 0
    ]


# ---------------------------------------------------------------------------
# 단일 책 처리
# ---------------------------------------------------------------------------

def process_book(
    kfx_path: Path,
    sdr_path: Path,
    file_stem: str,
    seen_keys: Set[str],
    pbar: "tqdm",
    title_cache: Optional[dict] = None,
) -> tuple[list[Clipping], list[str], str, str, int]:
    """
    YJR 파싱 → 신규 필터 → KFX 메타데이터·텍스트·페이지·KL 번호 채우기.

    Fingerprint 는 fill_clipping_kindle_locations 호출 전(raw char offset 상태)에
    한 번만 계산해 둔다. 이렇게 해야 seen_keys 비교와 저장이 같은 값을 쓰게 되어
    재실행 시 동일 클리핑이 다시 "신규" 로 잡히지 않는다.

    Returns:
        (new_clippings, new_fingerprints, real_title, author, skipped_count)
    """
    global _current_book

    # 1. 메타데이터 (실제 제목·저자) — 캐시 hit 시 kfxlib 호출 생략
    _current_book = file_stem   # 메타데이터 추출 전: 파일명으로 초기화
    pbar.set_postfix_str(f"{file_stem[:40]}  메타데이터", refresh=True)

    def _extract(p: Path) -> dict:
        with _capture_stderr_to_log(file_stem):
            return extract_kfx_metadata(p)

    if title_cache is not None:
        meta = get_or_extract_title(title_cache, kfx_path, _extract)
    else:
        meta = _extract(kfx_path)
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
        return [], [], real_title, author, 0

    # fingerprint 는 fill 전 raw char offset 기준으로 한 번에 계산
    fingerprints = [_fingerprint(c) for c in all_clips]
    pairs = [(fp, c) for fp, c in zip(fingerprints, all_clips) if fp not in seen_keys]
    new_clips = [c for _, c in pairs]
    new_fps   = [fp for fp, _ in pairs]
    skipped   = len(all_clips) - len(new_clips)

    if not new_clips:
        return [], [], real_title, author, skipped

    # 3. KFX 텍스트·페이지·KL 번호 추출
    pbar.set_postfix_str(f"{real_title[:40]}  KFX 추출", refresh=True)
    with _capture_stderr_to_log(real_title):
        page_map, kl_offsets, book_text, toc = extract_kfx_info(kfx_path)

    if book_text:
        fill_clipping_text(new_clips, book_text)
    else:
        logger.warning("[%s] book_text 추출 실패 — 하이라이트 내용이 비어있을 수 있음", real_title)

    if page_map:
        fill_clipping_pages(new_clips, page_map)

    if toc:
        fill_clipping_chapters(new_clips, toc)

    if kl_offsets:
        fill_clipping_kindle_locations(new_clips, kl_offsets)
    else:
        logger.warning("[%s] Kindle Location 맵 없음 — location 번호가 raw offset으로 남음", real_title)

    return new_clips, new_fps, real_title, author, skipped




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

    # ── 2. KFX 책 + 클리핑 상태 탐색 ─────────────────────────────────────
    print("\nKFX 책 탐색 중 …")
    books = list_kfx_books(documents)
    with_clips = [b for b in books if b["yjr_count"] > 0]
    print(f"  KFX 총 {len(books)}권 (클리핑 있음: {len(with_clips)}권)")

    if args.book:
        patterns = [p.lower() for p in args.book]
        books = [b for b in books if any(p in b["stem"].lower() for p in patterns)]
        with_clips = [b for b in books if b["yjr_count"] > 0]
        print(f"  --book 필터: {len(books)}권 매칭 / 클리핑 {len(with_clips)}권  "
              f"(패턴: {', '.join(args.book)})")
        if not books:
            print("매칭되는 책이 없습니다. --list-books 로 stem 확인 후 다시 시도하세요.", file=sys.stderr)
            return 1

    if args.list_books:
        if args.titles:
            cache_path = Path(args.title_cache)
            cache = load_title_cache(cache_path)
            hits = 0

            def _extract(p: Path) -> dict:
                with _capture_stderr_to_log(p.stem):
                    return extract_kfx_metadata(p)

            print()
            for b in tqdm(books, desc="제목 조회", unit="권", dynamic_ncols=True):
                before = len(cache.get("books", {}))
                meta = get_or_extract_title(cache, b["kfx"], _extract,
                                            refresh=args.refresh_titles)
                if len(cache.get("books", {})) == before and not args.refresh_titles:
                    hits += 1
                b["title"]  = meta["title"]
                b["author"] = meta["author"]
            save_title_cache(cache_path, cache)
            print(f"  캐시 hit {hits}/{len(books)}  ({cache_path})")

            print(f"\n{'#':<4} {_vis_pad('제목', 42)} {_vis_pad('저자', 20)} {'포맷':<6} 클리핑")
            print("-" * 90)
            for i, b in enumerate(books, 1):
                status = _book_status(b)
                print(f"{i:<4} {_vis_pad(b['title'], 42)} "
                      f"{_vis_pad(b['author'], 20)} {b['kfx'].suffix:<6} {status}")
        else:
            print(f"\n{'#':<4} {'파일명 (stem)':<55} {'포맷':<6} 클리핑")
            print("-" * 80)
            for i, b in enumerate(books, 1):
                status = _book_status(b)
                print(f"{i:<4} {b['stem']:<55} {b['kfx'].suffix:<6} {status}")
        return 0

    pairs = [(b["kfx"], b["sdr"], b["stem"]) for b in with_clips]
    if not pairs:
        print("처리할 책이 없습니다.")
        return 0

    # ── 3. 상태 로드 ─────────────────────────────────────────────────────
    state_path = Path(args.state)
    if args.reset:
        state = {"last_sync": None, "total_synced": 0, "seen_keys": []}
        print("로컬 상태 초기화 — 전체 재동기화")
    else:
        state = load_state(state_path)
        if state["last_sync"]:
            print(f"마지막 동기화: {state['last_sync']}  (누적 {state['total_synced']}개)")

    # Notion 상태 reset
    if args.reset_notion:
        nstate_path = Path(args.notion_state)
        if nstate_path.exists():
            nstate_path.unlink()
            print(f"Notion 상태 초기화: {nstate_path} 삭제됨")
        else:
            print(f"Notion 상태 파일 없음 (skip): {nstate_path}")

    seen_keys: Set[str] = set(state.get("seen_keys", []))

    # ── 4. 책별 처리 (progress bar) ──────────────────────────────────────
    all_new: list[Clipping] = []
    all_new_fps: list[str]  = []
    book_stats: list[dict]  = []

    title_cache_path = Path(args.title_cache)
    title_cache = load_title_cache(title_cache_path)

    import os as _os
    no_progress = args.no_progress or _os.environ.get("TQDM_DISABLE", "") in ("1", "true", "yes")

    total = len(pairs)
    with tqdm(
        pairs,
        desc="책 처리",
        unit="권",
        dynamic_ncols=True,
        disable=no_progress,
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}] {postfix}",
    ) as pbar:
        # rewrite-bodies: dedup 무시하고 전체 클리핑을 다시 끌어온다
        effective_seen = set() if args.rewrite_bodies else seen_keys
        for i, (kfx, sdr, stem) in enumerate(pbar, 1):
            new_clips, new_fps, real_title, author, skipped = process_book(
                kfx, sdr, stem, effective_seen, pbar, title_cache=title_cache,
            )
            flush_book_log()   # 이 책의 누적 경고를 즉시 파일에 기록
            all_new.extend(new_clips)
            all_new_fps.extend(new_fps)
            book_stats.append({
                "title": real_title, "author": author,
                "new": len(new_clips), "skipped": skipped,
            })
            if no_progress:
                print(
                    f"[{i:>3}/{total}] {real_title[:50]}   +{len(new_clips)} new / skip {skipped}",
                    flush=True,
                )
            else:
                pbar.set_postfix_str(
                    f"{real_title[:35]}  +{len(new_clips)} / skip {skipped}",
                    refresh=True,
                )

    save_title_cache(title_cache_path, title_cache)
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
            rewrite=args.rewrite_bodies,
        )
        print(
            f"Notion 완료: 추가 {result['added']}개 / skip {result['skipped']}개"
            f"  (신규 책 {result['books_new']}권 / 업데이트 {result['books_updated']}권)"
        )

    # ── 6. 상태 업데이트 ─────────────────────────────────────────────────
    # fingerprint 는 fill 이전 raw offset 기준으로 process_book 에서 미리 계산해 둠
    for fp in all_new_fps:
        seen_keys.add(fp)

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
                        help="로컬 KFX 상태 파일 초기화 후 전체 재동기화")
    parser.add_argument("--reset-notion", action="store_true",
                        help="Notion 측 상태 파일도 비움 (--notion-state). "
                             "주의: 기존 Notion 페이지에 클리핑이 재추가되어 "
                             "중복될 수 있음. 페이지 미리 삭제 권장.")
    parser.add_argument("--rewrite-bodies", action="store_true",
                        help="이미 동기화된 책도 Notion 페이지 본문을 통째로 다시 씀 "
                             "(챕터 정보 등 포맷 변경 백필용). fingerprint·표지·"
                             "속성은 보존. dedup 무시하고 전체 클리핑 재업로드.")
    parser.add_argument("--list-books", action="store_true",
                        help="documents/ 의 KFX 책 목록 + 클리핑 상태 출력 후 종료")
    parser.add_argument("--no-progress", action="store_true",
                        help="tqdm 진행 바를 끄고 각 책의 결과를 1줄 print로 출력 "
                             "(TUI·로그 캡처용. TQDM_DISABLE=1 환경변수로도 활성화)")
    parser.add_argument("--titles", action="store_true",
                        help="--list-books 에 KFX 메타데이터로 실제 제목·저자 표시 "
                             "(권당 kfxlib 호출이 들어가 느려짐, 결과는 캐싱됨)")
    parser.add_argument("--title-cache", default=str(DEFAULT_TITLE_CACHE), metavar="FILE",
                        help=f"제목·저자 캐시 경로 (기본값: {DEFAULT_TITLE_CACHE})")
    parser.add_argument("--refresh-titles", action="store_true",
                        help="--titles 캐시를 무시하고 강제 재추출")
    parser.add_argument("--book", action="append", default=None, metavar="PATTERN",
                        help="특정 책만 처리 (stem 파일명 substring, 대소문자 무시). "
                             "여러 번 지정 가능: --book A --book B")
    # Notion
    parser.add_argument("--notion-token", default=None, metavar="TOKEN",
                        help="Notion 통합 토큰 (NOTION_TOKEN 환경변수로도 설정 가능)")
    parser.add_argument("--notion-db", default=None, metavar="DB_ID",
                        help="업로드할 Notion 데이터베이스 ID (NOTION_DB 환경변수로도 설정 가능)")
    parser.add_argument("--notion-state", default=str(NOTION_DEFAULT_STATE), metavar="FILE",
                        help=f"Notion 상태 파일 경로 (기본값: {NOTION_DEFAULT_STATE})")
    parser.add_argument("--no-cover", action="store_true",
                        help="Notion 페이지에 책 표지 추가 안 함")
    args = parser.parse_args()

    # NOTION_TOKEN / NOTION_DB 환경변수 폴백 (.env 포함)
    import os
    if not args.notion_token:
        args.notion_token = os.environ.get("NOTION_TOKEN")
    if not args.notion_db:
        args.notion_db = os.environ.get("NOTION_DB")

    has_output = bool(args.output)
    has_notion = bool(args.notion_token and args.notion_db)
    if not args.dry_run and not args.list_books and not has_output and not has_notion:
        parser.error("--output, --notion-token+--notion-db, --dry-run, --list-books 중 하나가 필요합니다.")

    sys.exit(run_pipeline(args))


if __name__ == "__main__":
    main()
