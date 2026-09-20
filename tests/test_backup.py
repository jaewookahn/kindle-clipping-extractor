"""kindle.backup — 덮어쓰기 전 자동 롤링 백업.

09-19 소실 사고의 재발 방지 장치다. 설계 근거는 BACKUP_DESIGN.md.
"""

import logging
import os
import time

import pytest

from kindle import backup


@pytest.fixture(autouse=True)
def _enabled_again():
    """테스트가 전역 스위치를 끈 채로 끝나지 않게 한다."""
    backup._enabled = True
    yield
    backup._enabled = True


@pytest.fixture
def root(tmp_path):
    return tmp_path / "rolling"


@pytest.fixture
def target(tmp_path):
    p = tmp_path / "sync.json"
    p.write_text("x" * 1000, encoding="utf-8")
    return p


def _write(p, n):
    p.write_text("y" * n, encoding="utf-8")


# --------------------------------------------------------------------------
# 기본 동작
# --------------------------------------------------------------------------

def test_snapshot_copies_file(target, root):
    b = backup.snapshot(target, "sync_output", root=root)
    assert b is not None and b.exists()
    assert b.read_text(encoding="utf-8") == target.read_text(encoding="utf-8")
    assert b.parent == root / "sync_output"


def test_snapshot_keeps_extension(target, root):
    assert backup.snapshot(target, "sync_output", root=root).suffix == ".json"


def test_missing_file_returns_none(tmp_path, root):
    assert backup.snapshot(tmp_path / "없음.json", "x", root=root) is None


def test_empty_file_is_not_backed_up(tmp_path, root):
    p = tmp_path / "empty.json"
    p.write_text("", encoding="utf-8")
    assert backup.snapshot(p, "x", root=root) is None


def test_same_second_does_not_collide(target, root):
    a = backup.snapshot(target, "s", root=root)
    b = backup.snapshot(target, "s", root=root)
    assert a != b and a.exists() and b.exists()


def test_snapshot_never_raises(tmp_path, root, monkeypatch):
    """백업 실패로 원래 저장이 죽으면 더 나쁘다."""
    p = tmp_path / "a.json"
    p.write_text("data", encoding="utf-8")
    monkeypatch.setattr(backup.shutil, "copy2",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("디스크 꽉 참")))
    assert backup.snapshot(p, "x", root=root) is None


# --------------------------------------------------------------------------
# 끄기 — 경로가 좁아야 한다
# --------------------------------------------------------------------------

def test_disable_stops_backups(target, root):
    backup.disable()
    assert backup.is_enabled() is False
    assert backup.snapshot(target, "x", root=root) is None


def test_env_var_cannot_disable(target, root, monkeypatch):
    """환경변수로는 꺼지지 않는다 — 조용히 꺼지면 같은 구멍이 다시 난다."""
    for var in ("KINDLE_NO_BACKUP", "NO_BACKUP", "KINDLE_BACKUP", "KINDLE_BACKUP_DISABLE"):
        monkeypatch.setenv(var, "1")
    assert backup.is_enabled() is True
    assert backup.snapshot(target, "x", root=root) is not None


def test_disable_logs_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="kindle.backup"):
        backup.disable()
    assert any("백업이 꺼졌" in r.message for r in caplog.records)


def test_backup_dir_env_override(monkeypatch, tmp_path):
    """위치는 환경변수로 옮길 수 있다 (끄는 것과 다르다)."""
    monkeypatch.setenv("KINDLE_BACKUP_DIR", str(tmp_path / "elsewhere"))
    assert backup.backup_dir("s") == tmp_path / "elsewhere" / "s"


def test_default_root_is_shared_backup_tree():
    """백업 루트가 갈리면 한쪽만 보고 '백업이 없다'고 판단하게 된다."""
    assert backup.DEFAULT_ROOT.parent.name == "kindle_annotation_backup"


# --------------------------------------------------------------------------
# 회전 — 사고본이 정상본을 밀어내면 안 된다
# --------------------------------------------------------------------------

def _aged(d, name, size, ago):
    p = d / name
    p.write_text("z" * size, encoding="utf-8")
    t = time.time() - ago
    os.utime(p, (t, t))
    return p


def test_rotation_keeps_recent(root):
    d = root / "s"
    d.mkdir(parents=True)
    for i in range(15):
        _aged(d, f"{i:02d}.json", 100, ago=1000 - i)
    backup._rotate(d, keep=10)
    assert len(list(d.glob("*.json"))) == 10


def test_rotation_preserves_largest_ever(root):
    """최근 N개만 두면 사고 후 N번 실행으로 정상본이 밀려난다."""
    d = root / "s"
    d.mkdir(parents=True)
    big = _aged(d, "정상본.json", 800_000, ago=9999)      # 가장 오래됐고 가장 크다
    for i in range(15):
        _aged(d, f"사고본{i:02d}.json", 32_000, ago=100 - i)
    backup._rotate(d, keep=10)
    assert big.exists(), "역대 최대본이 회전에 밀려났다"
    assert len(list(d.glob("*.json"))) == 11               # 최근 10 + 최대 1


def test_rotation_noop_when_under_limit(root):
    d = root / "s"
    d.mkdir(parents=True)
    for i in range(5):
        _aged(d, f"{i}.json", 10, ago=i)
    backup._rotate(d, keep=10)
    assert len(list(d.glob("*.json"))) == 5


def test_snapshot_rotates(target, root):
    for _ in range(12):
        backup.snapshot(target, "s", keep=3, root=root)
    # 최근 3 + 최대 1 (크기가 모두 같으면 최대는 그 중 하나라 3~4개)
    assert len(list((root / "s").glob("*.json"))) <= 4


# --------------------------------------------------------------------------
# 급감 경고
# --------------------------------------------------------------------------

def test_shrink_warns(target, root, caplog):
    prev = backup.snapshot(target, "s", root=root)      # 1000 bytes
    _write(target, 50)                                   # 95% 감소
    with caplog.at_level(logging.WARNING, logger="kindle.backup"):
        assert backup.check_shrink(target, prev) is True
    assert any("줄었습니다" in r.message for r in caplog.records)


def test_no_warn_when_growing(target, root):
    prev = backup.snapshot(target, "s", root=root)
    _write(target, 5000)
    assert backup.check_shrink(target, prev) is False


def test_no_warn_just_under_threshold(target, root):
    prev = backup.snapshot(target, "s", root=root)       # 1000
    _write(target, 600)                                  # 40% 감소 — 임계 미만
    assert backup.check_shrink(target, prev) is False


def test_shrink_without_previous_is_silent(target):
    assert backup.check_shrink(target, None) is False


def test_real_incident_ratio_is_caught(tmp_path, root):
    """09-19 실측: 783,142B → 32,408B (92% 감소)."""
    p = tmp_path / "kindle_sync.json"
    p.write_bytes(b"x" * 783_142)
    prev = backup.snapshot(p, "sync_output", root=root)
    p.write_bytes(b"x" * 32_408)
    assert backup.check_shrink(p, prev) is True


# --------------------------------------------------------------------------
# guard
# --------------------------------------------------------------------------

def test_guard_backs_up_then_writes(target, root):
    old = target.read_text(encoding="utf-8")
    with backup.guard(target, "s", root=root) as prev:
        _write(target, 20)
    assert prev is not None
    assert prev.read_text(encoding="utf-8") == old       # 옛 내용이 살아 있다
    assert len(target.read_text(encoding="utf-8")) == 20


def test_guard_on_first_run_has_no_previous(tmp_path, root):
    p = tmp_path / "new.json"
    with backup.guard(p, "s", root=root) as prev:
        p.write_text("처음", encoding="utf-8")
    assert prev is None and p.exists()


def test_guard_keeps_backup_when_write_raises(target, root):
    old = target.read_text(encoding="utf-8")
    with pytest.raises(RuntimeError):
        with backup.guard(target, "s", root=root) as prev:
            raise RuntimeError("쓰기 실패")
    saved = list((root / "s").glob("*.json"))
    assert len(saved) == 1 and saved[0].read_text(encoding="utf-8") == old
