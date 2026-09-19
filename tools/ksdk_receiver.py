#!/usr/bin/env python3
"""KSDK 어노테이션 DB 수신기 — 킨들이 WiFi 로 밀어 올린 파일을 받는다.

## 왜 필요한가

펌웨어 5.19.x 이후 어노테이션은 `/mnt/us/system/ksdk/.annotations/<account>/`
의 SQLite 로 간다. 이 경로는 USB(MTP)로 노출되지 않아 탈옥한 기기에서 꺼내야
하는데, 매번 케이블을 꽂고 MacDroid 를 토글하는 건 번거롭다. 기기에 `curl` 이
있으므로 **기기가 직접 밀어 올리게** 한다 (`tools/ksdk_push.sh` 참조).

    킨들 (검색창에 `;log mrpi`)  ──curl -T──▶  이 수신기  ──▶  로컬 파일

## 보안 — 일회성·최소 노출

인증 없는 PUT 수신기를 계속 띄워두는 건 위험하다. 그래서:

- **LAN 인터페이스에만 바인드** (기본 `0.0.0.0` 이 아니라 실제 LAN IP)
- URL 경로에 **1회용 토큰**을 넣어 그 경로로만 받는다
- 기대한 파일을 다 받으면 **즉시 종료**
- `--timeout` 안에 못 받으면 종료

강한 보안은 아니다. 같은 LAN 안의 다른 기기가 토큰을 알면 업로드할 수 있다.
그래서 **필요할 때 띄우고 바로 끄는** 용도로만 쓴다.

## 사용

    python tools/ksdk_receiver.py --out ~/kindle_annotation_backup/live
    # → 기기에서 실행할 curl 명령을 출력하고 대기

받은 파일은 `--out` 아래에 원래 이름으로 저장된다.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import secrets
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# 기기가 올릴 것으로 기대하는 파일들. WAL 모드면 -wal/-shm 이 따라온다 —
# 본체만 받으면 최근 트랜잭션이 통째로 빠질 수 있다.
EXPECTED = ("ksdk_annotation_v1.db", "ksdk_annotation_v1.db-wal",
            "ksdk_annotation_v1.db-shm")

MAX_BYTES = 256 * 1024 * 1024     # 방어적 상한 (DB 는 보통 수 MB)


def lan_ip() -> str:
    """기본 경로로 나가는 인터페이스의 주소. 루프백이면 빈 문자열."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 1))       # TEST-NET-1 — 실제로 전송하지 않는다
        ip = s.getsockname()[0]
    except OSError:
        ip = ""
    finally:
        s.close()
    return "" if ip.startswith("127.") else ip


class _Handler(BaseHTTPRequestHandler):
    server_version = "ksdk-receiver/1.0"
    token = ""
    out_dir = Path(".")
    received: dict = {}
    done = threading.Event()

    def log_message(self, fmt, *args):      # 기본 stderr 로그를 우리 형식으로
        print(f"  [{self.address_string()}] {fmt % args}", flush=True)

    def _deny(self, code=404):
        self.send_response(code)
        self.end_headers()

    def do_PUT(self):
        parts = [p for p in self.path.split("/") if p]
        if len(parts) != 2 or parts[0] != self.token:
            self.log_message("거부: 경로 불일치 %s", self.path)
            return self._deny()
        name = os.path.basename(parts[1])
        if name not in EXPECTED:
            self.log_message("거부: 예상 밖 파일명 %s", name)
            return self._deny()

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._deny(400)
        if length <= 0 or length > MAX_BYTES:
            self.log_message("거부: 길이 %s", length)
            return self._deny(413)

        dest = self.out_dir / name
        h = hashlib.sha1()
        got = 0
        with open(dest, "wb") as f:
            while got < length:
                chunk = self.rfile.read(min(65536, length - got))
                if not chunk:
                    break
                f.write(chunk); h.update(chunk); got += len(chunk)

        if got != length:
            self.log_message("불완전: %s (%d/%d)", name, got, length)
            dest.unlink(missing_ok=True)
            return self._deny(400)

        self.received[name] = (got, h.hexdigest())
        print(f"  ✅ {name}  {got:,}B  sha1 {h.hexdigest()[:12]}", flush=True)
        self.send_response(200); self.end_headers()

        # 본체를 받았으면 임무 완료로 본다 (-wal/-shm 은 없을 수도 있다)
        if "ksdk_annotation_v1.db" in self.received:
            threading.Timer(2.0, self.done.set).start()

    def do_POST(self):      # curl -F 로 보내는 경우를 막는다 — PUT 만 받는다
        self._deny(405)


def main() -> int:
    ap = argparse.ArgumentParser(description="KSDK DB WiFi 수신기 (일회성)")
    ap.add_argument("--out", default=None, metavar="DIR",
                    help="저장 위치 (기본: ~/kindle_annotation_backup/live-<시각>)")
    ap.add_argument("--port", type=int, default=8713)
    ap.add_argument("--bind", default=None,
                    help="바인드 주소 (기본: 자동 감지한 LAN IP)")
    ap.add_argument("--timeout", type=int, default=600, metavar="SEC",
                    help="이 시간 안에 못 받으면 종료 (기본 600초)")
    args = ap.parse_args()

    host = args.bind or lan_ip()
    if not host:
        print("LAN 주소를 찾지 못했습니다. --bind 로 직접 지정하세요.", file=sys.stderr)
        return 1

    out = Path(args.out) if args.out else (
        Path.home() / "kindle_annotation_backup" /
        ("live-" + datetime.datetime.now().strftime("%Y%m%d-%H%M")))
    out.mkdir(parents=True, exist_ok=True)

    token = secrets.token_urlsafe(9)
    _Handler.token = token
    _Handler.out_dir = out
    _Handler.received = {}
    _Handler.done = threading.Event()

    httpd = ThreadingHTTPServer((host, args.port), _Handler)
    base = f"http://{host}:{args.port}/{token}"

    print(f"수신 대기  {host}:{args.port}   (LAN 전용, 일회성)")
    print(f"저장 위치  {out}")
    print(f"제한시간   {args.timeout}초\n")
    print("기기 스크립트가 쓸 URL:")
    print(f"  {base}\n")

    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        ok = _Handler.done.wait(timeout=args.timeout)
    except KeyboardInterrupt:
        ok = False
        print("\n중단됨")
    httpd.shutdown()

    if _Handler.received:
        print(f"\n받은 파일 {len(_Handler.received)}개:")
        for n, (sz, sha) in _Handler.received.items():
            print(f"  {n:<32} {sz:>12,}B  sha1 {sha}")
        return 0
    print("\n아무것도 받지 못했습니다.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
