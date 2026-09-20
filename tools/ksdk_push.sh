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
# Mac 수신기의 주소 (IP:포트만 — 토큰은 실행 때 GET /token 으로 받는다).
# 예: BASE_URL="http://192.168.1.172:8713"
# 비워 두면 WiFi 를 건너뛰고 USB 회수용 복사만 한다.
BASE_URL=""
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

# ── 0) OTA 차단 상태 점검 ──────────────────────────────────────────────
# 펌웨어 업데이트가 들어오면 탈옥·추출 창이 닫힌다 — 상태를 함께 실어 보낸다.
OTA="$OUT/ota_status.txt"
{
  echo "=== OTA 차단 상태 점검 $(date) ==="
  echo
  echo "## 업데이트 바이너리 (이름 변경 여부 = 차단 흔적)"
  ls -la /opt/amazon/ebook/bin/ 2>/dev/null | grep -iE "ota|update" || echo "  (없음/접근불가)"
  ls -la /usr/sbin/ 2>/dev/null | grep -iE "^.*ota|otaup" || echo "  (usr/sbin 에 ota 없음)"
  echo
  echo "## 실행 중인 OTA 관련 프로세스"
  ps 2>/dev/null | grep -iE "ota|update" | grep -v grep || echo "  (없음)"
  echo
  echo "## 루트의 업데이트 패키지"
  ls -la /mnt/us/*.bin 2>/dev/null || echo "  (없음 — 정상)"
  echo
  echo "## OTA 관련 설정·상태 파일"
  ls -la /var/local/ota* /var/local/*ota* 2>/dev/null || echo "  (없음)"
  cat /var/local/system/update_state 2>/dev/null || echo "  (update_state 없음)"
  echo
  echo "## 저장공간 (채우기 방식 차단 여부 판단용)"
  df -h /mnt/us /var/local 2>/dev/null
  echo
  echo "## 펌웨어 버전"
  cat /etc/prettyversion.txt 2>/dev/null || cat /etc/version.txt 2>/dev/null || echo "  (확인불가)"
} > "$OTA" 2>&1

# ── 1) WiFi push ────────────────────────────────────────────────────────
pushed=0
if [ -n "$BASE_URL" ]; then
  echo "--- wifi push ---" >> "$LOG"
  # 1회용 토큰을 수신기에서 받아온다. 실패하면 USB 복사로 폴백.
  TOKEN=$(curl -sS --fail --max-time 10 "$BASE_URL/token" 2>>"$LOG") || TOKEN=""
  if [ -z "$TOKEN" ]; then
    echo "  token fetch FAIL" >> "$LOG"
  else
    # ── 1a) KFX 본문 — 맥이 원하는 책만 ────────────────────────────────
    # 수신기의 GET /wanted 가 stem(확장자 없는 파일명)을 줄 단위로 준다.
    WANTED=$(curl -sS --fail --max-time 15 "$BASE_URL/wanted" 2>>"$LOG") || WANTED=""
    if [ -n "$WANTED" ]; then
      echo "$WANTED" | while read -r stem; do
        [ -n "$stem" ] || continue
        f="/mnt/us/documents/${stem}.kfx"
        if [ ! -f "$f" ]; then
          echo "  MISS  ${stem}.kfx" >> "$LOG"
          continue
        fi
        # URL 경로의 공백은 %20 으로 (수신기가 디코딩해 원래 이름으로 저장).
        enc=$(printf '%s' "$stem" | sed 's/ /%20/g')
        # KFX 는 수십 MB — 넉넉하게 900초
        if curl -sS --fail --max-time 900 -T "$f" "$BASE_URL/$TOKEN/${enc}.kfx" >> "$LOG" 2>&1; then
          echo "  OK    ${stem}.kfx ($(ls -l "$f" | awk '{print $5}') bytes)" >> "$LOG"
        else
          echo "  FAIL  ${stem}.kfx (rc=$?)" >> "$LOG"
        fi
      done
    fi
    # ── 1b) OTA 점검 결과 (수신기 EXPECTED_FILES 에 있음) ────────────────
    # pushed 카운터는 DB 전용 — OTA 만 성공해도 USB 폴백이 살아 있어야 한다.
    if [ -f "$OTA" ]; then
      if curl -sS --fail --max-time 60 -T "$OTA" "$BASE_URL/$TOKEN/ota_status.txt" >> "$LOG" 2>&1; then
        echo "  OK   ota_status.txt" >> "$LOG"
      else
        echo "  FAIL ota_status.txt (rc=$?)" >> "$LOG"
      fi
    fi
    # ── 1c) DB 를 **마지막에** — 수신기가 본체 도착을 완료 신호로 쓴다 ──
    for suffix in "" "-wal" "-shm"; do
      f="${DB}${suffix}"
      [ -f "$f" ] || continue
      name="ksdk_annotation_v1.db${suffix}"
      # --fail: HTTP 오류를 종료코드로 / -T: PUT 업로드
      if curl -sS --fail --max-time 300 -T "$f" "$BASE_URL/$TOKEN/$name" >> "$LOG" 2>&1; then
        echo "  OK   $name ($(ls -l "$f" | awk '{print $5}') bytes)" >> "$LOG"
        pushed=$((pushed + 1))
      else
        echo "  FAIL $name (rc=$?)" >> "$LOG"
      fi
    done
  fi
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
  [ -f "$OTA" ] && cp "$OTA" "$OUT/ota_status.txt" 2>> "$LOG" \
    && echo "  copied ota_status.txt" >> "$LOG"
fi

ls -la "$OUT" >> "$LOG" 2>&1
echo done > /mnt/us/documents/ksdk_export_done.txt
exit 0
