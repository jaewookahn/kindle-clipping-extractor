"""KSDK DB 반입 staleness 방어 — 옛 사본을 새 것으로 착각해 조용히 반입하는 것을 막는다.

## 왜

`SYMLINK_STALENESS_REVIEW.md` §3 에서 실측으로 확인됐다 — 온디바이스 스크립트
(고정 파일명), WiFi 수신(sha1을 로그에만 찍고 저장 안 함), MTP 다운로드(원본
`modificationdate` 미보존), 반입(`--ksdk-db`, 경로·건수만 출력) 어디에도
"이 DB 가 언제 것인지" 기록·검사가 없었다. **로컬 파일 mtime 은 신뢰할 수
없다** — 전송 경로 어디도 원본 시각을 보존하지 않기 때문이다. 그래서 판단은
**DB 안의 내용**(각 클리핑의 `added_date` 중 최신값)과 파일 sha1 로만 한다.

## 왜 화면 표시만으로는 부족한가

2026-09-19 사고는 **정상 종료한 실행**에서 났다. 화면에 날짜가 찍혀 있어도
사람은 넘긴다. 그래서 지난 반입과 **기계적으로 비교**해 역행하면 경고한다 —
사람이 안 봐도 작동한다. `kindle/backup.py` 의 급감 경고와 같은 판단이다.

## 막지는 않는다

시각 역행이 항상 오류는 아니다 — Scribe 와 Colorsoft 를 번갈아 반입하면
정상적으로 역행한다. 그래서 **경고만 하고 중단하지 않는다.**

## 기기 구분은 못 한다

KSDK 클리핑에는 기기 정보가 없다 (`device_name` 은 `last_read` 전용,
하이라이트·북마크·노트는 0건 — `kindle/ksdk.py` 상단 참조). 그래서 "역행"이
"옛 사본"인지 "다른 기기"인지 이 모듈은 구분하지 못한다 — 경고 문구에 그
가능성을 그대로 적는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

__all__ = ["StalenessResult", "check"]


@dataclass(frozen=True)
class StalenessResult:
    """`warnings` 는 사람에게 보여줄 문구(0개 이상). `record` 는 다음 실행이
    비교할 수 있도록 상태 파일에 그대로 저장할 값이다."""
    warnings: List[str] = field(default_factory=list)
    record: dict = field(default_factory=dict)


def check(prev: Optional[dict], sha1: str, newest_added_date: Optional[str],
         seen_at: Optional[str] = None) -> StalenessResult:
    """이번에 반입한 DB(`sha1`, `newest_added_date`)를 지난 기록(`prev`)과 비교한다.

    `prev` 는 이전 호출이 돌려준 `record` 를 상태 파일에서 그대로 읽어 온 것 —
    없으면(`None`, 첫 실행이거나 `--reset` 직후) 경고 없이 새 기록만 만든다.
    """
    warnings: List[str] = []
    if prev:
        if prev.get("sha1") == sha1:
            warnings.append(
                f"지난 반입({prev.get('seen_at', '?')})과 파일이 동일합니다"
                f" (sha1 일치) — ;log mrpi 를 다시 안 돌렸을 수 있습니다."
            )
        elif (newest_added_date and prev.get("max_added_date")
              and newest_added_date < prev["max_added_date"]):
            warnings.append(
                "이 DB 의 최신 어노테이션이 지난 반입보다 오래됐습니다.\n"
                f"         지난번 {prev['max_added_date']}  →  이번 {newest_added_date}\n"
                "         옛 사본을 가리키고 있을 수 있습니다"
                " (또는 다른 기기 DB — 기기별로 구분하지 못합니다)."
                " ;log mrpi 를 다시 돌리셨습니까?"
            )
    record = {
        "sha1": sha1,
        "max_added_date": newest_added_date,
        "seen_at": seen_at or datetime.now().isoformat(timespec="seconds"),
    }
    return StalenessResult(warnings=warnings, record=record)
