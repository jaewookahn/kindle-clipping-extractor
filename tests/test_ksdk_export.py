"""kindle.ksdk_export — `tools/ksdk_extract.sh` TSV 파서.

합성 TSV 로 검증한다(실제 DB·기기 불필요). 실제 DB 로 `parse_ksdk_db()` 와의
동등성을 확인하는 것은 `tools/verify_ksdk_export.py` (개인 백업 경로에
의존하는 검증 스크립트 — pytest 대상이 아니다, `color_census.py`·
`loc_end_check.py` 와 같은 패턴).
"""

from pathlib import Path

import pytest

from kindle import ksdk_export as E


def _write(tmp_path: Path, rows: list[str]) -> Path:
    p = tmp_path / "export.tsv"
    p.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
    return p


def _row(type_="HIGHLIGHT", asin="ASIN1", start="100", end="200",
        created="1741094751863", color="yellow", note=""):
    return "\t".join([type_, asin, start, end, created, color, note])


# --------------------------------------------------------------------------

def test_parses_basic_highlight(tmp_path):
    p = _write(tmp_path, [_row()])
    clips = E.parse_ksdk_export(p)
    assert len(clips) == 1
    c = clips[0]
    assert c.clip_type == "highlight"
    assert c.location_start == 100 and c.location_end == 200
    assert c.content == "[yellow] "
    assert c.book_title == "ASIN1"          # book_title 없으면 asin 이 대신한다
    assert c.source_file == str(p)


def test_note_uses_note_text_not_color(tmp_path):
    p = _write(tmp_path, [_row(type_="NOTE", color="", note="메모 본문")])
    c = E.parse_ksdk_export(p)[0]
    assert c.clip_type == "note"
    assert c.content == "메모 본문"


def test_bookmark_end_is_normalized_to_none(tmp_path):
    """KSDK 는 end==start 를 내보낼 수 있지만 클리핑에서는 None 이어야 한다
    — parse_ksdk_db() 와 어긋나면 fingerprint/clip_key 가 소스별로 갈린다."""
    p = _write(tmp_path, [_row(type_="BOOKMARK", start="500", end="500", color="dark_blue")])
    c = E.parse_ksdk_export(p)[0]
    assert c.location_start == 500
    assert c.location_end is None


def test_highlight_missing_end_defaults_to_start(tmp_path):
    p = _write(tmp_path, [_row(end="")])
    c = E.parse_ksdk_export(p)[0]
    assert c.location_end == c.location_start == 100


def test_asin_filter(tmp_path):
    p = _write(tmp_path, [_row(asin="A1"), _row(asin="A2")])
    assert len(E.parse_ksdk_export(p, asin="A2")) == 1
    assert len(E.parse_ksdk_export(p, asin="없는asin")) == 0


def test_book_title_overrides_asin(tmp_path):
    p = _write(tmp_path, [_row(asin="ASIN1")])
    c = E.parse_ksdk_export(p, book_title="제목")[0]
    assert c.book_title == "제목"


def test_added_date_formatted_same_as_ksdk_db(tmp_path):
    """kindle.ksdk._fmt_time 을 그대로 쓰므로 형식이 한 글자도 다르면 안 된다."""
    p = _write(tmp_path, [_row(created="1741094751863")])
    c = E.parse_ksdk_export(p)[0]
    assert c.added_date is not None and len(c.added_date) == 19 and c.added_date[4] == "-"


def test_empty_created_time_yields_no_date(tmp_path):
    p = _write(tmp_path, [_row(created="")])
    assert E.parse_ksdk_export(p)[0].added_date is None


def test_sorted_by_location_start(tmp_path):
    p = _write(tmp_path, [_row(start="500"), _row(start="10"), _row(start="200")])
    offs = [c.location_start for c in E.parse_ksdk_export(p)]
    assert offs == sorted(offs)


def test_blank_lines_are_skipped(tmp_path):
    p = tmp_path / "export.tsv"
    p.write_text(f"{_row()}\n\n\n{_row(start='999')}\n", encoding="utf-8")
    assert len(E.parse_ksdk_export(p)) == 2


def test_wrong_field_count_is_skipped_not_fatal(tmp_path):
    p = tmp_path / "export.tsv"
    p.write_text(f"broken\tline\n{_row()}\n", encoding="utf-8")
    clips = E.parse_ksdk_export(p)
    assert len(clips) == 1     # 깨진 줄은 건너뛰고 나머지는 파싱된다


def test_unknown_type_is_skipped(tmp_path):
    p = _write(tmp_path, [_row(type_="POPULARHIGHLIGHT")])
    assert E.parse_ksdk_export(p) == []


def test_missing_file_raises(tmp_path):
    with pytest.raises(E.KSDKError):
        E.parse_ksdk_export(tmp_path / "없음.tsv")


def test_non_integer_position_is_none_not_crash(tmp_path):
    p = _write(tmp_path, [_row(start="garbage")])
    c = E.parse_ksdk_export(p)[0]
    assert c.location_start is None


# --------------------------------------------------------------------------
# 이스케이프 — 여러 줄 메모가 공백으로 뭉개지면 안 된다 (실측으로 발견됨)
# --------------------------------------------------------------------------

def test_escaped_newline_in_note_is_restored(tmp_path):
    """tools/ksdk_extract.sh 가 실제 개행을 \\n(2글자)으로 보낸다."""
    p = _write(tmp_path, [_row(type_="NOTE", color="", note=r"Really?\npremodern?")])
    c = E.parse_ksdk_export(p)[0]
    assert c.content == "Really?\npremodern?"


def test_escaped_tab_in_note_is_restored(tmp_path):
    p = _write(tmp_path, [_row(type_="NOTE", color="", note=r"a\tb")])
    assert E.parse_ksdk_export(p)[0].content == "a\tb"


def test_escaped_backslash_is_restored_and_does_not_break_n_t():
    from kindle.ksdk_export import _unescape
    assert _unescape(r"a\\nb") == "a\\nb"        # 백슬래시 하나 + n — 개행이 아니다
    assert _unescape(r"a\\\\b") == r"a\\b"        # 이스케이프된 백슬래시 두 개
    assert _unescape(r"\n") == "\n"
    assert _unescape(r"\t") == "\t"


def test_unknown_escape_sequence_is_left_readable(tmp_path):
    """모르는 이스케이프(예: \\x)는 죽지 않고 원래 두 글자를 그대로 남긴다
    (조용히 백슬래시만 지우면 원문이 달라 보인다)."""
    from kindle.ksdk_export import _unescape
    assert _unescape(r"\x") == r"\x"
