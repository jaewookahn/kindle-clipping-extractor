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

from kindle import backup
from kindle.device import find_kindle
from kindle.models import Clipping
from kindle.parsers.yjr import parse_yjr
from kindle.ebook import (
    extract_kfx_metadata,
    extract_kfx_info,
    build_chapter_ranges,
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
    get_stale as get_stale_title,
    DEFAULT_PATH as DEFAULT_TITLE_CACHE,
)
from kindle.text_cache import (
    get_or_extract as text_cache_get,
    cache_key as text_cache_key,
    has as text_cache_has,
    DEFAULT_DIR as DEFAULT_TEXT_CACHE,
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
        #
        # `record.msg` 가 아니라 **이미 서식이 적용된** `msg` 를 쓴다. 원본
        # `record.msg` 는 "%s 이 %s 로 줄었습니다" 같은 템플릿이고, 여기서
        # args 를 비우면 치환이 영영 일어나지 않아 로그에 %s 가 그대로 찍힌다.
        # (`kindle/backup.py` 의 급감 경고에서 실제로 그렇게 나왔다. 기존
        #  메시지들이 대부분 '['로 시작해 이 경로를 안 타서 드러나지 않았다.)
        if book and not msg.startswith("["):
            record = logging.makeLogRecord(record.__dict__)
            record.msg  = f"[{book}] {msg}"
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
        with backup.guard(path, "kfx_state"):
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


def _wanted_stems(pairs, args, text_cache_dir: Optional[Path]) -> list[str]:
    """WiFi 로 본문(KFX)을 받아야 할 책의 stem 목록.

    "본문 캐시가 없는 책"이 대상이다. ASIN 은 제목 캐시에서 꺼내 쓴다 —
    여기서 KFX 를 새로 열면 지금 받으려는 그 파일을 읽어야 해서 앞뒤가 맞지
    않는다. ASIN 을 모르면 경로 기반 키로 떨어지는데, 그때는 캐시 hit 가
    안 나므로 자연히 요청 대상이 된다 (보수적으로 맞는 쪽).
    """
    if not args.wifi or args.wifi_kfx <= 0 or text_cache_dir is None:
        return []
    from kindle.title_cache import get_cached as _title_cached
    probe = load_title_cache(Path(args.title_cache))
    out: list[str] = []
    for kfx, _sdr, stem in pairs:
        hit = _title_cached(probe, kfx) or {}
        try:
            key = text_cache_key(hit.get("asin", ""), kfx)
        except ValueError:
            continue
        if not text_cache_has(key, text_cache_dir):
            out.append(stem)
        if len(out) >= args.wifi_kfx:
            break
    return out


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
    ksdk_by_asin: Optional[dict] = None,
    text_cache_dir: Optional[Path] = None,
    refresh_text: bool = False,
    allow_empty_text: bool = False,
) -> tuple[list[Clipping], list[str], str, str, int, list]:
    """
    YJR 파싱 → 신규 필터 → KFX 메타데이터·텍스트·페이지·KL 번호 채우기.

    Fingerprint 는 fill_clipping_kindle_locations 호출 전(raw char offset 상태)에
    한 번만 계산해 둔다. 이렇게 해야 seen_keys 비교와 저장이 같은 값을 쓰게 되어
    재실행 시 동일 클리핑이 다시 "신규" 로 잡히지 않는다.

    Returns:
        (new_clippings, new_fingerprints, real_title, author, skipped_count, chapters)
        chapters 는 이 책의 [Chapter, …] (TOC 없으면 빈 리스트).

    ksdk_by_asin: {ASIN: [Clipping, …]} 가 주어지면 **YJR 대신** KSDK DB 에서
        온 클리핑을 쓴다 (펌웨어 5.19.x 이후 사이드카가 고아화된 기기).
        좌표계가 같아서(PRE-KL char offset) 이후 fill_* 와 fingerprint 는
        그대로 동작한다. 자세한 경위는 KINDLE_ANNOTATION_OUTAGE.md 참조.
    """
    global _current_book

    # 1. 메타데이터 (실제 제목·저자) — 캐시 hit 시 kfxlib 호출 생략
    _current_book = file_stem   # 메타데이터 추출 전: 파일명으로 초기화
    pbar.set_postfix_str(f"{file_stem[:40]}  메타데이터", refresh=True)

    def _extract(p: Path) -> dict:
        with _capture_stderr_to_log(file_stem):
            meta = extract_kfx_metadata(p)
        # KFX 를 못 읽으면 ASIN 이 빈다 (제목은 파일명으로 대체되므로 실패
        # 신호가 못 된다). ASIN 이 없으면 KSDK 클리핑도 본문 캐시도 못 찾아
        # 책이 통째로 사라진다. 캐시가 ASIN 을 알고 있으면 그게 더 정확하다 —
        # 모르는 값을 아는 값으로 덮는 것이므로 안전하다.
        if title_cache is not None and not meta.get("asin"):
            stale = get_stale_title(title_cache, p)
            if stale and stale.get("asin"):
                logger.warning("[%s] 메타데이터에 ASIN 이 없음 — 캐시 값으로 진행 (%s)",
                               file_stem, stale["asin"])
                return stale
        return meta

    if title_cache is not None:
        meta = get_or_extract_title(title_cache, kfx_path, _extract)
        # asin 은 나중에 추가된 필드라 예전 캐시 항목에는 없다. KSDK 매칭은
        # ASIN 이 유일한 연결고리이므로, 비어 있으면 그 책만 강제 재추출한다.
        if ksdk_by_asin is not None and not meta.get("asin"):
            meta = get_or_extract_title(title_cache, kfx_path, _extract, refresh=True)
    else:
        meta = _extract(kfx_path)
    real_title = meta["title"]
    author     = meta["author"]
    _current_book = real_title  # 실제 제목으로 업데이트 → 이후 kfxlib 로그에 반영

    # 2. 클리핑 확보 — KSDK DB 우선, 없으면 YJR 사이드카
    #
    # KSDK 매칭은 ASIN 으로 한다. 사이드로드 책의 .sdr 폴더명에는 ASIN 이 없어서
    # kfxlib 메타데이터의 asin 이 유일한 연결고리다 (extract_kfx_metadata 참조).
    all_clips: list[Clipping] = []
    if ksdk_by_asin is not None:
        asin = meta.get("asin", "")
        pbar.set_postfix_str(f"{real_title[:40]}  KSDK", refresh=True)
        for c in ksdk_by_asin.get(asin, []):
            # book_title 은 반드시 제목 캐시 값으로 채운다. ASIN 플레이스홀더
            # 상태로 fingerprint 를 만들면 기존 동기화분과 전부 어긋나
            # (실측: 교집합 93 → 0) 이미 올린 항목이 중복 업로드된다.
            c.book_title = real_title
            c.author = author
            all_clips.append(c)
    else:
        pbar.set_postfix_str(f"{real_title[:40]}  YJR 파싱", refresh=True)
        # KSDK 모드에서는 .sdr 이 없는 책도 목록에 오므로 None 을 견뎌야 한다.
        for yjr in (sdr_path.glob("*.yjr") if sdr_path is not None else []):
            clips = parse_yjr(yjr, book_title=real_title)
            for c in clips:
                c.author = author
            all_clips.extend(clips)

    if not all_clips:
        return [], [], real_title, author, 0, []

    # fingerprint 는 fill 전 raw char offset 기준으로 한 번에 계산
    fingerprints = [_fingerprint(c) for c in all_clips]
    pairs = [(fp, c) for fp, c in zip(fingerprints, all_clips) if fp not in seen_keys]
    new_clips = [c for _, c in pairs]
    new_fps   = [fp for fp, _ in pairs]
    skipped   = len(all_clips) - len(new_clips)

    if not new_clips:
        return [], [], real_title, author, skipped, []

    # 3. KFX 텍스트·페이지·KL 번호 확보 (캐시 우선)
    #
    # 책은 안 변하고 어노테이션만 변한다. 그래서 추출 결과를 ASIN 키로 캐시해
    # 두면 이후에는 KFX 파일이 없어도 본문을 채울 수 있다 (kindle/text_cache.py).
    pbar.set_postfix_str(f"{real_title[:40]}  KFX 추출", refresh=True)

    def _extract_info(p: Path):
        with _capture_stderr_to_log(real_title):
            return extract_kfx_info(p)

    if text_cache_dir is not None:
        (page_map, kl_offsets, book_text, toc), src = text_cache_get(
            kfx_path, _extract_info, asin=meta.get("asin", ""), title=real_title,
            refresh=refresh_text, cache_dir=text_cache_dir,
        )
        if src == "cache":
            pbar.set_postfix_str(f"{real_title[:40]}  본문 캐시", refresh=True)
    else:
        page_map, kl_offsets, book_text, toc = _extract_info(kfx_path)

    if book_text:
        fill_clipping_text(new_clips, book_text)
    elif allow_empty_text:
        logger.warning("[%s] book_text 추출 실패 — 하이라이트 내용이 비어있을 수 있음", real_title)
    else:
        # 본문 없이 올리면 위치·시각만 있는 껍데기가 Notion 에 박히고
        # fingerprint 까지 기록돼 **다시 시도할 수도 없게 된다**
        # (2026-09-19: 마운트가 죽은 줄 모르고 157건을 그렇게 올렸다).
        # 조용히 진행하느니 이 책을 건너뛴다. 정말 위치만이라도 필요하면
        # --allow-empty-text 로 예전 동작을 쓸 수 있다.
        logger.error("[%s] book_text 를 얻지 못해 이 책을 건너뜁니다 "
                     "(KFX 를 읽을 수 없고 본문 캐시도 없음). "
                     "위치만이라도 올리려면 --allow-empty-text", real_title)
        return [], [], real_title, author, skipped + len(new_clips), []

    if page_map:
        fill_clipping_pages(new_clips, page_map)

    chapters: list = []
    if toc:
        fill_clipping_chapters(new_clips, toc)
        # 챕터 범위는 raw char offset 기준 맵을 쓰므로 KL 변환 전에 계산한다
        # (변환 후에는 page_map·kl_offsets 와 좌표계가 어긋난다).
        chapters = build_chapter_ranges(
            toc, page_map, kl_offsets, len(book_text) if book_text else None,
        )

    if kl_offsets:
        fill_clipping_kindle_locations(new_clips, kl_offsets)
    else:
        logger.warning("[%s] Kindle Location 맵 없음 — location 번호가 raw offset으로 남음", real_title)

    return new_clips, new_fps, real_title, author, skipped, chapters




# ---------------------------------------------------------------------------
# 파이프라인
# ---------------------------------------------------------------------------

def run_pipeline(args) -> int:
    setup_logging(Path(args.log))

    if getattr(args, "no_backup", False):
        backup.disable()
        print("⚠ 자동 백업이 꺼졌습니다 — 덮어쓰기 전 스냅샷이 만들어지지 않습니다.",
              file=sys.stderr)

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

    if args.book or args.book_exact:
        # --book 은 부분 문자열, --book-exact 는 stem 전체 일치.
        # 부분 문자열만 있으면 어떤 책의 stem 이 다른 책 stem 의 접두사일 때
        # (예: "… ver.1" ⊂ "… ver.1 … 2") 의도치 않게 여러 권이 딸려 온다.
        patterns = [p.lower() for p in (args.book or [])]
        exacts = {p.lower() for p in (args.book_exact or [])}
        books = [b for b in books
                 if b["stem"].lower() in exacts
                 or any(p in b["stem"].lower() for p in patterns)]
        with_clips = [b for b in books if b["yjr_count"] > 0]
        shown = ", ".join((args.book or []) + (args.book_exact or []))
        print(f"  --book 필터: {len(books)}권 매칭 / 클리핑 {len(with_clips)}권  "
              f"(패턴: {shown})")
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

    # KSDK 모드에서는 YJR 유무로 거르면 안 된다 — 사이드카가 고아화돼
    # yjr_count 가 0 이어도 KSDK DB 에는 클리핑이 있다. .sdr 폴더도 없을 수 있다.
    # (books 전체를 돌면 메타데이터 추출 비용이 들지만, ASIN 을 알아야
    #  매칭되므로 피할 수 없다.)
    pairs = [(b["kfx"], b["sdr"], b["stem"])
             for b in (books if (args.wifi or args.ksdk_db) else with_clips)]
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

    text_cache_dir = (None if args.no_text_cache
                      else Path(args.text_cache).expanduser())

    # ── 3b. KSDK 어노테이션 DB 확보 (WiFi 수신 또는 로컬 파일) ───────────
    #
    # 펌웨어 5.19.x 이후 어노테이션이 .yjr 대신 기기 내부 SQLite 로 간다.
    # 그 DB 를 받아 쓰면 사이드카가 고아화된 기기에서도 신규 클리핑을 얻는다.
    # 좌표계가 PRE-KL char offset 으로 YJR 과 같아 이후 처리는 동일하다.
    ksdk_by_asin: Optional[dict] = None
    if args.wifi or args.ksdk_db:
        from kindle.ksdk import parse_ksdk_db

        if args.ksdk_db:
            db_path = Path(args.ksdk_db).expanduser()
            if not db_path.exists():
                print(f"오류: KSDK DB 없음 — {db_path}", file=sys.stderr)
                return 1
        else:
            from kindle.wifi import WifiReceiver

            out_dir = Path(args.state).expanduser().parent / "ksdk_wifi"

            # 본문 캐시가 없는 책은 KFX 도 같이 받아 온다. 기기가
            # GET /wanted 로 이 목록을 가져가 해당 파일을 밀어 올린다.
            # 권당 수십 MB 라 --wifi-kfx 로 상한을 둔다 (전권을 한 번에
            # 요청하면 1GB 를 넘어 제한시간 안에 못 끝낸다).
            wanted = _wanted_stems(pairs, args, text_cache_dir)
            try:
                rx = WifiReceiver(out_dir, port=args.wifi_port, bind=args.wifi_bind,
                                  wanted=wanted,
                                  on_file=lambda n, sz, sha:
                                      print(f"  받음 {n}  {sz:,}B  sha1 {sha[:12]}", flush=True))
            except RuntimeError as e:
                print(f"오류: {e}", file=sys.stderr)
                return 1
            with rx:
                print("\nWiFi 수신 대기 중 …")
                print(f"  기기 스크립트의 BASE_URL: {rx.base_url}")
                if wanted:
                    print(f"  본문도 요청: {len(wanted)}권 — {', '.join(wanted[:3])}"
                          f"{' …' if len(wanted) > 3 else ''}")
                print(f"  기기 홈 화면 검색창에  ;log mrpi  를 입력하세요."
                      f"  (제한 {args.wifi_timeout}초)\n")
                result = rx.wait(args.wifi_timeout)
            if result.db_path is None:
                print("WiFi 수신 실패 — DB 를 받지 못했습니다.", file=sys.stderr)
                return 1
            db_path = result.db_path

            # 받은 KFX 가 있으면 그쪽을 본문 소스로 쓴다. 마운트는 내용을
            # 못 내주면서 stat 만 답하는 일이 있어 (2026-09-19) 신뢰하지 않는다.
            recv_kfx = {Path(n).stem: out_dir / n
                        for n in result.files if n.lower().endswith(".kfx")}
            if recv_kfx:
                pairs = [(recv_kfx.get(stem, kfx), sdr, stem)
                         for kfx, sdr, stem in pairs]
                print(f"  KFX {len(recv_kfx)}권 수신 — 본문은 수신본에서 추출합니다")

        ksdk_clips = parse_ksdk_db(db_path)
        ksdk_by_asin = {}
        for c in ksdk_clips:
            ksdk_by_asin.setdefault(c.book_title, []).append(c)   # 제목 채우기 전엔 ASIN
        print(f"KSDK DB: {db_path}")
        print(f"  클리핑 {len(ksdk_clips)}개 / 책 {len(ksdk_by_asin)}권")

    # ── 4. 책별 처리 (progress bar) ──────────────────────────────────────
    all_new: list[Clipping] = []
    all_new_fps: list[str]  = []
    book_stats: list[dict]  = []
    chapters_by_book: dict[str, list] = {}

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
            new_clips, new_fps, real_title, author, skipped, chapters = process_book(
                kfx, sdr, stem, effective_seen, pbar, title_cache=title_cache,
                ksdk_by_asin=ksdk_by_asin,
                text_cache_dir=text_cache_dir,
                refresh_text=args.refresh_text,
                allow_empty_text=args.allow_empty_text,
            )
            flush_book_log()   # 이 책의 누적 경고를 즉시 파일에 기록
            all_new.extend(new_clips)
            all_new_fps.extend(new_fps)
            if chapters and not args.no_chapter_outline:
                chapters_by_book[real_title] = chapters
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

    # 정렬은 클리핑과 fingerprint 를 반드시 함께 해야 한다.
    # all_new 만 정렬하면 all_new_fps 와 인덱스가 어긋나고,
    # sync_to_notion(clip_fps=…) 이 둘을 위치로 zip 하므로 엉뚱한
    # fingerprint 가 저장돼 dedup 이 통째로 깨진다.
    _paired = sorted(
        zip(all_new, all_new_fps),
        key=lambda p: (p[0].book_title, p[0].added_date or ""),
    )
    all_new     = [c for c, _ in _paired]
    all_new_fps = [fp for _, fp in _paired]

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

        # 2026-09-19 에 이 덮어쓰기가 783KB(29권 1,951건) 산출물을 32KB 로 날렸다.
        # 정상 종료한 실행이었다 — 그래서 성공/실패를 따지지 않고 무조건 뜬다.
        with backup.guard(out_path, "sync_output"):
            if fmt == "json":
                sync_export_json_grouped(all_new, out_path, meta,
                                         chapters_by_book=chapters_by_book)
            elif fmt == "csv":
                sync_export_csv(all_new, out_path)
            elif fmt == "markdown":
                sync_export_markdown(all_new, out_path, heading="킨들 KFX 클리핑",
                                     chapters_by_book=chapters_by_book)
            else:
                sync_export_text(all_new, out_path)
        print(f"저장: {out_path}")

    # ── 5a2. 장별 요약 (선택) ────────────────────────────────────────────
    # 요약은 클리핑 **뒤**에 붙는다 — Notion append 가 끝에만 붙으므로 이 배치는
    # 기존 페이지에도 rewrite 없이 그대로 추가된다.
    summaries_by_book: dict[str, str] = {}
    if args.summarize:
        from kindle.summarize import (DEFAULT_PATH as SUM_CACHE, SummarizerError,
                                      format_chapter_summaries, load_cache,
                                      save_cache, summarize_book)
        cache = load_cache(Path(args.summary_cache or SUM_CACHE))
        by_book: dict[str, list] = {}
        for c in all_new:
            by_book.setdefault(c.book_title, []).append(c)
        print(f"\n장별 요약 생성 중 … ({args.summary_model}, {len(by_book)}권)")
        for title, clips in by_book.items():
            try:
                pairs = summarize_book(
                    clips, title, cache=cache, model=args.summary_model,
                    progress=lambda i, n, ch: print(f"  [{title}] {i}/{n} {ch}",
                                                    flush=True),
                )
            except SummarizerError as e:
                print(f"  요약 실패 ({title}): {e}", file=sys.stderr)
                continue
            text = format_chapter_summaries(pairs, model=args.summary_model)
            if text:
                summaries_by_book[title] = text
        save_cache(Path(args.summary_cache or SUM_CACHE), cache)
        print(f"요약 완료: {len(summaries_by_book)}권")

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
            clip_fps=all_new_fps,   # PRE-KL fingerprint 전달 → synced_fingerprints도 PRE-KL 기준
            chapters_by_book=chapters_by_book,
            summaries_by_book=summaries_by_book,
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
    # 끄는 경로를 이 인자 하나로 좁힌다. 환경변수·설정파일로는 꺼지지 않는다 —
    # 조용히 꺼질 수 있으면 2026-09-19 같은 소실이 다시 난다 (BACKUP_DESIGN.md §4).
    parser.add_argument("--no-backup", action="store_true",
                        help="덮어쓰기 전 자동 백업을 끈다. 권장하지 않는다 — "
                             "2026-09-19 에 정상 종료한 실행이 산출물을 783KB→32KB 로 "
                             "덮어써 복구하지 못했다")
    parser.add_argument("--text-cache", default=str(DEFAULT_TEXT_CACHE), metavar="DIR",
                        help=f"KFX 본문·목차 캐시 디렉터리 (기본값: {DEFAULT_TEXT_CACHE}). "
                             "책당 한 번만 추출하고 이후에는 KFX 파일 없이도 본문을 채운다 "
                             "(권당 약 250KB)")
    parser.add_argument("--no-text-cache", action="store_true",
                        help="본문 캐시를 쓰지 않고 매번 KFX 에서 추출")
    parser.add_argument("--refresh-text", action="store_true",
                        help="본문 캐시를 무시하고 강제 재추출 (책을 다른 판본으로 교체했을 때)")
    parser.add_argument("--allow-empty-text", action="store_true",
                        help="본문 추출에 실패해도 그 책을 건너뛰지 않고 위치·시각만 올린다. "
                             "기본값은 건너뛰기 — 껍데기를 올리면 fingerprint 가 기록돼 "
                             "나중에 다시 채울 수 없다")
    parser.add_argument("--book", action="append", default=None, metavar="PATTERN",
                        help="특정 책만 처리 (stem 파일명 substring, 대소문자 무시). "
                             "여러 번 지정 가능: --book A --book B")
    parser.add_argument("--book-exact", action="append", default=None, metavar="STEM",
                        help="stem 전체가 일치하는 책만 처리 (대소문자 무시). "
                             "어떤 책의 stem 이 다른 책 stem 의 접두사일 때 "
                             "--book 이 여러 권을 잡는 문제를 피한다.")
    # Notion
    parser.add_argument("--notion-token", default=None, metavar="TOKEN",
                        help="Notion 통합 토큰 (NOTION_TOKEN 환경변수로도 설정 가능)")
    parser.add_argument("--notion-db", default=None, metavar="DB_ID",
                        help="업로드할 Notion 데이터베이스 ID (NOTION_DB 환경변수로도 설정 가능)")
    parser.add_argument("--notion-state", default=str(NOTION_DEFAULT_STATE), metavar="FILE",
                        help=f"Notion 상태 파일 경로 (기본값: {NOTION_DEFAULT_STATE})")
    parser.add_argument("--no-cover", action="store_true",
                        help="Notion 페이지에 책 표지 추가 안 함")
    # WiFi / KSDK (펌웨어 5.19.x 이후 사이드카 고아화 대응)
    parser.add_argument("--wifi", action="store_true",
                        help="킨들이 WiFi 로 밀어 올린 KSDK 어노테이션 DB 를 받아서 쓴다. "
                             "수신 URL 을 출력하고 기다린다 — 기기 검색창에 ';log mrpi' 입력. "
                             "USB·MacDroid 불필요 (단 KFX 본문 추출에는 마운트가 필요).")
    parser.add_argument("--wifi-port", type=int, default=8713, metavar="PORT",
                        help="WiFi 수신 포트 (기본값: 8713)")
    parser.add_argument("--wifi-bind", default=None, metavar="ADDR",
                        help="WiFi 수신 바인드 주소 (기본: 자동 감지한 LAN IP)")
    parser.add_argument("--wifi-kfx", type=int, default=0, metavar="N",
                        help="본문 캐시가 없는 책의 KFX 를 최대 N권까지 WiFi 로 함께 받는다 "
                             "(기본 0 = 받지 않음). 권당 수십 MB 라 크게 잡으면 "
                             "--wifi-timeout 도 함께 올려야 한다")
    parser.add_argument("--wifi-timeout", type=int, default=600, metavar="SEC",
                        help="WiFi 수신 제한시간 (기본값: 600초)")
    parser.add_argument("--ksdk-db", default=None, metavar="FILE",
                        help="이미 받아둔 ksdk_annotation_v1.db 를 쓴다 (수신 대기 없음)")

    # 장별 요약 (DeepSeek)
    parser.add_argument("--summarize", action="store_true",
                        help="장별 상세 요약을 생성해 클리핑 뒤에 붙인다 "
                             "(DEEPSEEK_API_KEY 필요). 장 안의 하이라이트를 입력으로 쓴다.")
    parser.add_argument("--summary-model", default="deepseek-chat", metavar="MODEL",
                        help="요약에 쓸 DeepSeek 모델 (기본값: deepseek-chat)")
    parser.add_argument("--summary-cache", default=None, metavar="FILE",
                        help="요약 캐시 경로 (기본값: ~/.kindle_summaries.json)")
    parser.add_argument("--no-chapter-outline", action="store_true",
                        help="클리핑 앞에 챕터별 페이지·Location 범위 목차를 넣지 않음 "
                             "(기본: KFX 목차가 있으면 넣음). Notion 에서는 페이지를 "
                             "새로 만들 때와 --rewrite-bodies 때만 반영된다.")
    args = parser.parse_args()

    # NOTION_TOKEN / NOTION_DB 환경변수 폴백 (.env 포함)
    import os
    if not args.notion_token:
        args.notion_token = os.environ.get("NOTION_TOKEN")
    if not args.notion_db:
        args.notion_db = os.environ.get("NOTION_DB")

    # 두 플래그는 2026-09-19 사고의 두 원인(덮어쓰기 / 껍데기 반입)에 각각
    # 대응하는 안전장치다. 둘 다 끄면 그날 상태로 정확히 돌아간다.
    if args.no_backup and args.allow_empty_text:
        parser.error(
            "--no-backup 과 --allow-empty-text 를 함께 쓸 수 없습니다. "
            "이 둘은 2026-09-19 소실 사고의 두 원인(덮어쓰기 보호 / 본문 없는 반입)에 "
            "각각 대응하는 안전장치라, 동시에 끄면 그날 상태가 그대로 재현됩니다. "
            "정말 필요하면 한 번에 하나씩 쓰십시오.")

    has_output = bool(args.output)
    has_notion = bool(args.notion_token and args.notion_db)
    if not args.dry_run and not args.list_books and not has_output and not has_notion:
        parser.error("--output, --notion-token+--notion-db, --dry-run, --list-books 중 하나가 필요합니다.")

    sys.exit(run_pipeline(args))


if __name__ == "__main__":
    main()
