"""kindle.ksdk — KSDKAnnotations DB 파서.

임시 SQLite 를 만들어 검증한다. 실제 기기·회수본 DB 없이 돈다.
"""

import base64
import json
import sqlite3
import struct

import pytest

from kindle import ksdk


# --------------------------------------------------------------------------
# fixture
# --------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE nonsyncable_annotations (
  annotation_id TEXT NOT NULL, book_id TEXT NOT NULL, dataset INTEGER NOT NULL,
  start_position TEXT NOT NULL, end_position TEXT NOT NULL,
  created_time INTEGER NOT NULL, modified_time INTEGER NOT NULL,
  serialized_payload TEXT NOT NULL, PRIMARY KEY (annotation_id, book_id))
"""


def _pos(short, eid=1, off=0):
    raw = bytes([0x01]) + struct.pack("<II", eid, off)
    return json.dumps({"longPosition": base64.b64encode(raw).decode(),
                       "shortPosition": short})


def _row(aid, asin, dataset, typ, start, end, created, meta=None):
    payload = {
        "book_data": {"asin": asin, "contentType": "PDOC", "guid": "CR!X",
                      "isOwnedByCustomer": 0, "isSample": 0},
        "created_time": created,
        "start_position": json.loads(_pos(start)),
        "end_position": json.loads(_pos(end)),
        "position_type": 0,
    }
    if typ:
        payload["type"] = typ
    if meta is not None:
        payload["json_metadata"] = json.dumps(meta)   # 이중 인코딩이 실제 형식
    return (aid, f"{asin}-PDOC-CR!X-0", dataset, _pos(start), _pos(end),
            created, created, json.dumps(payload))


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "ksdk_annotation_v1.db"
    con = sqlite3.connect(p)
    con.execute(_SCHEMA)
    con.executemany("INSERT INTO nonsyncable_annotations VALUES (?,?,?,?,?,?,?,?)", [
        _row("a1", "ASIN1", 1, "HIGHLIGHT", 804, 862, 1789000000000, {"mchl_color": "yellow"}),
        _row("a2", "ASIN1", 2, "BOOKMARK", 5000, 5000, 1789000001000, {"mchl_color": "dark_blue"}),
        _row("a3", "ASIN1", 3, "NOTE", 6000, 6005, 1789000002000, {"note_text": "메모 본문"}),
        _row("a4", "ASIN2", 1, "HIGHLIGHT", 100, 120, 1789000003000, {"mchl_color": "pink"}),
        # 클리핑이 아닌 것들 — 제외되어야 한다
        _row("a5", "ASIN1", 7, None, 1, 1, 1789000004000),            # waypoint
        _row("a6", "ASIN1", 8, None, 2, 2, 1789000005000),            # last_read
        _row("a7", "ASIN1", 18, "POPULARHIGHLIGHT", 3, 9, 1789000006000, {"num_users": 42}),
    ])
    con.commit(); con.close()
    return p


# --------------------------------------------------------------------------

def test_parses_only_real_clippings(db):
    clips = ksdk.parse_ksdk_db(db)
    assert len(clips) == 4                       # waypoint/last_read/popular 제외
    assert {c.clip_type for c in clips} == {"highlight", "bookmark", "note"}


def test_skips_popular_highlight(db):
    assert all("POPULAR" not in (c.content or "").upper()
               for c in ksdk.parse_ksdk_db(db))


def test_asin_filter(db):
    assert len(ksdk.parse_ksdk_db(db, asin="ASIN2")) == 1
    assert len(ksdk.parse_ksdk_db(db, asin="없는asin")) == 0


def test_short_position_is_char_offset(db):
    """shortPosition 을 그대로 location_start 로 싣는다 (PRE-KL char offset)."""
    c = next(c for c in ksdk.parse_ksdk_db(db, asin="ASIN1")
             if c.clip_type == "highlight")
    assert (c.location_start, c.location_end) == (804, 862)


def test_sorted_by_offset(db):
    offs = [c.location_start for c in ksdk.parse_ksdk_db(db, asin="ASIN1")]
    assert offs == sorted(offs)


def test_color_prefix_and_note_text(db):
    clips = {c.clip_type: c for c in ksdk.parse_ksdk_db(db, asin="ASIN1")}
    # 하이라이트·북마크는 색상 접두사만 (본문은 fill_clipping_text 가 채운다)
    assert clips["highlight"].content == "[yellow] "
    assert clips["bookmark"].content == "[dark_blue] "
    # 노트는 본문이 DB 안에 있다
    assert clips["note"].content == "메모 본문"


def test_date_normalized(db):
    c = ksdk.parse_ksdk_db(db, asin="ASIN2")[0]
    assert c.added_date and len(c.added_date) == 19 and c.added_date[4] == "-"


def test_book_title_defaults_to_asin(db):
    assert ksdk.parse_ksdk_db(db, asin="ASIN2")[0].book_title == "ASIN2"
    assert ksdk.parse_ksdk_db(db, asin="ASIN2", book_title="제목")[0].book_title == "제목"


def test_group_by_asin(db):
    g = ksdk.group_by_asin(db)
    assert g == {"ASIN1": 3, "ASIN2": 1}


def test_decode_long_position():
    raw = bytes([0x01]) + struct.pack("<II", 4434, 136)
    assert ksdk.decode_long_position(base64.b64encode(raw).decode()) == (4434, 136)


def test_decode_long_position_rejects_bad_input():
    assert ksdk.decode_long_position("!!!not base64!!!") is None
    assert ksdk.decode_long_position(base64.b64encode(b"\x02short").decode()) is None


def test_missing_db_raises(tmp_path):
    with pytest.raises(ksdk.KSDKError):
        list(ksdk.iter_annotations(tmp_path / "없음.db"))


def test_bad_payload_is_skipped(db):
    con = sqlite3.connect(db)
    con.execute("INSERT INTO nonsyncable_annotations VALUES (?,?,?,?,?,?,?,?)",
                ("bad", "ASIN1-PDOC-CR!X-0", 1, _pos(1), _pos(2), 1, 1, "{깨진 JSON"))
    con.commit(); con.close()
    assert len(ksdk.parse_ksdk_db(db, asin="ASIN1")) == 3     # 늘지 않는다


def test_missing_metadata_is_tolerated(db):
    con = sqlite3.connect(db)
    con.execute("INSERT INTO nonsyncable_annotations VALUES (?,?,?,?,?,?,?,?)",
                _row("nometa", "ASIN3", 1, "HIGHLIGHT", 7, 9, 1789000007000))
    con.commit(); con.close()
    c = ksdk.parse_ksdk_db(db, asin="ASIN3")[0]
    assert c.content == "" and c.location_start == 7


# --------------------------------------------------------------------------
# fingerprint 호환 — 회귀하면 중복 업로드가 난다
# --------------------------------------------------------------------------

def test_bookmark_end_is_none_like_yjr(db):
    """북마크의 location_end 는 None 이어야 한다.

    KSDK DB 는 end==start 로 채워 주지만 `parse_yjr()` 은 None 으로 남긴다.
    fingerprint 가 `title|type|start|end` 라서 여기가 어긋나면 **이미 동기화한
    북마크가 전부 신규로 오인**된다. 실측에서 먼저 온 미래 북마크 12건이
    이 이유로 교집합에서 빠졌다.
    """
    bm = next(c for c in ksdk.parse_ksdk_db(db, asin="ASIN1")
              if c.clip_type == "bookmark")
    assert bm.location_start == 5000
    assert bm.location_end is None


def test_non_bookmark_keeps_end(db):
    hl = next(c for c in ksdk.parse_ksdk_db(db, asin="ASIN1")
              if c.clip_type == "highlight")
    assert (hl.location_start, hl.location_end) == (804, 862)


def test_fingerprint_matches_yjr_for_same_position(db):
    """같은 위치·타입·제목이면 YJR 클리핑과 fingerprint 가 일치해야 한다."""
    from kindle.models import Clipping
    from kindle.notion_export import fingerprint
    ks = {fingerprint(c) for c in ksdk.parse_ksdk_db(db, asin="ASIN1", book_title="책")}
    # parse_yjr 이 만들어내는 형태를 손으로 재현
    like_yjr = [
        Clipping(book_title="책", clip_type="highlight", location_start=804, location_end=862),
        Clipping(book_title="책", clip_type="bookmark", location_start=5000, location_end=None),
        Clipping(book_title="책", clip_type="note", location_start=6000, location_end=6005),
    ]
    assert {fingerprint(c) for c in like_yjr} <= ks
