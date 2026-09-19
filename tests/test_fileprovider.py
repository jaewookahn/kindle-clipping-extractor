"""kindle.fileprovider — MacDroid File Provider 지연 열거 우회.

기기가 없어도 도는 테스트만 둔다. 실제 재열거(Finder/NSFileCoordinator)는
마운트된 MacDroid 가 있어야 하므로 여기서 검증하지 않는다.
"""

from pathlib import Path

import pytest

from kindle import fileprovider as fp


def test_is_fileprovider_path_true(tmp_path, monkeypatch):
    cloud = tmp_path / "Library" / "CloudStorage"
    (cloud / "MacDroid-Kindle" / "Internal Storage").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert fp.is_fileprovider_path(cloud / "MacDroid-Kindle" / "Internal Storage")


def test_is_fileprovider_path_false(tmp_path, monkeypatch):
    (tmp_path / "Library" / "CloudStorage").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert not fp.is_fileprovider_path(tmp_path / "elsewhere")


def test_looks_unmaterialized_detects_missing_annotations(tmp_path):
    sdr = tmp_path / "book.sdr"
    (sdr / "assets").mkdir(parents=True)
    # assets 만 있으면 아직 열거가 안 된 것으로 본다
    assert fp.looks_unmaterialized(sdr)

    (sdr / "bookHASH.yjr").write_bytes(b"")
    assert not fp.looks_unmaterialized(sdr)


def test_looks_unmaterialized_accepts_yjf_and_mbp(tmp_path):
    for ext in (".yjf", ".mbp"):
        sdr = tmp_path / f"b{ext}.sdr"
        sdr.mkdir()
        (sdr / f"x{ext}").write_bytes(b"")
        assert not fp.looks_unmaterialized(sdr)


def test_looks_unmaterialized_on_unreadable_dir(tmp_path):
    # 존재하지 않는 경로도 예외 없이 True (= 어노테이션 안 보임)
    assert fp.looks_unmaterialized(tmp_path / "nope")


def test_stale_report_flags_only_suspects(tmp_path, monkeypatch):
    cloud = tmp_path / "Library" / "CloudStorage" / "MacDroid-K"
    hidden = cloud / "hidden.sdr"; (hidden / "assets").mkdir(parents=True)
    ok = cloud / "ok.sdr"; ok.mkdir()
    (ok / "x.yjr").write_bytes(b"")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    books = [
        {"stem": "hidden", "sdr": hidden, "yjr_count": 0},
        {"stem": "ok", "sdr": ok, "yjr_count": 1},
        {"stem": "nosdr", "sdr": None, "yjr_count": 0},
    ]
    out = fp.stale_report(books)
    assert [b["stem"] for b in out] == ["hidden"]


def test_stale_report_ignores_non_fileprovider(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    sdr = tmp_path / "plain.sdr"; sdr.mkdir()
    assert fp.stale_report([{"stem": "p", "sdr": sdr, "yjr_count": 0}]) == []


def test_module_makes_no_false_promises():
    """고치는 척하는 API 가 되살아나면 실패한다 — 전부 실측으로 무효 확인됨."""
    assert not hasattr(fp, "materialize")
    assert "subprocess" not in dir(fp)


def test_hint_mentions_path_and_options():
    msg = fp.HINT.format(path="/x/y.sdr")
    assert "/x/y.sdr" in msg
    assert "MacDroid" in msg
