#!/usr/bin/env python3
"""왕복 검증 — DB 통째 반입과 부분 추출(TSV) 반입이 같은 클리핑·같은 키를 내는가.

리딩총괄2 지시(2026-09-20): "이게 이 설계의 합격 기준입니다." 실제 로컬
회수본으로 `tools/ksdk_extract.sh`(시스템 `sqlite3` 사용) 를 돌려 TSV 를
만들고, `kindle.ksdk_export.parse_ksdk_export()` 로 읽은 결과를
`kindle.ksdk.parse_ksdk_db()` 가 같은 DB 를 직접 읽은 결과와 대조한다 —
① Clipping 필드가 완전히 같은지 ② `clip_key`(kindle.keys)가 완전히 같은
집합인지. 전체 추출과 증분 추출 둘 다 확인한다.

기기·네트워크 불필요 — `~/kindle_annotation_backup/` 의 로컬 사본만 쓴다.

사용법:
    python tools/verify_ksdk_export.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kindle import ksdk
from kindle import ksdk_export
from kindle import keys as K

DB_CANDIDATES = [
    Path.home() / "kindle_annotation_backup" / "live" / "ksdk_annotation_v1.db",
    Path.home() / "kindle_annotation_backup" / "ksdk-recovered-20260919-0331"
    / ".annotations" / "amzn1.account.AG4IK4ZSHJ4AVFVDXU4ZYLIGSAKA" / "ksdk_annotation_v1.db",
]
EXTRACT_SH = Path(__file__).resolve().parent / "ksdk_extract.sh"


def _run_extract(db: Path, since_ms: int = 0) -> Path:
    tmp = Path(tempfile.mkstemp(suffix=".tsv")[1])
    with tmp.open("w", encoding="utf-8") as f:
        subprocess.run(["sh", str(EXTRACT_SH), str(db), str(since_ms)],
                       check=True, stdout=f)
    return tmp


def _clip_sort_key(c):
    return (c.book_title, c.clip_type, c.location_start, c.location_end, c.content)


def _clip_key_for(c, asin_is_book_key: bool) -> str:
    # book_title == asin 인 상태(제목 캐시 없이 두 경로 다 호출)라 그대로 book_key 입력으로 쓴다.
    bk = K.book_key(asin=c.book_title) if asin_is_book_key else K.book_key(title=c.book_title)
    return K.clip_key(bk, c.clip_type, content=c.content,
                      loc_start=c.location_start) or "<None>"


def verify_one(db: Path) -> bool:
    print(f"\n=== {db} ===")
    if not db.exists():
        print("  [skip] 파일 없음")
        return True

    ok = True

    # ── 전체 추출 ────────────────────────────────────────────────────────
    direct = ksdk.parse_ksdk_db(db)
    tsv = _run_extract(db, since_ms=0)
    try:
        via_export = ksdk_export.parse_ksdk_export(tsv)
    finally:
        tsv.unlink(missing_ok=True)

    print(f"  DB 직접   : {len(direct)}건")
    print(f"  TSV 경유  : {len(via_export)}건")
    if len(direct) != len(via_export):
        print("  [FAIL] 건수 불일치")
        ok = False

    # source_file 은 당연히 다르다(DB 경로 vs TSV 경로) — 비교에서 뺀다.
    def _strip_source(c):
        return (c.book_title, c.clip_type, c.location_start, c.location_end,
               c.content, c.added_date)

    a = sorted((_strip_source(c) for c in direct), key=lambda t: (t[0], t[1], t[2] or 0))
    b = sorted((_strip_source(c) for c in via_export), key=lambda t: (t[0], t[1], t[2] or 0))
    if a != b:
        print(f"  [FAIL] 필드 불일치 — 처음 어긋난 지점 찾는 중 …")
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                print(f"    #{i}  direct={x}\n         export={y}")
                break
        ok = False
    else:
        print("  [OK] 필드 완전 일치")

    # ── clip_key 대조 (asin 을 book_key 로 그대로 쓴다 — 두 경로 동일 조건) ──
    keys_direct = {_clip_key_for(c, asin_is_book_key=True) for c in direct}
    keys_export = {_clip_key_for(c, asin_is_book_key=True) for c in via_export}
    if keys_direct != keys_export:
        print(f"  [FAIL] clip_key 집합 불일치 — direct only "
              f"{len(keys_direct - keys_export)}, export only {len(keys_export - keys_direct)}")
        ok = False
    else:
        print(f"  [OK] clip_key {len(keys_direct)}개 완전 일치")

    # ── 증분 추출 — 중간 시각 이후만 잘라서 부분집합인지 확인 ──────────────
    dated = [c for c in direct if c.added_date]
    if len(dated) >= 4:
        # since_ms 는 epoch-ms 인데 added_date 는 이미 로컬 문자열로 변환된
        # 뒤라 그 컷을 그대로 못 쓴다 — DB 를 직접 훑어 실제 epoch-ms 의
        # 중간값을 구한다("일부만 추출됐고 그게 전체의 부분집합인가"만
        # 확인한다 — 형식 대조는 전체 추출에서 이미 끝냈다).
        import sqlite3, json
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        created = sorted(
            int(json.loads(pl).get("created_time") or ct)
            for (ct, pl) in con.execute(
                "SELECT created_time, serialized_payload FROM nonsyncable_annotations "
                "WHERE dataset NOT IN (7,8,18)")
        )
        con.close()
        since_ms = created[len(created) // 2]

        tsv2 = _run_extract(db, since_ms=since_ms)
        try:
            incremental = ksdk_export.parse_ksdk_export(tsv2)
        finally:
            tsv2.unlink(missing_ok=True)

        keys_incremental = {_clip_key_for(c, asin_is_book_key=True) for c in incremental}
        expected_count = sum(1 for v in created if v > since_ms)
        print(f"  증분(since={since_ms}): {len(incremental)}건 "
              f"(기대 {expected_count}건, 전체의 {len(incremental)/len(direct)*100:.0f}%)")
        if len(incremental) != expected_count:
            print("  [FAIL] 증분 건수가 컷 기준과 안 맞음")
            ok = False
        elif not keys_incremental <= keys_direct:
            print("  [FAIL] 증분 clip_key 가 전체 집합의 부분집합이 아님")
            ok = False
        else:
            print("  [OK] 증분은 전체의 부분집합, 건수도 일치")
    else:
        print("  [skip] 증분 검증 — 표본이 너무 적음")

    return ok


def main() -> int:
    if not EXTRACT_SH.exists():
        print(f"오류: {EXTRACT_SH} 없음", file=sys.stderr)
        return 1
    results = [verify_one(db) for db in DB_CANDIDATES]
    all_ok = all(results)
    print(f"\n{'전부 통과' if all_ok else '실패 있음'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
