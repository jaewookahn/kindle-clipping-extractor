"""KSDKAnnotations DB 파서 — 펌웨어 5.19.x 이후의 새 어노테이션 저장소.

## 배경

2026년 어느 시점부터 킨들이 어노테이션을 `.sdr/*.yjr` 사이드카가 아니라
기기 내부 SQLite DB 로 쓰기 시작했다 (웨브랩 `KSDKANNNOTATIONS_1379504` = T1,
`OnDeviceMigrationController` 가 `MIGRATION_COMPLETED` 로 보고). 그 시점부터
`.yjr` 과 `My Clippings.txt` 는 **고아화**된다 — 읽히기는 하지만 새 어노테이션이
들어오지 않는다. 경위는 `KINDLE_ANNOTATION_OUTAGE.md` 참조.

    /mnt/us/system/ksdk/.annotations/<amzn1.account.…>/ksdk_annotation_v1.db

이 경로는 USB(MTP)로 노출되지 않는다. 탈옥한 기기에서 꺼내와야 한다.

## 테이블

`nonsyncable_annotations` 가 사이드로드(PDOC) 어노테이션을 담는다. 이름의
"nonsyncable" 은 **Amazon 클라우드로 동기화되지 않는다**는 뜻이지 쓸 수 없다는
뜻이 아니다.

    annotation_id / book_id / dataset / start_position / end_position
    created_time / modified_time / serialized_payload

## ⚠️ `asin` 은 대부분 ASIN 이 아니다 (이름이 오해를 부른다)

이 모듈이 `asin` 이라 부르는 값은 `payload["book_data"]["asin"]` 이다. 그런데
이 장서는 **98.5%가 사이드로드(PDOC)** 라, 그 자리에 든 것은 아마존 ASIN 이
아니라 **기기가 만든 32자 내부 ID** 다. 실측:

    PDOC (사이드로드)  140권 / 3,304건   ← asin 자리 = 기기 내부 ID
    EBOK (아마존 구매)    7권 /    49건   ← 이 중 3건은 Vera·Font_Calibration
                                           같은 시스템 항목
    진짜 ASIN 을 가진 책 = 4권

책을 가르는 **안정적인 키로는 그대로 써도 된다** (같은 책이면 같은 값이다).
다만 이것을 아마존 ASIN 으로 취급해 외부 조회(표지·메타데이터)에 넘기거나,
종이책 ISBN 과 잇는 별칭 키로 쓰면 안 된다. 기기 간 동일성도 미검증이다
(Scribe DB 를 확보해야 확인된다).

`book_id` 컬럼(`ASIN-contentType-guid` 형태)은 **문자열로 파싱하지 말 것.**
하이픈 개수가 3개/7개로 갈리고 id 자체가 UUID 인 경우가 있어 첫 `-` 로 자르면
깨진다. payload 의 `book_data` 에 이미 분리돼 있으므로 그쪽을 쓴다.

## 좌표계 — 중요

`start_position` 의 `shortPosition` 은 **KFX char offset (PRE-KL)** 이다.
Kindle Location 이 아니다. `parse_yjr()` 이 내놓는 값과 같은 좌표계라서
`fill_clipping_text()` 이하 기존 파이프라인을 그대로 쓸 수 있고, fingerprint 도
기존 PRE-KL 체계와 호환된다 (이미 동기화한 항목이 중복되지 않는다).

실측 검증: `먼저 온 미래` 의 DB 249건과 `.yjr` 92건을 대조한 결과 교집합 92,
YJR 전용 0, DB 전용 157건이 `.yjr` 최대 offset(106,804) **직후**인 113,365 부터
시작했다.

`longPosition` 은 9바이트 base64로 `0x01 | eid(LE u32) | eid_offset(LE u32)` —
KFX `$246.{$155, $143}` 과 같은 쌍이다. 현재 파이프라인은 char offset 만 쓰므로
파싱해 두기만 하고 사용하지는 않는다.
"""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from kindle.models import Clipping

__all__ = [
    "DEFAULT_DB_NAME",
    "KSDKError",
    "decode_long_position",
    "iter_annotations",
    "parse_ksdk_db",
    "group_by_asin",
]

DEFAULT_DB_NAME = "ksdk_annotation_v1.db"

logger = logging.getLogger(__name__)

# payload 의 type → Clipping.clip_type.
# dataset 코드로도 구분되지만 type 필드가 명시적이라 그쪽을 신뢰한다
# (dataset 7=waypoint·8=last_read 는 type 이 아예 없다).
_TYPE_MAP = {
    "HIGHLIGHT": "highlight",
    "BOOKMARK":  "bookmark",
    "NOTE":      "note",
}

# 읽기 위치·인기 하이라이트 등 클리핑이 아닌 dataset.
#   7  = waypoint          (type 없음)
#   8  = last_read         (type 없음)
#   18 = POPULARHIGHLIGHT  (내 하이라이트가 아니라 다른 독자 통계)
_SKIP_DATASETS = frozenset({7, 8, 18})


class KSDKError(RuntimeError):
    pass


def decode_long_position(b64: str) -> Optional[Tuple[int, int]]:
    """`longPosition` → `(eid, eid_offset)`. 형식이 다르면 None."""
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return None
    if len(raw) < 9 or raw[0] != 0x01:
        return None
    eid, off = struct.unpack("<II", raw[1:9])
    return eid, off


def _short_pos(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    try:
        v = json.loads(raw).get("shortPosition")
    except (json.JSONDecodeError, AttributeError):
        return None
    return v if isinstance(v, int) else None


def _meta(payload: dict) -> dict:
    """`json_metadata` 는 **문자열 안에 든 JSON** 이다 (이중 인코딩)."""
    raw = payload.get("json_metadata")
    if not raw:
        return {}
    if isinstance(raw, dict):      # 방어적 — 관측된 적은 없다
        return raw
    try:
        out = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return out if isinstance(out, dict) else {}


def _fmt_time(ms: Optional[int]) -> Optional[str]:
    """ms epoch → 'YYYY-MM-DD HH:MM:SS' (로컬 시각).

    `parsers/yjr.py` 의 `_yjr_timestamp()` 와 **같은 방식**으로 쓴다 — UTC 로
    해석한 뒤 로컬로 변환. 두 소스의 날짜 표기가 갈리면 dedup·정렬이 흔들린다.
    상한(4e12ms ≈ 2096년)도 같은 이유로 맞춘다.
    """
    if not isinstance(ms, int) or ms <= 0 or ms > 4_000_000_000_000:
        return None
    try:
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return None


def iter_annotations(db_path: Path) -> Iterable[dict]:
    """DB 행을 dict 로 흘려보낸다. 클리핑 여부 판단은 하지 않는다."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise KSDKError(f"KSDK DB 없음: {db_path}")
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as e:
        raise KSDKError(f"KSDK DB 열기 실패: {e}") from e
    try:
        con.row_factory = sqlite3.Row
        cur = con.execute(
            "SELECT annotation_id, book_id, dataset, start_position, end_position,"
            "       created_time, modified_time, serialized_payload"
            "  FROM nonsyncable_annotations"
        )
        for row in cur:
            yield dict(row)
    except sqlite3.Error as e:
        raise KSDKError(f"nonsyncable_annotations 읽기 실패: {e}") from e
    finally:
        con.close()


def parse_ksdk_db(db_path: Path,
                  asin: Optional[str] = None,
                  book_title: str = "") -> List[Clipping]:
    """KSDK DB → `Clipping` 목록.

    asin: 주면 그 책만. 없으면 전체. (이름과 달리 사이드로드 책에서는 기기
          내부 ID 다 — 모듈 상단 경고 참조. 키로는 안정적이지만 아마존 ASIN
          으로 취급하면 안 된다.)
    book_title: `Clipping.book_title` 에 넣을 값. 비어 있으면 ASIN 을 쓴다
                (호출부가 제목 캐시로 채우는 편이 낫다).

    `location_start/end` 는 **KFX char offset(PRE-KL)** 이다 —
    `parse_yjr()` 과 같은 좌표계이므로 이후 fill_* 파이프라인을 그대로 태운다.
    """
    out: List[Clipping] = []
    for row in iter_annotations(db_path):
        if row["dataset"] in _SKIP_DATASETS:
            continue
        try:
            payload = json.loads(row["serialized_payload"])
        except (json.JSONDecodeError, TypeError):
            logger.warning("KSDK payload 파싱 실패: %s", row.get("annotation_id"))
            continue

        clip_type = _TYPE_MAP.get(payload.get("type", ""))
        if clip_type is None:
            continue

        book = payload.get("book_data") or {}
        row_asin = book.get("asin") or ""
        if asin and row_asin != asin:
            continue

        start = _short_pos(row["start_position"])
        end = _short_pos(row["end_position"])
        meta = _meta(payload)

        # 본문은 여기서 채우지 않는다. char offset 만 싣고
        # fill_clipping_text() 가 KFX 원문을 잘라 넣는다 — YJR 경로와 동일.
        content = meta.get("note_text", "") if clip_type == "note" else ""
        color = meta.get("mchl_color")
        if color and clip_type != "note":
            content = f"[{color}] "

        # 북마크는 end 를 비워 둔다. KSDK 는 end==start 로 채워 주지만
        # `parse_yjr()` 은 None 으로 남기고, fingerprint 가
        # `title|type|start|end` 라서 여기서 어긋나면 **이미 동기화한 북마크가
        # 전부 신규로 오인된다**. 실측: 이 처리를 빼면 먼저 온 미래에서
        # YJR 93건 중 북마크 12건이 교집합에서 누락됐다.
        if clip_type == "bookmark":
            end = None
        elif end is None:
            end = start

        out.append(Clipping(
            book_title=book_title or row_asin,
            clip_type=clip_type,
            location_start=start,
            location_end=end,
            added_date=_fmt_time(payload.get("created_time") or row["created_time"]),
            content=content,
            source_file=str(db_path),
        ))
    out.sort(key=lambda c: (c.location_start is None, c.location_start or 0))
    return out


def group_by_asin(db_path: Path) -> Dict[str, int]:
    """ASIN → 클리핑 수. 어느 책이 들어 있는지 훑어볼 때 쓴다."""
    counts: Dict[str, int] = {}
    for row in iter_annotations(db_path):
        if row["dataset"] in _SKIP_DATASETS:
            continue
        try:
            payload = json.loads(row["serialized_payload"])
        except (json.JSONDecodeError, TypeError):
            continue
        if payload.get("type") not in _TYPE_MAP:
            continue
        a = (payload.get("book_data") or {}).get("asin") or "(unknown)"
        counts[a] = counts.get(a, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
