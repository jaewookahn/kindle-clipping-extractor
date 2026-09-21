"""KSDK 부분 추출(TSV) 파서 — `tools/ksdk_extract.sh` 산출물을 `Clipping` 으로.

## 왜

DB 를 통째로 옮기지 않고 좌표·색상·시각만 뽑아 오는 경로다
(`ON_DEVICE_EXTRACT_REVIEW.md` 참조). 실측: 전체 2,977건이 232,909B —
DB 원본(3.68MB)의 6.3%. 온디바이스 스크립트는 `sqlite3` 의 JSON1 함수
(`json_extract`)만으로 이 필드들을 뽑는다 — 커스텀 파서를 새로 짤 필요가
없었다(이중 인코딩된 `json_metadata` 도 `json_extract` 를 중첩하면 풀린다,
로컬 실측 확인).

## `kindle/ksdk.py` 와 로직을 공유한다 — 갈라지면 안 된다

같은 DB 를 어느 경로로 읽었든(통째 반입 `--ksdk-db` vs 부분 추출
`--ksdk-export`) **완전히 같은 `Clipping`, 완전히 같은 `clip_key` 가
나와야 한다.** 그래서 타입 매핑(`_TYPE_MAP`)과 시각 포맷(`_fmt_time`)을
`kindle.ksdk` 에서 그대로 가져다 쓴다 — 따로 구현하면 두 경로가 조용히
갈라질 수 있다. 북마크의 `location_end` 를 `None` 으로 정규화하는 규칙,
색상 접두사 조립 규칙도 `parse_ksdk_db()` 와 한 글자도 다르지 않게
맞췄다 — `tools/verify_ksdk_export.py` 가 실제 DB 로 이 동등성을 검증한다.

## TSV 스키마 (헤더 없음, 탭 구분)

    type  asin  loc_start  loc_end  created_ms  color  note_text

`tools/ksdk_extract.sh` 가 만드는 그대로다. 필드가 비어 있으면 빈 문자열
(`""`) — `"None"`/`"null"` 아니다 (탭으로 구분되는 빈 칸).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Optional

from kindle.ksdk import KSDKError, _TYPE_MAP, _fmt_time
from kindle.models import Clipping

__all__ = ["parse_ksdk_export"]

logger = logging.getLogger(__name__)

_EXPECTED_FIELDS = 7   # type, asin, loc_start, loc_end, created_ms, color, note_text

# tools/ksdk_extract.sh 가 color·note_text 안의 탭·개행·백슬래시를
# \t·\n·\\ 로 이스케이프해 보낸다(TSV 컬럼 구분이 깨지지 않도록). 한 번에
# 되돌린다 — 두 단계로 나누면 이스케이프가 만든 문자를 다시 이스케이프
# 대상으로 오인할 수 있다(예: \\n 을 순서대로 풀면 실제 백슬래시+n 이 아니라
# 개행이 돼 버린다).
_UNESCAPE_MAP = {"n": "\n", "t": "\t", "\\": "\\"}
_UNESCAPE_RE = re.compile(r"\\(.)")


def _unescape(s: str) -> str:
    return _UNESCAPE_RE.sub(lambda m: _UNESCAPE_MAP.get(m.group(1), m.group(0)), s)


def _int_or_none(s: str) -> Optional[int]:
    s = s.strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def parse_ksdk_export(path: Path,
                      asin: Optional[str] = None,
                      book_title: str = "") -> List[Clipping]:
    """`tools/ksdk_extract.sh` 가 만든 TSV → `Clipping` 목록.

    인자·반환값의 의미는 `kindle.ksdk.parse_ksdk_db()` 와 동일하다 —
    호출부(`sync_kfx.py`)가 두 경로를 같은 방식으로 다룰 수 있게 맞췄다.
    """
    path = Path(path)
    if not path.exists():
        raise KSDKError(f"KSDK 추출 파일 없음: {path}")

    out: List[Clipping] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != _EXPECTED_FIELDS:
            logger.warning("KSDK 추출 파일 %s:%d 필드 수 불일치(%d) — 건너뜀",
                           path, lineno, len(fields))
            continue
        raw_type, row_asin, raw_start, raw_end, raw_created, raw_color, raw_note = fields
        color = _unescape(raw_color)
        note = _unescape(raw_note)

        clip_type = _TYPE_MAP.get(raw_type.strip())
        if clip_type is None:
            continue     # tools/ksdk_extract.sh 가 이미 걸렀어야 하지만 방어적으로

        if asin and row_asin != asin:
            continue

        start = _int_or_none(raw_start)
        end = _int_or_none(raw_end)

        # kindle/ksdk.py::parse_ksdk_db() 와 한 글자도 다르지 않게 맞춘다 —
        # 본문은 여기서 채우지 않는다(fill_clipping_text 가 KFX 원문을 채운다).
        content = note if clip_type == "note" else ""
        if color and clip_type != "note":
            content = f"[{color}] "

        # 북마크는 end 를 비워 둔다. shortPosition 이 end==start 로 나올 수
        # 있는데, parse_yjr() 은 None 을 두고 fingerprint/clip_key 가
        # location_end 에 민감하다 — kindle/ksdk.py 상단 주석·회귀 이력 참조.
        if clip_type == "bookmark":
            end = None
        elif end is None:
            end = start

        out.append(Clipping(
            book_title=book_title or row_asin,
            clip_type=clip_type,
            location_start=start,
            location_end=end,
            added_date=_fmt_time(_int_or_none(raw_created)),
            content=content,
            source_file=str(path),
        ))

    out.sort(key=lambda c: (c.location_start is None, c.location_start or 0))
    return out
