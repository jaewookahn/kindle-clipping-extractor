"""Tests for kindle/parsers/yjr.py using the example fixtures."""

from pathlib import Path

from kindle.parsers.yjr import parse_yjr, parse_yjf
from kindle.models import Clipping

_SDR = Path(__file__).parent.parent / (
    "examples/"
    "gongsandangseoneon - kareul mareukeuseu _ peurideurihi enggelseu.sdr"
)
_YJR = next(_SDR.glob("*.yjr"))
_YJF = next(_SDR.glob("*.yjf"))

BOOK_TITLE = "공산당선언"


# ---------------------------------------------------------------------------
# parse_yjr
# ---------------------------------------------------------------------------

def test_yjr_returns_list():
    clips = parse_yjr(_YJR, book_title=BOOK_TITLE)
    assert isinstance(clips, list)
    assert len(clips) > 0


def test_yjr_all_clippings():
    clips = parse_yjr(_YJR, book_title=BOOK_TITLE)
    assert all(isinstance(c, Clipping) for c in clips)


def test_yjr_book_title():
    clips = parse_yjr(_YJR, book_title=BOOK_TITLE)
    assert all(c.book_title == BOOK_TITLE for c in clips)


def test_yjr_clip_types():
    clips = parse_yjr(_YJR, book_title=BOOK_TITLE)
    valid = {"highlight", "bookmark", "note"}
    assert all(c.clip_type in valid for c in clips)


def test_yjr_location_start_positive():
    clips = parse_yjr(_YJR, book_title=BOOK_TITLE)
    # All clips with a start location should have a positive integer
    for c in clips:
        if c.location_start is not None:
            assert c.location_start > 0


def test_yjr_location_end_gte_start():
    clips = parse_yjr(_YJR, book_title=BOOK_TITLE)
    for c in clips:
        if c.location_start is not None and c.location_end is not None:
            assert c.location_end >= c.location_start


def test_yjr_timestamps_format():
    clips = parse_yjr(_YJR, book_title=BOOK_TITLE)
    for c in clips:
        if c.added_date:
            # Expect "YYYY-MM-DD HH:MM:SS"
            assert len(c.added_date) == 19
            assert c.added_date[4] == "-"
            assert c.added_date[7] == "-"


def test_yjr_source_file():
    clips = parse_yjr(_YJR, book_title=BOOK_TITLE)
    assert all(c.source_file == str(_YJR) for c in clips)


def test_yjr_default_title():
    clips = parse_yjr(_YJR)
    assert all(c.book_title != "" for c in clips)


# ---------------------------------------------------------------------------
# parse_yjf
# ---------------------------------------------------------------------------

def test_yjf_returns_list():
    clips = parse_yjf(_YJF, book_title=BOOK_TITLE)
    assert isinstance(clips, list)


def test_yjf_last_position_type():
    clips = parse_yjf(_YJF, book_title=BOOK_TITLE)
    # If any entries exist, they should be last_position
    for c in clips:
        assert c.clip_type == "last_position"


def test_yjf_location_positive():
    clips = parse_yjf(_YJF, book_title=BOOK_TITLE)
    for c in clips:
        if c.location_start is not None:
            assert c.location_start > 0
