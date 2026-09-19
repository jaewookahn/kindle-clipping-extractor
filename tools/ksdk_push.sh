#!/bin/sh
##
# KSDK 어노테이션 DB 추출 — 킨들 기기에서 실행된다.
#
# 배치 위치: /mnt/us/extensions/MRInstaller/bin/mrinstaller.sh
# 실행 방법: 기기 홈 화면 검색창에  ;log mrpi
#
# ## 왜 MRPI 자리를 쓰나
#
# 5.19.6 에서 `;log mrpi` 가 이 경로의 스크립트를 **루트로 실행**하는 것이
# 실측으로 확인됐다 (uid=0). 반면 MRPI 본체는 이 펌웨어에서 동작하지 않는다 —
# 번들 `kindletool` 이 `libz.so.1: internal error` 로 죽는다(라이브러리 비호환,
# noexec 아님). 즉 이 자리는 비어 있는 것과 같아 우리가 쓴다.
#
# ## 하는 일
#
#   1) WiFi 로 Mac 수신기에 DB 를 push  (URL 이 설정돼 있을 때)
#   2) 실패하거나 URL 이 없으면 documents/ 로 복사  (USB 로 회수)
#
# WAL 모드면 `-wal`/`-shm` 에 최근 트랜잭션이 들어 있으므로 함께 보낸다.
# 본체만 가져가면 마지막 하이라이트 몇 개가 빠질 수 있다.
##

# ── 설정 ────────────────────────────────────────────────────────────────
# Mac 에서 tools/ksdk_receiver.py 를 띄우면 출력되는 URL 을 여기 넣는다.
# 비워 두면 WiFi 를 건너뛰고 USB 회수용 복사만 한다.
PUSH_URL=""
# ────────────────────────────────────────────────────────────────────────

ACCT_DIR=$(ls -d /mnt/us/system/ksdk/.annotations/amzn1.account.* 2>/dev/null | head -1)
DB="$ACCT_DIR/ksdk_annotation_v1.db"
OUT=/mnt/us/documents/ksdk_export
LOG="$OUT/push.log"

mkdir -p "$OUT" 2>/dev/null
{
  echo "=== $(date) ==="
  echo "id      : $(id)"
  echo "acct_dir: $ACCT_DIR"
  echo "db      : $DB"
} > "$LOG" 2>&1

if [ ! -f "$DB" ]; then
  echo "ERROR: DB 없음" >> "$LOG"
  echo fail > /mnt/us/documents/ksdk_export_done.txt
  exit 1
fi

echo "size    : $(ls -l "$DB" | awk '{print $5}')" >> "$LOG"

# ── 1) WiFi push ────────────────────────────────────────────────────────
pushed=0
if [ -n "$PUSH_URL" ]; then
  echo "--- wifi push ---" >> "$LOG"
  for suffix in "" "-wal" "-shm"; do
    f="${DB}${suffix}"
    [ -f "$f" ] || continue
    name="ksdk_annotation_v1.db${suffix}"
    # --fail: HTTP 오류를 종료코드로 / -T: PUT 업로드
    if curl -sS --fail --max-time 300 -T "$f" "$PUSH_URL/$name" >> "$LOG" 2>&1; then
      echo "  OK   $name ($(ls -l "$f" | awk '{print $5}') bytes)" >> "$LOG"
      pushed=$((pushed + 1))
    else
      echo "  FAIL $name (rc=$?)" >> "$LOG"
    fi
  done
  echo "pushed  : $pushed" >> "$LOG"
fi

# ── 2) USB 회수용 복사 (push 실패 시 보험) ──────────────────────────────
if [ "$pushed" -eq 0 ]; then
  echo "--- local copy ---" >> "$LOG"
  for suffix in "" "-wal" "-shm"; do
    f="${DB}${suffix}"
    [ -f "$f" ] || continue
    cp "$f" "$OUT/$(basename "$f")" 2>> "$LOG" \
      && echo "  copied $(basename "$f")" >> "$LOG"
  done
fi

ls -la "$OUT" >> "$LOG" 2>&1
echo done > /mnt/us/documents/ksdk_export_done.txt
exit 0
