#!/bin/sh
##
# 정적 링크 ARM sqlite3 CLI 빌드 — ONLY 빌드, 기기에 올리지 않는다.
#
# 배경: ON_DEVICE_EXTRACT_REVIEW.md ①. kindletool 이 죽은 원인은 동적 링크
# `libz.so.1` 버전 비호환이었다(§16.6) — 정적 링크는 실행 시 공유 라이브러리를
# 전혀 참조하지 않으므로 이 실패 모드 자체가 없다.
#
# 크로스컴파일러: zig cc (brew install zig) — musl-cross 툴체인을 따로
# 설치하지 않고 단일 바이너리로 크로스 컴파일까지 되는 것을 실측 확인했다.
# 소스: sqlite.org 공식 앰알거메이션(단일 파일, 퍼블릭 도메인) — 커스텀
# 파서를 새로 짜지 않는다.
#
# ⚠️ 실행 검증은 기기에서만 가능하다 (Apple Silicon 맥은 ARM32 Linux ELF 를
# 실행할 수 없다 — 아키텍처도 OS/ABI 도 다르다). 여기서 확인되는 것은
# ① 정적 링크가 됐다 ② ELF 헤더가 armv7l 커널이 요구하는 32비트 ARM EABI5 와
# 맞다 ③ 로컬 sqlite3(같은 앰알거메이션 소스)로 이미 SQL 질의 자체는
# 검증했다(tools/verify_ksdk_export.py) — 뿐이다. 실제 기기 실행 가부는
# 미확인으로 남는다(하드/소프트 float ABI 등).
#
# 사용법:
#   tools/build_static_sqlite3_arm.sh [출력_디렉터리]
##

set -eu

OUT_DIR="${1:-/tmp/sqlite3-arm-build}"
SQLITE_VERSION="3530400"       # sqlite.org 앰알거메이션 버전 (2026-09-20 기준 최신)
AMAL_URL="https://sqlite.org/2026/sqlite-amalgamation-${SQLITE_VERSION}.zip"

if ! command -v zig >/dev/null 2>&1; then
  echo "zig 가 없습니다 — brew install zig" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
cd "$OUT_DIR"

if [ ! -f "sqlite-amalgamation-${SQLITE_VERSION}/sqlite3.c" ]; then
  echo "앰알거메이션 다운로드 중 … ($AMAL_URL)"
  curl -sS -o amalgamation.zip "$AMAL_URL"
  unzip -q -o amalgamation.zip
fi

cd "sqlite-amalgamation-${SQLITE_VERSION}"

echo "크로스 컴파일 중 (arm-linux-musleabihf, 정적) …"
zig cc -target arm-linux-musleabihf -static -O2 -s \
  -DSQLITE_ENABLE_JSON1 \
  -DSQLITE_THREADSAFE=0 \
  -DSQLITE_OMIT_LOAD_EXTENSION \
  -o "$OUT_DIR/sqlite3-arm" shell.c sqlite3.c -lm -lpthread -ldl

echo
echo "완료: $OUT_DIR/sqlite3-arm"
file "$OUT_DIR/sqlite3-arm" 2>/dev/null || true
ls -la "$OUT_DIR/sqlite3-arm"
echo
echo "🔴 기기에 올리지 않았습니다. 올리려면(총괄 승인 후):"
echo "   kindle/device.py::mtp_direct_session() 의 MTP 직접 쓰기로 documents/ 에 배치"
echo "   (§16.8 에서 mrinstaller.sh 교체에 이미 쓴 것과 같은 방법)"
