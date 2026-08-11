"""sync_to_notion — fingerprint 좌표계·카운트 관련 단위 테스트.

네트워크를 타지 않도록 Notion 호출부를 전부 monkeypatch 한다.
검증 대상은 상태 파일(synced_fingerprints)과 summary 계산 로직.
"""

import json

import pytest

from kindle import notion_export
from kindle.models import Clipping
from kindle.notion_export import (
    _build_properties,
    _NotionAPI,
    _split_chunks,
    fingerprint,
    sync_to_notion,
)


def _clip(title="책", ctype="highlight", start=10, end=None, content="본문"):
    return Clipping(book_title=title, clip_type=ctype, content=content,
                    location_start=start, location_end=end)


@pytest.fixture
def fake_notion(monkeypatch):
    """Notion API 호출을 전부 무력화하고, 본문에 쓰인 텍스트를 기록한다."""
    appended: list = []

    monkeypatch.setattr(notion_export, "_page_exists", lambda *a, **k: False)
    monkeypatch.setattr(notion_export, "_find_existing_page", lambda *a, **k: None)
    monkeypatch.setattr(notion_export, "_create_page", lambda *a, **k: "page-id-1")
    monkeypatch.setattr(notion_export, "_append_clippings",
                        lambda api, pid, formatted: appended.extend(formatted))
    monkeypatch.setattr(notion_export, "_rewrite_page_body", lambda *a, **k: None)
    monkeypatch.setattr(notion_export, "_update_page_properties", lambda *a, **k: None)
    return appended


def _run(tmp_path, clips, **kw):
    state_path = tmp_path / "state.json"
    summary = sync_to_notion(clips, notion_token="t", database_id="db",
                             state_path=state_path, enable_book_cover=False, **kw)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    return summary, state


# ---------------------------------------------------------------------------
# clip_fps: 외부에서 계산한 PRE-KL fingerprint 가 그대로 저장돼야 한다
# ---------------------------------------------------------------------------

def test_clip_fps_are_stored_verbatim(tmp_path, fake_notion):
    clips = [_clip(start=1), _clip(start=2)]
    _, state = _run(tmp_path, clips, clip_fps=["fp-A", "fp-B"])
    stored = state["books"]["책"]["synced_fingerprints"]
    assert stored == sorted(["fp-A", "fp-B"])


def test_without_clip_fps_falls_back_to_computed(tmp_path, fake_notion):
    clips = [_clip(start=1)]
    _, state = _run(tmp_path, clips)
    assert state["books"]["책"]["synced_fingerprints"] == [fingerprint(clips[0])]


def test_length_mismatch_raises_instead_of_silently_truncating(tmp_path, fake_notion):
    """호출부에서 clippings 만 정렬하면 짝이 어긋난다 — 조용히 넘어가면 안 된다."""
    clips = [_clip(start=1), _clip(start=2)]
    with pytest.raises(ValueError, match="clip_fps 길이 불일치"):
        _run(tmp_path, clips, clip_fps=["fp-A"])


# ---------------------------------------------------------------------------
# dedup
# ---------------------------------------------------------------------------

def test_second_run_skips_already_synced(tmp_path, fake_notion):
    clips = [_clip(start=1), _clip(start=2)]
    fps = ["fp-A", "fp-B"]
    _run(tmp_path, clips, clip_fps=fps)

    # 같은 fingerprint 로 재실행 → 전부 skip
    clips2 = [_clip(start=1), _clip(start=2)]
    summary2, _ = _run(tmp_path, clips2, clip_fps=fps)
    assert summary2["added"] == 0
    assert summary2["skipped"] == 2


# ---------------------------------------------------------------------------
# added 카운트: bookmark 는 본문에 쓰이지 않으므로 제외
# ---------------------------------------------------------------------------

def test_added_count_excludes_bookmarks(tmp_path, fake_notion):
    clips = [_clip(start=1), _clip(ctype="bookmark", start=2, content="")]
    summary, _ = _run(tmp_path, clips, clip_fps=["fp-A", "fp-B"])
    assert summary["added"] == 1          # bookmark 는 세지 않음
    # 그래도 bookmark fingerprint 는 기록돼 재등장하지 않아야 한다
    _, state = _run(tmp_path, [], clip_fps=[])
    assert "fp-B" in state["books"]["책"]["synced_fingerprints"]


def test_bookmark_only_book_records_fingerprints(tmp_path, fake_notion):
    clips = [_clip(ctype="bookmark", start=5, content="")]
    summary, state = _run(tmp_path, clips, clip_fps=["fp-BM"])
    assert summary["added"] == 0
    assert state["books"]["책"]["synced_fingerprints"] == ["fp-BM"]


# ---------------------------------------------------------------------------
# _split_chunks
# ---------------------------------------------------------------------------

def test_split_chunks_respects_max_len():
    text = "가" * 5000
    chunks = _split_chunks(text, max_len=2000)
    assert all(len(c) <= 2000 for c in chunks)
    assert "".join(chunks) == text


def test_split_chunks_newline_at_boundary_terminates():
    """max_len 구간의 첫 문자가 \\n 이면 rfind 가 0 을 반환 — 무한 루프 회귀 방지."""
    text = "\n" + "가" * 5000
    chunks = _split_chunks(text, max_len=100)
    assert "".join(chunks) == text
    assert all(len(c) <= 100 for c in chunks)


def test_split_chunks_prefers_newline_boundary():
    text = "가" * 50 + "\n" + "나" * 60
    chunks = _split_chunks(text, max_len=100)
    assert chunks[0] == "가" * 50
    assert "".join(chunks) == text


# ---------------------------------------------------------------------------
# _build_properties — Notion REST properties payload 형태
# ---------------------------------------------------------------------------

def test_build_properties_shapes():
    props = _build_properties(title="책", author="저자", highlight_count=7,
                              last_date="2026-03-24 20:10:52")
    assert props["Title"]["title"][0]["text"]["content"] == "책"
    assert props["Author"]["rich_text"][0]["text"]["content"] == "저자"
    assert props["Highlights"] == {"number": 7}
    assert props["Last Highlighted"]["date"]["start"].startswith("2026-03-24T20:10:52")
    assert "Last Synced" in props


def test_build_properties_omits_highlights_when_none():
    """증분 sync 는 전체 수를 모르므로 Highlights 를 건드리면 안 된다."""
    props = _build_properties(highlight_count=None, last_date=None)
    assert "Highlights" not in props
    assert "Title" not in props
    assert "Last Synced" in props


def test_build_properties_skips_unparsable_date():
    """My Clippings.txt 의 로캘 날짜는 파싱 실패 — 예외 없이 건너뛴다."""
    props = _build_properties(last_date="Monday, January 1, 2024 10:00:00 AM")
    assert "Last Highlighted" not in props


# ---------------------------------------------------------------------------
# _NotionAPI — 재시도 동작
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return {}


def _api_with_responses(monkeypatch, responses):
    """세션이 responses 를 순서대로 반환하도록 만든 _NotionAPI."""
    api = _NotionAPI("token", max_retries=3)
    calls = {"n": 0}
    slept: list = []

    def _fake_request(method, url, **kw):
        r = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        return r

    monkeypatch.setattr(api._session, "request", _fake_request)
    monkeypatch.setattr(notion_export.time, "sleep", lambda s: slept.append(s))
    return api, calls, slept


def test_retries_on_429_then_succeeds(monkeypatch):
    api, calls, slept = _api_with_responses(
        monkeypatch, [_Resp(429, {"Retry-After": "2"}), _Resp(200)]
    )
    r = api.get("/pages/x")
    assert r.status_code == 200
    assert calls["n"] == 2
    assert slept == [2.0]          # Retry-After 를 그대로 존중


def test_retries_on_5xx(monkeypatch):
    api, calls, slept = _api_with_responses(monkeypatch, [_Resp(500), _Resp(200)])
    r = api.get("/pages/x")
    assert r.status_code == 200
    assert calls["n"] == 2
    assert len(slept) == 1


def test_gives_up_after_max_retries(monkeypatch):
    api, calls, _ = _api_with_responses(monkeypatch, [_Resp(429)])
    r = api.get("/pages/x")
    assert r.status_code == 429
    assert calls["n"] == 4         # 최초 1회 + max_retries 3회


def test_no_retry_on_4xx(monkeypatch):
    """404/400 은 재시도해도 소용없다 — 즉시 반환."""
    api, calls, slept = _api_with_responses(monkeypatch, [_Resp(404)])
    r = api.get("/pages/x")
    assert r.status_code == 404
    assert calls["n"] == 1
    assert slept == []


def test_missing_token_raises():
    with pytest.raises(RuntimeError, match="토큰 누락"):
        _NotionAPI("")
