"""kindle.exporters — sync_export_json_grouped 의 strip 정책.

`source_file` 은 기기(YJR 사이드카 경로, KSDK DB 경로)를 식별할 유일한 흔적이라
지우면 안 된다 (CLAUDE.md "통합을 막고 있는 것", 2026-09-20 strip 해제).
"""

import json
from pathlib import Path

from kindle.exporters import sync_export_json_grouped
from kindle.models import Clipping


def _clip(**kw) -> Clipping:
    base = dict(book_title="책", author="저자", clip_type="highlight",
               location_start=1, location_end=2, added_date="2026-01-01 00:00:00",
               content="본문", source_file="/mnt/us/system/ksdk/.annotations/acct/ksdk_annotation_v1.db")
    base.update(kw)
    return Clipping(**base)


def test_source_file_is_kept(tmp_path: Path):
    out = tmp_path / "out.json"
    sync_export_json_grouped([_clip()], out, {})
    clip = json.loads(out.read_text(encoding="utf-8"))["books"][0]["clippings"][0]
    assert clip["source_file"] == "/mnt/us/system/ksdk/.annotations/acct/ksdk_annotation_v1.db"


def test_book_title_and_author_are_still_stripped_per_clip(tmp_path: Path):
    """책 단위로 이미 있으므로 클리핑마다 반복하지 않는다 — 이건 그대로 유지."""
    out = tmp_path / "out.json"
    sync_export_json_grouped([_clip()], out, {})
    clip = json.loads(out.read_text(encoding="utf-8"))["books"][0]["clippings"][0]
    assert "book_title" not in clip and "author" not in clip


def test_different_devices_keep_distinct_source_file(tmp_path: Path):
    """같은 책을 두 기기에서 읽어도 source_file 로 구분된다 — 삭제 탐지의 전제 조건."""
    out = tmp_path / "out.json"
    a = _clip(source_file="/device-a/ksdk_annotation_v1.db")
    b = _clip(source_file="/device-b/ksdk_annotation_v1.db", location_start=3, location_end=4)
    sync_export_json_grouped([a, b], out, {})
    clips = json.loads(out.read_text(encoding="utf-8"))["books"][0]["clippings"]
    sources = {c["source_file"] for c in clips}
    assert sources == {"/device-a/ksdk_annotation_v1.db", "/device-b/ksdk_annotation_v1.db"}
