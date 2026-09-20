"""kindle.wifi — 수신기 동작 (GET /token, PUT 수신·거부).

루프백 바인드로 실제 HTTP 를 때린다. 기기 없이 돈다.
"""

import http.client
import time

import pytest

from kindle.wifi import WifiReceiver

_DB = b"SQLite format 3\x00" + b"\x00" * 512


@pytest.fixture
def rx(tmp_path):
    r = WifiReceiver(tmp_path, port=0, bind="127.0.0.1")
    r.start()
    yield r
    r.stop()


def _port(rx) -> int:
    assert rx._httpd is not None
    return rx._httpd.server_address[1]


def _req(port, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, path, body=body)
    r = c.getresponse()
    out = (r.status, r.read())
    c.close()
    return out


def test_token_endpoint(rx):
    status, body = _req(_port(rx), "GET", "/token")
    assert status == 200
    assert body.decode() == rx.token


def test_token_endpoint_only_exact_path(rx):
    status, _ = _req(_port(rx), "GET", "/token/extra")
    assert status == 404
    status, _ = _req(_port(rx), "GET", "/")
    assert status == 404


def test_put_wrong_token_rejected(rx, tmp_path):
    status, _ = _req(_port(rx), "PUT", f"/wrong{_DB[:4].decode()}/ksdk_annotation_v1.db",
                     body=_DB)
    assert status == 404
    assert not (tmp_path / "ksdk_annotation_v1.db").exists()


def test_put_bad_filename_rejected(rx, tmp_path):
    status, _ = _req(_port(rx), "PUT", f"/{rx.token}/etc_passwd", body=b"x")
    assert status == 404
    assert not (tmp_path / "etc_passwd").exists()


def test_put_ok_saves_and_signals_done(rx, tmp_path):
    port = _port(rx)
    status, _ = _req(port, "PUT", f"/{rx.token}/ksdk_annotation_v1.db", body=_DB)
    assert status == 200
    assert (tmp_path / "ksdk_annotation_v1.db").read_bytes() == _DB
    # 본체를 받으면 잠깐 뒤 완료 이벤트가 켜진다 (2초 타이머)
    result = rx.wait(timeout=10)
    assert result.db_path is not None
    assert result.files["ksdk_annotation_v1.db"][0] == len(_DB)


def test_post_rejected(rx):
    status, _ = _req(_port(rx), "POST", f"/{rx.token}/ksdk_annotation_v1.db")
    assert status == 405


def test_wait_times_out_without_db(rx):
    assert rx.wait(timeout=0.5).db_path is None


def test_wanted_endpoint(rx):
    rx.wanted = ["meonjeo on mirae - janggangmyeong", "daly"]
    status, body = _req(_port(rx), "GET", "/wanted")
    assert status == 200
    assert body.decode() == "meonjeo on mirae - janggangmyeong\ndaly\n"


def test_wanted_endpoint_empty(rx):
    status, body = _req(_port(rx), "GET", "/wanted")
    assert status == 200
    assert body == b""


def test_put_kfx_accepted_with_decoded_name(rx, tmp_path):
    port = _port(rx)
    # 기기 curl 은 공백을 %20 으로 보낸다 — 수신기가 원래 이름으로 되돌린다
    status, _ = _req(port, "PUT",
                     f"/{rx.token}/meonjeo%20on%20mirae%20-%20janggangmyeong.kfx",
                     body=b"kfx-bytes")
    assert status == 200
    assert (tmp_path / "meonjeo on mirae - janggangmyeong.kfx").read_bytes() == b"kfx-bytes"


def test_put_other_extension_rejected(rx, tmp_path):
    status, _ = _req(_port(rx), "PUT", f"/{rx.token}/evil.exe", body=b"x")
    assert status == 404
    assert not (tmp_path / "evil.exe").exists()
