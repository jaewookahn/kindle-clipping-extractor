#!/bin/sh
##
# KSDK 어노테이션 부분 추출 — DB 통째가 아니라 좌표·색상·시각만 TSV 로 뽑는다.
#
# 배경: ON_DEVICE_EXTRACT_REVIEW.md ①②③. DB 통째 반출(수 MB)이 아니라
# 필요한 필드만 뽑으면 실측 355KB(gzip 46.8KB, 2,977건) — 증분이면 더 작다.
#
# ⚠️ 아직 기기에 올리지 않았다. 정적 링크 sqlite3 를 기기에 준비하는 절차는
# tools/build_static_sqlite3_arm.sh. 이 스크립트는 그 sqlite3 (또는 로컬
# 검증 때는 시스템 sqlite3) 를 그대로 부른다 — 커스텀 파서를 새로 짜지
# 않는다. SQLite 의 JSON1 함수(`json_extract`)로 이중 인코딩된
# `json_metadata` 까지 SQL 만으로 뽑을 수 있다는 것을 로컬에서 실측 확인했다.
#
# 사용법:
#   ksdk_extract.sh <db_path> [since_epoch_ms]
#   since_epoch_ms 를 생략하면 전체, 주면 그 이후(created_time > 값)만.
#
# 출력(stdout, TSV, 헤더 없음 — kindle/ksdk_export.py 와 스키마를 맞춘다):
#   type\tasin\tloc_start\tloc_end\tcreated_ms\tcolor\tnote_text
#
# dataset 7(waypoint)·8(last_read)·18(POPULARHIGHLIGHT) 는 클리핑이 아니다
# (kindle/ksdk.py 의 _SKIP_DATASETS 와 동일 기준) — SQL 에서 미리 뺀다.
#
# color·note_text 안의 탭·개행·백슬래시는 `\t`·`\n`·`\\` 로 이스케이프한다
# (역순 적용 — 백슬래시부터). 그냥 공백으로 뭉개면 여러 줄 메모의 원문이
# 손실된다(실측: "Really?\npremodern?" 이 "Really? premodern?" 이 돼
# 왕복 검증에서 필드 불일치로 잡혔다). `kindle/ksdk_export.py` 가 되돌린다.
##

set -eu

DB="${1:?사용법: ksdk_extract.sh <db_path> [since_epoch_ms]}"
SINCE="${2:-0}"

# sqlite3 가 PATH 에 없으면(기기에 정적 빌드를 아직 안 올린 상태) 여기서
# 바로 실패한다 — 조용히 빈 출력을 내지 않는다.
if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "ksdk_extract.sh: sqlite3 없음 (PATH: $PATH)" >&2
  exit 1
fi

# 이스케이프는 순서가 중요하다 — 백슬래시부터 두 배로 만든 다음에
# 개행·탭을 \n·\t 로 바꿔야, 이스케이프가 만든 백슬래시 자체가 다시
# 이스케이프되는 일이 없다.
ESC="REPLACE(REPLACE(REPLACE(COALESCE(%s, ''), CHAR(92), CHAR(92)||CHAR(92)), CHAR(10), CHAR(92)||'n'), CHAR(9), CHAR(92)||'t')"
COLOR_EXPR=$(printf "$ESC" "json_extract(json_extract(serialized_payload,'\$.json_metadata'),'\$.mchl_color')")
NOTE_EXPR=$(printf "$ESC" "json_extract(json_extract(serialized_payload,'\$.json_metadata'),'\$.note_text')")

sqlite3 -readonly -noheader -separator "$(printf '\t')" "$DB" "
SELECT
  json_extract(serialized_payload,'\$.type'),
  COALESCE(json_extract(serialized_payload,'\$.book_data.asin'), ''),
  json_extract(serialized_payload,'\$.start_position.shortPosition'),
  json_extract(serialized_payload,'\$.end_position.shortPosition'),
  COALESCE(json_extract(serialized_payload,'\$.created_time'), created_time),
  $COLOR_EXPR,
  $NOTE_EXPR
FROM nonsyncable_annotations
WHERE dataset NOT IN (7,8,18)
  AND COALESCE(json_extract(serialized_payload,'\$.created_time'), created_time) > $SINCE
ORDER BY created_time;
"
