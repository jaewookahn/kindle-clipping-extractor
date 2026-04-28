"""Tests for kindle/parsers/my_clippings.py"""

import textwrap
from pathlib import Path

import pytest

from kindle.parsers.my_clippings import parse_my_clippings, is_limit_exceeded


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SAMPLE = textwrap.dedent("""\
    Some Book (Some Author)
    - Your Highlight on page 12 | Location 150-162 | Added on Monday, January 1, 2024 10:00:00 AM

    This is the highlighted text.
    ==========
    Another Book
    - Your Note on page 3 | Location 42 | Added on Tuesday, February 6, 2024 9:15:00 AM

    A note without author.
    ==========
    Some Book (Some Author)
    - Your Bookmark on page 99 | Location 1234 | Added on Wednesday, March 7, 2024 8:00:00 AM

    ==========
    Limit Book (Test Author)
    - Your Highlight on page 5 | Location 50-60 | Added on Thursday, April 4, 2024 7:00:00 AM

    <You have reached the clipping limit for this item>
    ==========
""")


@pytest.fixture()
def clippings_file(tmp_path: Path) -> Path:
    p = tmp_path / "My Clippings.txt"
    p.write_text(_SAMPLE, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# parse_my_clippings
# ---------------------------------------------------------------------------

def test_parse_count(clippings_file):
    clips = parse_my_clippings(clippings_file)
    assert len(clips) == 4


def test_highlight_fields(clippings_file):
    clips = parse_my_clippings(clippings_file)
    h = clips[0]
    assert h.book_title == "Some Book"
    assert h.author == "Some Author"
    assert h.clip_type == "highlight"
    assert h.page == 12
    assert h.location_start == 150
    assert h.location_end == 162
    assert h.content == "This is the highlighted text."
    assert h.added_date is not None and "2024" in h.added_date


def test_note_no_author(clippings_file):
    clips = parse_my_clippings(clippings_file)
    n = clips[1]
    assert n.book_title == "Another Book"
    assert n.author == ""
    assert n.clip_type == "note"
    assert n.location_start == 42
    assert n.location_end is None


def test_bookmark_no_content(clippings_file):
    clips = parse_my_clippings(clippings_file)
    b = clips[2]
    assert b.clip_type == "bookmark"
    assert b.content == ""


def test_limit_exceeded_content(clippings_file):
    clips = parse_my_clippings(clippings_file)
    lim = clips[3]
    assert lim.book_title == "Limit Book"
    assert is_limit_exceeded(lim.content)


def test_source_file_set(clippings_file):
    clips = parse_my_clippings(clippings_file)
    assert all(c.source_file == str(clippings_file) for c in clips)


# ---------------------------------------------------------------------------
# is_limit_exceeded
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("content", [
    "<You have reached the clipping limit for this item>",
    "<The clipping limit for this item has been reached>",
    "<이 항목의 클리핑 한도에 도달했습니다>",
    "",
    "   ",
])
def test_is_limit_exceeded_true(content):
    assert is_limit_exceeded(content)


@pytest.mark.parametrize("content", [
    "Normal highlight text",
    "근대의 부르주아지 자체가",
])
def test_is_limit_exceeded_false(content):
    assert not is_limit_exceeded(content)
