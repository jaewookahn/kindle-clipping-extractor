"""kindle.ksdk_staleness — KSDK DB 반입 staleness 방어.

배경: SYMLINK_STALENESS_REVIEW.md §3 — 로컬 파일 mtime 은 신뢰할 수 없어
DB 안의 내용(최신 어노테이션 시각)과 sha1 로만 판단한다.
"""

from kindle import ksdk_staleness as S


def test_first_import_has_no_warning():
    r = S.check(prev=None, sha1="abc123", newest_added_date="2026-09-20 21:14:03")
    assert r.warnings == []


def test_first_import_records_sha1_and_date():
    r = S.check(prev=None, sha1="abc123", newest_added_date="2026-09-20 21:14:03",
               seen_at="2026-09-20T21:15:00")
    assert r.record == {
        "sha1": "abc123",
        "max_added_date": "2026-09-20 21:14:03",
        "seen_at": "2026-09-20T21:15:00",
    }


def test_same_sha1_warns_same_file_reused():
    prev = {"sha1": "abc123", "max_added_date": "2026-09-20 21:14:03",
            "seen_at": "2026-09-20T21:15:00"}
    r = S.check(prev, sha1="abc123", newest_added_date="2026-09-20 21:14:03")
    assert len(r.warnings) == 1
    assert "동일합니다" in r.warnings[0]
    assert "2026-09-20T21:15:00" in r.warnings[0]      # 지난 반입 시각을 알려준다


def test_different_sha1_newer_date_is_silent():
    """정상적인 새 반입 — 경고 없음."""
    prev = {"sha1": "abc123", "max_added_date": "2026-09-20 21:14:03"}
    r = S.check(prev, sha1="def456", newest_added_date="2026-09-21 08:00:00")
    assert r.warnings == []


def test_different_sha1_older_date_warns_regression():
    """다른 파일인데 내용이 더 오래됨 — 옛 사본을 가리킬 가능성."""
    prev = {"sha1": "abc123", "max_added_date": "2026-09-20 21:14:03"}
    r = S.check(prev, sha1="def456", newest_added_date="2026-09-19 14:11:00")
    assert len(r.warnings) == 1
    assert "오래됐습니다" in r.warnings[0]
    assert "2026-09-20 21:14:03" in r.warnings[0]      # 지난번
    assert "2026-09-19 14:11:00" in r.warnings[0]       # 이번


def test_regression_warning_mentions_device_ambiguity():
    """KSDK 에는 클리핑별 기기 정보가 없어 옛 사본인지 다른 기기인지 구분 못 한다."""
    prev = {"sha1": "abc123", "max_added_date": "2026-09-20 21:14:03"}
    r = S.check(prev, sha1="def456", newest_added_date="2026-09-19 14:11:00")
    assert "다른 기기" in r.warnings[0]


def test_regression_does_not_block_only_warns():
    """막지 않는다 — Scribe/Colorsoft 번갈아 반입은 정상적으로 역행한다.
    이 함수는 경고를 반환할 뿐 예외를 던지거나 record 생성을 거부하지 않는다."""
    prev = {"sha1": "abc123", "max_added_date": "2026-09-20 21:14:03"}
    r = S.check(prev, sha1="def456", newest_added_date="2026-09-19 14:11:00")
    assert r.record["sha1"] == "def456"
    assert r.record["max_added_date"] == "2026-09-19 14:11:00"


def test_same_sha1_check_runs_before_date_check():
    """같은 파일이면(sha1 일치) 재사용 경고만 뜨고, 날짜 역행 경고와 중복되지 않는다."""
    prev = {"sha1": "abc123", "max_added_date": "2026-09-20 21:14:03"}
    r = S.check(prev, sha1="abc123", newest_added_date="2026-09-20 21:14:03")
    assert len(r.warnings) == 1
    assert "동일합니다" in r.warnings[0]


def test_missing_dates_do_not_crash():
    """added_date 가 하나도 없는 DB (드문 경우) — None 비교로 죽으면 안 된다."""
    prev = {"sha1": "abc123", "max_added_date": None}
    r = S.check(prev, sha1="def456", newest_added_date=None)
    assert r.warnings == []
    assert r.record["max_added_date"] is None


def test_prev_without_max_added_date_key_is_tolerated():
    """옛 상태 파일(이 필드가 생기기 전)을 읽어도 죽지 않는다."""
    prev = {"sha1": "abc123"}   # max_added_date 키 자체가 없음
    r = S.check(prev, sha1="def456", newest_added_date="2026-09-20 21:14:03")
    assert r.warnings == []


def test_reset_clears_history_no_false_warning():
    """--reset 이후에는 prev 가 None 이 되어 경고가 안 뜬다."""
    r = S.check(prev=None, sha1="def456", newest_added_date="2019-01-01 00:00:00")
    assert r.warnings == []


def test_seen_at_defaults_to_now(monkeypatch):
    import datetime as _dt

    class _Fixed(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime(2026, 9, 20, 21, 15, 0)

    monkeypatch.setattr(S, "datetime", _Fixed)
    r = S.check(prev=None, sha1="abc", newest_added_date=None)
    assert r.record["seen_at"] == "2026-09-20T21:15:00"
