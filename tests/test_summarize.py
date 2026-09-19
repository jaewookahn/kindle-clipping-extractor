"""kindle.summarize — 장별 상세 요약.

네트워크를 타지 않는다. DeepSeek 호출부(`_post_chat`)를 monkeypatch 한다.
"""

import json

import pytest

from kindle import summarize as sm
from kindle.models import Clipping


def _clip(content, chapter=None, ctype="highlight"):
    return Clipping(book_title="책", clip_type=ctype, content=content, chapter=chapter)


# -- 그룹핑 -----------------------------------------------------------------

def test_group_by_chapter_keeps_order_and_skips_bookmarks():
    clips = [
        _clip("[yellow] 가", chapter="1장"),
        _clip("", chapter="1장", ctype="bookmark"),
        _clip("[yellow] 나", chapter="2장"),
        _clip("[yellow] 다", chapter="1장"),
    ]
    groups = sm.group_by_chapter(clips)
    assert [ch for ch, _ in groups] == ["1장", "2장"]
    assert len(groups[0][1]) == 2      # 북마크 제외
    assert len(groups[1][1]) == 1


def test_group_by_chapter_buckets_missing_chapter():
    groups = sm.group_by_chapter([_clip("[yellow] 가")])
    assert groups[0][0] == sm._NO_CHAPTER


def test_group_by_chapter_skips_empty_content():
    assert sm.group_by_chapter([_clip("   ", chapter="1장")]) == []


def test_clip_text_strips_color_prefix():
    assert sm._clip_text(_clip("[yellow] 본문입니다")) == "본문입니다"
    assert sm._clip_text(_clip("색없음")) == "색없음"


# -- 요약 + 캐시 -------------------------------------------------------------

def test_summarize_book_calls_llm_once_per_chapter(monkeypatch):
    calls = []
    monkeypatch.setattr(sm, "_post_chat",
                        lambda *a, **k: calls.append(a[3]) or "요약문")
    clips = [_clip("[yellow] 가", chapter="1장"), _clip("[yellow] 나", chapter="2장")]
    out = sm.summarize_book(clips, "책", api_key="k")
    assert [(c.chapter, c.summary) for c in out] == [("1장", "요약문"), ("2장", "요약문")]
    assert len(calls) == 2


def test_summarize_book_uses_cache(monkeypatch):
    n = {"calls": 0}

    def fake(*a, **k):
        n["calls"] += 1
        return "요약문"

    monkeypatch.setattr(sm, "_post_chat", fake)
    clips = [_clip("[yellow] 가", chapter="1장")]
    cache = sm.load_cache("/nonexistent-path-for-test")

    sm.summarize_book(clips, "책", api_key="k", cache=cache)
    assert n["calls"] == 1
    sm.summarize_book(clips, "책", api_key="k", cache=cache)   # 두 번째는 캐시
    assert n["calls"] == 1


def test_cache_invalidates_when_highlights_change(monkeypatch):
    n = {"calls": 0}
    monkeypatch.setattr(sm, "_post_chat",
                        lambda *a, **k: (n.__setitem__("calls", n["calls"] + 1), "요약문")[1])
    cache = sm.load_cache("/nonexistent-path-for-test")

    sm.summarize_book([_clip("[yellow] 가", chapter="1장")], "책", api_key="k", cache=cache)
    assert n["calls"] == 1
    # 같은 장에 하이라이트가 하나 늘면 다시 요약해야 한다
    sm.summarize_book([_clip("[yellow] 가", chapter="1장"),
                       _clip("[yellow] 나", chapter="1장")], "책", api_key="k", cache=cache)
    assert n["calls"] == 2


def test_summarize_book_requires_key_only_on_miss(monkeypatch):
    monkeypatch.delenv(sm._ENV_KEY, raising=False)
    cache = sm.load_cache("/nonexistent-path-for-test")
    cache["chapters"][sm._cache_key("책", "1장")] = {
        "digest": sm._content_digest([_clip("[yellow] 가", chapter="1장")]),
        "summary": "캐시된 요약",
    }
    # 캐시 적중이면 키 없이도 된다
    out = sm.summarize_book([_clip("[yellow] 가", chapter="1장")], "책", cache=cache)
    assert [(c.chapter, c.summary) for c in out] == [("1장", "캐시된 요약")]
    assert out[0].clips, "캐시 적중 때도 인용용 클리핑을 들고 있어야 한다"

    # 캐시에 없으면 키를 요구한다
    with pytest.raises(sm.SummarizerError):
        sm.summarize_book([_clip("[yellow] 나", chapter="2장")], "책", cache=cache)


def test_summarize_book_min_clips(monkeypatch):
    monkeypatch.setattr(sm, "_post_chat", lambda *a, **k: "요약문")
    clips = [_clip("[yellow] 가", chapter="1장")]
    assert sm.summarize_book(clips, "책", api_key="k", min_clips=2) == []


def test_cache_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    cache = sm.load_cache(p)
    cache["chapters"]["k"] = {"digest": "d", "summary": "s"}
    sm.save_cache(p, cache)
    assert sm.load_cache(p)["chapters"]["k"]["summary"] == "s"


def test_load_cache_tolerates_corrupt_file(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not json", encoding="utf-8")
    assert sm.load_cache(p) == {"version": sm._VERSION, "chapters": {}}


# -- 출력 포맷 ---------------------------------------------------------------

def test_format_chapter_summaries():
    out = sm.format_chapter_summaries([sm.ChapterSummary("1장", "요약A[1]", []),
                                       sm.ChapterSummary("2장", "요약B", [])])
    assert "1장" in out and "요약A[1]" in out and "2장" in out
    assert out.startswith("\n\n")          # 클리핑 뒤에 붙으므로 앞 여백


def test_format_citations_includes_page_and_location():
    c = Clipping(book_title="책", clip_type="highlight", content="[yellow] 본문입니다",
                 page=57, location_start=881, location_end=885, chapter="1장")
    out = sm.format_citations([c])
    assert "[1]" in out and "p57" in out and "Loc 881–885" in out and "본문입니다" in out


def test_format_citations_numbering_matches_prompt(monkeypatch):
    """프롬프트의 [n] 과 인용 목록의 [n] 이 같은 리스트에서 나와야 한다."""
    clips = [_clip("[yellow] 가", chapter="1장"), _clip("   ", chapter="1장"),
             _clip("[yellow] 나", chapter="1장")]
    sent = {}
    def fake(api_key, model, msgs, *a, **kw):
        sent["u"] = msgs[1]["content"]
        return "요약[2]"

    monkeypatch.setattr(sm, "_post_chat", fake)
    sm.summarize_chapter(clips, "책", "1장", "k")
    cites = sm.format_citations(clips)
    assert "[1] " in sent["u"] and "[2] " in sent["u"] and "[3]" not in sent["u"]
    assert cites.count("\n  [") == 2          # 빈 내용은 번호를 받지 않는다


def test_format_citations_truncates_long_text():
    c = Clipping(book_title="책", clip_type="highlight", content="가" * 500, page=1)
    assert "…" in sm.format_citations([c], max_chars=50)


def test_summary_with_citations_can_be_disabled():
    c = Clipping(book_title="책", clip_type="highlight", content="본문", page=3)
    on = sm.format_chapter_summaries([sm.ChapterSummary("1장", "요약", [c])])
    off = sm.format_chapter_summaries([sm.ChapterSummary("1장", "요약", [c])],
                                      with_citations=False)
    assert "인용:" in on and "인용:" not in off


def test_format_chapter_summaries_empty():
    assert sm.format_chapter_summaries([]) == ""


# -- HTTP 오류 처리 ----------------------------------------------------------

def test_post_chat_raises_on_4xx(monkeypatch):
    class R:
        status_code = 401
        text = "bad key"
        headers: dict = {}

    monkeypatch.setattr(sm.requests, "post", lambda *a, **k: R())
    with pytest.raises(sm.SummarizerError) as e:
        sm._post_chat("k", "m", [], timeout=1, max_retries=0)
    assert "401" in str(e.value)


def test_post_chat_retries_5xx_then_succeeds(monkeypatch):
    seq = []

    class R:
        def __init__(self, code):
            self.status_code = code
            self.text = ""
            self.headers = {}

        def json(self):
            return {"choices": [{"message": {"content": " 요약 "}}]}

    def fake_post(*a, **k):
        seq.append(1)
        return R(500) if len(seq) == 1 else R(200)

    monkeypatch.setattr(sm.requests, "post", fake_post)
    monkeypatch.setattr(sm.time, "sleep", lambda s: None)
    assert sm._post_chat("k", "m", [], timeout=1, max_retries=2) == "요약"
    assert len(seq) == 2
