#!/usr/bin/env python3
"""
sync_clippings.py — 마운트된 킨들에서 신규 클리핑 자동 동기화 파이프라인

파이프라인:
  1. 킨들 마운트 자동 감지  (find_kindle)
  2. My Clippings.txt 파싱
  3. 상태 파일과 비교 → 신규 항목만 추출  (fingerprint 기반 중복 제거)
  4. 책별로 KFX 파일 탐색 → 한도 초과 텍스트 자동 복구  (recover_clippings 동일 로직)
  5. 신규 클리핑 저장
  6. 상태 파일 업데이트

사용법:
    python sync_clippings.py -o new_clippings.json
    python sync_clippings.py -o ~/notes/kindle.md --state ~/.kindle_sync.json
    python sync_clippings.py --dry-run            # 저장 없이 신규 목록만 출력
    python sync_clippings.py --reset              # 상태 초기화 후 전체 재동기화
    python sync_clippings.py --kindle /Volumes/Kindle -o out.json  # 경로 직접 지정

상태 파일 (기본값: ~/.kindle_sync.json):
    {
      "last_sync":    "2024-01-15T10:30:00",
      "total_synced": 142,
      "seen_keys":    ["<fingerprint>", ...]
    }
"""

import argparse
import csv
import hashlib
import json
import re
import sys
import tempfile
import zipfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set, Dict

# kindle 패키지에서 마운트 감지·파싱 재사용
from kindle.device import find_kindle, list_device_clippings_file
from kindle.parsers.my_clippings import parse_my_clippings as _parse
from kindle.models import Clipping


# ---------------------------------------------------------------------------
# 상태 파일 관리
# ---------------------------------------------------------------------------

DEFAULT_STATE = Path.home() / ".kindle_sync.json"


def load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_sync": None, "total_synced": 0, "seen_keys": []}


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _fingerprint(c: Clipping) -> str:
    """클리핑의 고유 식별자 (책·위치·날짜 조합 SHA-1)."""
    key = f"{c.book_title}|{c.location_start}|{c.location_end}|{c.added_date}"
    return hashlib.sha1(key.encode()).hexdigest()


# ---------------------------------------------------------------------------
# 킨들 디바이스에서 KFX 파일 탐색
# ---------------------------------------------------------------------------

_KFX_EXTS = (".kfx", ".azw3", ".azw", ".mobi")


def find_kfx_on_device(kindle_root: Path, book_title: str) -> Optional[Path]:
    """documents/ 아래에서 책 제목과 일치하는 KFX(또는 AZW3 등) 파일 탐색."""
    docs = kindle_root / "documents"
    if not docs.exists():
        return None
    for ext in _KFX_EXTS:
        candidate = docs / (book_title + ext)
        if candidate.exists():
            return candidate
    # 퍼지 탐색: 파일명이 제목을 포함하는 경우
    title_lower = book_title.lower()
    for f in docs.rglob("*"):
        if f.suffix.lower() in _KFX_EXTS and title_lower in f.stem.lower():
            return f
    return None


# ---------------------------------------------------------------------------
# KFX 텍스트 추출 + Kindle Location 맵  (recover_clippings.py 동일 로직)
# ---------------------------------------------------------------------------

_KFX_PLUGIN_PATHS = [
    "~/Library/Preferences/calibre/plugins/KFX Input.zip",
    "~/.config/calibre/plugins/KFX Input.zip",
]

_LIMIT_PREFIXES = (
    "<you have reached the clipping limit",
    "<the clipping limit",
    "<이 항목의 클리핑 한도",
)


def _find_kfx_plugin() -> Optional[str]:
    for p in _KFX_PLUGIN_PATHS:
        expanded = Path(p).expanduser()
        if expanded.exists():
            return str(expanded)
    return None


def _is_limit_exceeded(content: str) -> bool:
    if not content or not content.strip():
        return True
    return content.strip().lower().startswith(_LIMIT_PREFIXES)


def _extract_kfx_data(kfx_path: Path):
    """(kindle_loc_offsets, book_text) 반환. 실패 시 (None, None)."""
    plugin_zip = _find_kfx_plugin()
    if not plugin_zip:
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

        loc_info = book.collect_location_map_info(pos_info)
        kl_offsets: Optional[List[int]] = (
            [entry.pid for entry in loc_info] if loc_info else None
        )

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
            print(f"    [경고] 텍스트 추출 실패: {exc}", file=sys.stderr)

        return kl_offsets, book_text

    except Exception as exc:
        print(f"    [경고] KFX 파싱 실패: {exc}", file=sys.stderr)
        return None, None
    finally:
        if tmpdir:
            import shutil
            shutil.rmtree(str(tmpdir), ignore_errors=True)


def _recover_text(clippings: List[Clipping], kl_offsets: List[int], book_text: str) -> int:
    """한도 초과 클리핑의 텍스트를 KFX 원문에서 복구. 복구 수 반환."""
    recovered = 0
    n = len(kl_offsets)
    for c in clippings:
        if c.clip_type not in ("highlight", "bookmark"):
            continue
        if not _is_limit_exceeded(c.content):
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
        snippet = book_text[char_start : min(char_end, len(book_text))].strip()
        snippet = re.sub(r"\s+", " ", snippet)
        if snippet:
            c.content  = snippet
            recovered += 1
    return recovered


# ---------------------------------------------------------------------------
# 책별 KFX 복구 파이프라인
# ---------------------------------------------------------------------------

def recover_by_book(
    new_clippings: List[Clipping],
    kindle_root: Path,
    verbose: bool = True,
) -> int:
    """책 제목별로 그룹화하여 KFX 복구를 시도. 총 복구 수 반환."""
    # 책별 그룹화
    groups: Dict[str, List[Clipping]] = {}
    for c in new_clippings:
        groups.setdefault(c.book_title, []).append(c)

    total_recovered = 0
    for title, group in groups.items():
        # 한도 초과 항목이 있는 책만 처리
        needs_recovery = [c for c in group if _is_limit_exceeded(c.content)
                          and c.clip_type in ("highlight", "bookmark")]
        if not needs_recovery:
            continue

        kfx = find_kfx_on_device(kindle_root, title)
        if not kfx:
            if verbose:
                print(f"  [{title[:40]}] KFX 없음 — {len(needs_recovery)}개 항목 복구 불가")
            continue

        if verbose:
            print(f"  [{title[:40]}] KFX 발견: {kfx.name}")
            print(f"    → {len(needs_recovery)}개 한도 초과 항목 복구 시도 …")

        kl_offsets, book_text = _extract_kfx_data(kfx)
        if not kl_offsets or not book_text:
            if verbose:
                print(f"    → 추출 실패", file=sys.stderr)
            continue

        n = _recover_text(group, kl_offsets, book_text)
        total_recovered += n
        if verbose:
            print(f"    → {n}/{len(needs_recovery)}개 복구 완료")

    return total_recovered


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------

def _to_dict(c: Clipping) -> dict:
    d = asdict(c)
    return {k: v for k, v in d.items() if v is not None and v != ""}


def export_json(clippings: List[Clipping], out: Path, meta: dict) -> None:
    payload = {
        **meta,
        "clippings": [_to_dict(c) for c in clippings],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def export_csv(clippings: List[Clipping], out: Path) -> None:
    fields = ["book_title", "author", "clip_type", "page",
              "location_start", "location_end", "added_date", "content"]
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for c in clippings:
            w.writerow({k: getattr(c, k, "") for k in fields})


def export_markdown(clippings: List[Clipping], out: Path) -> None:
    lines: List[str] = [f"# 킨들 클리핑 동기화  ·  {datetime.now():%Y-%m-%d %H:%M}\n"]
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
        lines.append(f"*{c.clip_type} · {loc}*\n")
        lines.append(f"> {c.content}\n")
    out.write_text("\n".join(lines), encoding="utf-8")


def export_text(clippings: List[Clipping], out: Path) -> None:
    lines: List[str] = []
    for c in clippings:
        loc = f"Location {c.location_start}"
        if c.location_end and c.location_end != c.location_start:
            loc += f"–{c.location_end}"
        lines.append(f"[{c.book_title}]  {loc}")
        lines.append(c.content or "(내용 없음)")
        lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# 메인 파이프라인
# ---------------------------------------------------------------------------

def run_pipeline(args) -> int:
    # ── 1. 킨들 마운트 감지 ──────────────────────────────────────────────
    if args.kindle:
        kindle_root = Path(args.kindle)
        if not kindle_root.exists():
            print(f"오류: 지정한 경로를 찾을 수 없습니다: {kindle_root}", file=sys.stderr)
            return 1
        print(f"킨들 경로 지정: {kindle_root}")
    else:
        print("킨들 마운트 탐색 중 …")
        kindle_root = find_kindle()
        if not kindle_root:
            print("오류: 마운트된 킨들 디바이스를 찾을 수 없습니다.", file=sys.stderr)
            print("  USB 연결 및 마운트 상태를 확인하거나 --kindle 경로를 직접 지정하세요.",
                  file=sys.stderr)
            return 1
        print(f"킨들 감지: {kindle_root}")

    # ── 2. My Clippings.txt 파싱 ─────────────────────────────────────────
    clippings_file = list_device_clippings_file(kindle_root)
    if not clippings_file:
        print("오류: My Clippings.txt를 찾을 수 없습니다.", file=sys.stderr)
        return 1

    print(f"파싱: {clippings_file}")
    all_clippings = _parse(clippings_file)
    print(f"  총 {len(all_clippings)}개 클리핑")

    # ── 3. 상태 파일 로드 · 신규 항목 추출 ──────────────────────────────
    state_path = Path(args.state)

    if args.reset:
        state = {"last_sync": None, "total_synced": 0, "seen_keys": []}
        print("상태 초기화 — 전체 재동기화")
    else:
        state = load_state(state_path)

    seen_keys: Set[str] = set(state.get("seen_keys", []))

    new_clippings = [c for c in all_clippings if _fingerprint(c) not in seen_keys]
    print(f"  신규 항목: {len(new_clippings)}개  (이전 동기화: {state.get('total_synced', 0)}개)")

    if not new_clippings:
        print("신규 클리핑이 없습니다.")
        return 0

    # ── 4. KFX 복구 ──────────────────────────────────────────────────────
    if not args.no_recover:
        plugin = _find_kfx_plugin()
        if plugin:
            print("\nKFX 텍스트 복구 시작 …")
            total_recovered = recover_by_book(new_clippings, kindle_root, verbose=True)
            print(f"복구 완료: 총 {total_recovered}개")
        else:
            print("\n[정보] KFX Input 플러그인 없음 — 텍스트 복구 건너뜀")
            print("  설치: Calibre → 환경설정 → 플러그인 → 'KFX Input' 검색")

    # ── 5. 출력 ──────────────────────────────────────────────────────────
    if args.dry_run:
        print("\n[dry-run] 저장 생략. 신규 항목:")
        for c in new_clippings:
            loc = f"L{c.location_start}" + (f"-{c.location_end}" if c.location_end else "")
            flag = " *" if _is_limit_exceeded(c.content) else ""
            print(f"  {c.book_title[:45]:<45}  {loc:<12}  {c.content[:60]}{flag}")
        return 0

    out_path = Path(args.output)
    fmt = args.format
    if fmt is None:
        ext_map = {".json": "json", ".csv": "csv", ".md": "markdown", ".txt": "text"}
        fmt = ext_map.get(out_path.suffix.lower(), "json")

    new_clippings.sort(key=lambda c: (c.book_title, c.added_date or ""))

    meta = {
        "synced_at": datetime.now().isoformat(),
        "kindle_path": str(kindle_root),
        "new_count": len(new_clippings),
    }

    if fmt == "json":
        export_json(new_clippings, out_path, meta)
    elif fmt == "csv":
        export_csv(new_clippings, out_path)
    elif fmt == "markdown":
        export_markdown(new_clippings, out_path)
    else:
        export_text(new_clippings, out_path)

    print(f"\n저장: {out_path}  ({len(new_clippings)}개)")

    # ── 6. 상태 업데이트 ──────────────────────────────────────────────────
    for c in new_clippings:
        seen_keys.add(_fingerprint(c))

    state["last_sync"]    = datetime.now().isoformat()
    state["total_synced"] = state.get("total_synced", 0) + len(new_clippings)
    state["seen_keys"]    = sorted(seen_keys)

    save_state(state_path, state)
    print(f"상태 저장: {state_path}  (누적 {state['total_synced']}개)")

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="마운트된 킨들에서 신규 클리핑 자동 동기화",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--kindle", default=None, metavar="PATH",
        help="킨들 마운트 경로 (생략 시 자동 감지)",
    )
    parser.add_argument(
        "-o", "--output", default=None, metavar="FILE",
        help="출력 파일 경로 (--dry-run 없이는 필수)",
    )
    parser.add_argument(
        "-f", "--format", choices=["json", "csv", "markdown", "text"], default=None,
        help="출력 형식 (기본값: 확장자에서 추론)",
    )
    parser.add_argument(
        "--state", default=str(DEFAULT_STATE), metavar="FILE",
        help=f"상태 파일 경로 (기본값: {DEFAULT_STATE})",
    )
    parser.add_argument(
        "--no-recover", action="store_true",
        help="KFX 텍스트 복구 건너뜀",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="신규 항목 목록만 출력, 파일 저장 및 상태 업데이트 없음",
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="상태 파일 초기화 후 전체 재동기화",
    )
    args = parser.parse_args()

    if not args.dry_run and not args.output:
        parser.error("--output 또는 --dry-run 중 하나가 필요합니다.")

    sys.exit(run_pipeline(args))


if __name__ == "__main__":
    main()
