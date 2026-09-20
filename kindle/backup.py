"""덮어쓰기 전 자동 롤링 백업.

## 왜

2026-09-19 14:11 의 동기화가 `kindle_sync.json` 을 783KB(29권 1,951건) →
32KB(1권 157건) 로 덮어썼다. Time Machine 으로도 복구하지 못했다.

**그 실행은 정상 종료했다.** 크래시도 디스크 오류도 아니었다 — KFX 본문 읽기가
조용히 비었고(마운트가 `stat` 은 답하면서 읽기는 타임아웃), 코드가 그 결과를
성공으로 간주해 정상적으로 덮어썼다. 그래서 **백업 시점을 실패 처리에 묶으면
같은 사고를 못 막는다.** 여기서는 쓰기 직전에 무조건 뜬다.

설계 근거와 결정 이력은 `BACKUP_DESIGN.md`.

## 회전 — 최근 N개만으로는 부족하다

사고 후 N번 실행하는 것만으로 정상본이 밀려난다. 09-19 사고도 그랬을 것이다.
그래서 **최근 N세대에 더해 역대 최대 크기 1개**를 삭제 대상에서 뺀다. 크기는
완전성의 대리 지표일 뿐이지만, 클리핑 산출물이 단조 증가에 가까워 이 도메인에서는
잘 맞는다. 표식 파일은 쓰지 않는다 — 회전할 때마다 디렉터리에서 다시 계산한다.

## 끄는 경로는 좁다

`disable()` 은 CLI 의 `--no-backup` 만 부른다. **환경변수·설정파일·기본값으로는
꺼지지 않는다.** 방침의 요지가 "잊을 수 없게"인데 조용히 꺼질 수 있으면 같은
구멍이 다시 난다.
"""

from __future__ import annotations

import logging
import os
import shutil
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional

__all__ = [
    "DEFAULT_ROOT",
    "KEEP",
    "SHRINK_THRESHOLD",
    "disable",
    "is_enabled",
    "backup_dir",
    "snapshot",
    "check_shrink",
    "guard",
]

# 백업 루트를 하나로 유지한다 — 긴급분·탈옥 전 스냅샷·KSDK 회수본이 이미 여기 있다.
# 루트가 갈리면 나중에 한쪽만 보고 "백업이 없다"고 판단하게 된다.
DEFAULT_ROOT = Path.home() / "kindle_annotation_backup" / "rolling"

KEEP = 10
SHRINK_THRESHOLD = 0.5      # 직전 백업의 50% 미만으로 줄면 경고

logger = logging.getLogger(__name__)

_enabled = True


def disable(reason: str = "--no-backup") -> None:
    """백업을 끈다. **CLI 플래그에서만 부를 것.**

    환경변수나 설정파일에서 부르지 말 것 — 조용히 꺼지면 이 모듈이 막으려는
    사고가 그대로 재현된다. 끌 때마다 경고를 남긴다.
    """
    global _enabled
    _enabled = False
    logger.warning("자동 백업이 꺼졌습니다 (%s). "
                   "덮어쓰기 전 스냅샷이 만들어지지 않습니다.", reason)


def is_enabled() -> bool:
    return _enabled


def backup_dir(name: str, root: Optional[Path] = None) -> Path:
    """`<root>/<name>/`. `KINDLE_BACKUP_DIR` 로 루트를 재지정할 수 있다."""
    if root is None:
        env = os.environ.get("KINDLE_BACKUP_DIR", "").strip()
        root = Path(env).expanduser() if env else DEFAULT_ROOT
    return Path(root) / name


def _existing(d: Path) -> List[Path]:
    return sorted((p for p in d.glob("*") if p.is_file()),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def _rotate(d: Path, keep: int) -> None:
    """최근 `keep` 개 + 역대 최대 1개만 남기고 지운다."""
    files = _existing(d)
    if len(files) <= keep:
        return
    largest = max(files, key=lambda p: p.stat().st_size)
    for p in files[keep:]:
        if p == largest:
            continue                    # 사고본이 정상본을 밀어내지 못하게 한다
        try:
            p.unlink()
        except OSError as e:
            logger.warning("백업 회전 실패 (%s): %s", p, e)


def snapshot(path: Path, name: str, keep: int = KEEP,
             root: Optional[Path] = None) -> Optional[Path]:
    """`path` 가 있으면 백업하고 그 경로를 돌려준다. 없거나 꺼져 있으면 None.

    **예외를 던지지 않는다.** 백업 실패로 동기화가 죽으면 더 나쁘다.
    """
    if not _enabled:
        return None
    path = Path(path)
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return None                 # 첫 실행이거나 빈 파일 — 지킬 것이 없다
        d = backup_dir(name, root)
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = d / f"{stamp}{path.suffix}"
        n = 1
        while dest.exists():            # 같은 초에 두 번 도는 경우
            dest = d / f"{stamp}-{n}{path.suffix}"
            n += 1
        shutil.copy2(path, dest)
        _rotate(d, keep)
        logger.info("백업 %s → %s (%d bytes)", path.name, dest, dest.stat().st_size)
        return dest
    except Exception as e:              # 어떤 이유로든 저장을 막지 않는다
        logger.warning("백업 실패 (%s): %s", path, e)
        return None


def check_shrink(path: Path, prev: Optional[Path],
                 threshold: float = SHRINK_THRESHOLD) -> bool:
    """새로 쓴 파일이 직전 백업보다 크게 줄었으면 경고. 줄었으면 True.

    중단하지는 않는다 — `--book-exact` 로 한 권만 뽑는 등 정당한 축소가 있다.
    판단은 사람이 한다. 09-19 사고 때 이 한 줄만 있었어도 즉시 알아챘을 것이다.
    """
    if prev is None:
        return False
    try:
        new = Path(path).stat().st_size
        old = prev.stat().st_size
    except OSError:
        return False
    if old <= 0 or new >= old * threshold:
        return False
    logger.warning("%s 이 %s → %s bytes 로 줄었습니다 (%.0f%% 감소). 직전 백업: %s",
                   Path(path).name, f"{old:,}", f"{new:,}",
                   (1 - new / old) * 100, prev)
    return True


@contextmanager
def guard(path: Path, name: str, keep: int = KEEP,
          root: Optional[Path] = None) -> Iterator[Optional[Path]]:
    """덮어쓰기를 감싼다 — 앞에서 백업, 뒤에서 급감 검사.

        with backup.guard(path, "notion_state"):
            path.write_text(...)

    쓰기가 예외로 죽으면 급감 검사는 건너뛴다 (파일이 온전치 않을 수 있다).
    백업은 이미 떠 있으므로 옛 내용은 살아 있다.
    """
    prev = snapshot(path, name, keep=keep, root=root)
    yield prev
    check_shrink(path, prev)
