"""WiFi 수신 — 킨들이 KSDK 어노테이션 DB 를 직접 밀어 올린다.

## 왜

펌웨어 5.19.x 이후 어노테이션은 `/mnt/us/system/ksdk/.annotations/<account>/`
의 SQLite 로 간다. 이 경로는 USB(MTP)로 노출되지 않아 **탈옥한 기기에서 꺼내야**
하는데, 매번 케이블을 꽂고 MacDroid 를 토글하는 건 번거롭다.

기기에 `curl` 이 있으므로(실측 확인) 기기가 직접 올리게 한다:

    킨들 (검색창에 `;log mrpi`)  ──curl -T──▶  이 수신기  ──▶  로컬 파일

기기 쪽 스크립트는 `tools/ksdk_push.sh`.

## MacDroid 와 공존한다

MTP 직접 접근은 MacDroid 와 USB 인터페이스를 두고 배타적이지만, **WiFi 는 USB 를
쓰지 않으므로 마운트를 켜둔 채로 쓸 수 있다.** 그래서 어노테이션은 WiFi 로 받고
KFX 본문은 마운트에서 읽는 조합이 가능하다.

## 보안 — 일회성·최소 노출

인증 없는 PUT 수신기를 계속 띄워두면 안 된다:

- **LAN 인터페이스에만 바인드** (`0.0.0.0` 아님)
- URL 경로에 **1회용 토큰**
- 기대한 파일을 받으면 **즉시 종료**
- 타임아웃 후 종료

같은 LAN 의 다른 기기가 토큰을 알면 업로드할 수 있다 — 강한 보안이 아니라
**노출 시간을 줄이는** 설계다. 필요할 때만 띄운다.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import socket
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import unquote

__all__ = ["DEFAULT_PORT", "EXPECTED_FILES", "WifiReceiver", "ReceiveResult", "lan_ip"]

DEFAULT_PORT = 8713

# 기기가 올릴 수 있는 파일. WAL 모드면 -wal/-shm 에 최근 트랜잭션이 들어 있어
# 본체만 받으면 마지막 하이라이트 몇 개가 빠질 수 있다.
EXPECTED_FILES = ("ksdk_annotation_v1.db",
                  "ksdk_annotation_v1.db-wal",
                  "ksdk_annotation_v1.db-shm",
                  "ota_status.txt")

# KFX 본문도 받는다 — 책마다 파일명이 달라 확장자로 판별한다.
def _allowed(name: str) -> bool:
    return name in EXPECTED_FILES or name.lower().endswith(".kfx")

# DB 는 보통 수 MB, KFX 는 수십 MB. 방어적 상한.
MAX_BYTES = 256 * 1024 * 1024

logger = logging.getLogger(__name__)


@dataclass
class ReceiveResult:
    """수신 결과. `db_path` 가 None 이면 본체를 못 받은 것."""
    out_dir: Path
    files: Dict[str, Tuple[int, str]] = field(default_factory=dict)   # 이름 → (크기, sha1)
    timed_out: bool = False

    @property
    def db_path(self) -> Optional[Path]:
        if "ksdk_annotation_v1.db" in self.files:
            return self.out_dir / "ksdk_annotation_v1.db"
        return None


def lan_ip() -> str:
    """기본 경로로 나가는 인터페이스 주소. 루프백뿐이면 빈 문자열.

    실제로 패킷을 보내지 않는다 — UDP 소켓을 connect 하면 커널이 경로를 고르고
    그때 로컬 주소가 정해지는 것을 이용한다.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 1))      # TEST-NET-1 (RFC 5737)
        ip = s.getsockname()[0]
    except OSError:
        ip = ""
    finally:
        s.close()
    return "" if ip.startswith("127.") else ip


class _Handler(BaseHTTPRequestHandler):
    server_version = "kindle-clipping-extractor/1.0"
    receiver: "WifiReceiver" = None      # type: ignore[assignment]

    def log_message(self, fmt, *args):
        logger.info("[%s] %s", self.address_string(), fmt % args)

    def _deny(self, code=404):
        # 요청 본문을 읽어 버려야 클라이언트(curl)가 RST 대신 깨끗한 404 를
        # 받는다 — 옛 토큰으로 푸시한 기기도 로그에 명확한 rc 를 남긴다.
        try:
            length = int(self.headers.get("Content-Length") or 0)
            while length > 0:
                chunk = self.rfile.read(min(65536, length))
                if not chunk:
                    break
                length -= len(chunk)
        except (ValueError, OSError):
            pass
        self.send_response(code)
        self.end_headers()

    def do_GET(self):
        # 기기 스크립트가 토큰을 동적으로 받아간다. 스크립트에는 IP:포트만
        # 고정해 두면 되므로 실행 때마다 갱신할 필요가 없다 (MacDroid 전파
        # 지연으로 옛 토큰을 쓰는 경쟁을 원천 차단).
        parts = [p for p in self.path.split("/") if p]
        if parts == ["token"]:
            body = self.receiver.token.encode()
        elif parts == ["wanted"]:
            # 맥이 본문을 원하는 책의 stem 목록 (줄 단위). 기기 스크립트가
            # /mnt/us/documents/<stem>.kfx 를 찾아 올린다.
            body = ("\n".join(self.receiver.wanted)
                    + ("\n" if self.receiver.wanted else "")).encode()
        else:
            self._deny()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self):
        r = self.receiver
        parts = [p for p in self.path.split("/") if p]
        if len(parts) != 2 or parts[0] != r.token:
            logger.warning("거부(경로): %s", self.path)
            return self._deny()
        name = os.path.basename(unquote(parts[1]))
        if not _allowed(name):
            logger.warning("거부(파일명): %s", name)
            return self._deny()

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._deny(400)
        if length <= 0 or length > MAX_BYTES:
            logger.warning("거부(길이): %s", length)
            return self._deny(413)

        dest = r.out_dir / name
        h = hashlib.sha1()
        got = 0
        with open(dest, "wb") as f:
            while got < length:
                chunk = self.rfile.read(min(65536, length - got))
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)

        if got != length:
            logger.error("불완전 수신 %s (%d/%d)", name, got, length)
            dest.unlink(missing_ok=True)
            return self._deny(400)

        r.result.files[name] = (got, h.hexdigest())
        if r.on_file:
            r.on_file(name, got, h.hexdigest())
        self.send_response(200)
        self.end_headers()

        # 본체가 오면 완료로 본다. -wal/-shm 은 없을 수도 있으므로
        # 곧바로 끊지 않고 잠깐 더 기다린다.
        if "ksdk_annotation_v1.db" in r.result.files:
            threading.Timer(2.0, r._done.set).start()

    def do_POST(self):
        self._deny(405)     # PUT 만 받는다


class WifiReceiver:
    """일회성 수신기. `with` 로 쓰거나 `start()`/`wait()`/`stop()` 을 직접 호출.

        with WifiReceiver(out_dir) as rx:
            print(rx.url)           # 기기 스크립트에 넣을 URL
            result = rx.wait(600)
    """

    def __init__(self, out_dir: Path, port: int = DEFAULT_PORT,
                 bind: Optional[str] = None,
                 on_file: Optional[Callable[[str, int, str], None]] = None,
                 wanted: Optional[list] = None) -> None:
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.host = bind or lan_ip()
        if not self.host:
            raise RuntimeError("LAN 주소를 찾지 못했습니다. bind= 로 직접 지정하세요.")
        self.port = port
        self.token = secrets.token_urlsafe(9)
        self.on_file = on_file
        self.wanted = list(wanted or [])
        self.result = ReceiveResult(out_dir=self.out_dir)
        self._done = threading.Event()
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        """기기 스크립트의 `PUSH_URL` 에 넣을 값.

        새 스크립트(tools/ksdk_push.sh)는 이 URL 의 토큰 부분을 직접 쓰지 않고
        `GET /token` 으로 받아간다 — 여기에는 참조용으로만 남긴다.
        """
        return f"http://{self.host}:{self.port}/{self.token}"

    @property
    def base_url(self) -> str:
        """기기 스크립트의 `BASE_URL` 에 넣을 값 (토큰 제외)."""
        return f"http://{self.host}:{self.port}"

    def start(self) -> "WifiReceiver":
        handler = type("_BoundHandler", (_Handler,), {"receiver": self})
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        logger.info("WiFi 수신 대기 %s:%s", self.host, self.port)
        return self

    def wait(self, timeout: float = 600.0) -> ReceiveResult:
        self.result.timed_out = not self._done.wait(timeout=timeout)
        return self.result

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def __enter__(self) -> "WifiReceiver":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
