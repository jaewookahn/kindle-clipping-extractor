"""MacDroid(File Provider) 낡은 캐시 탐지.

## 증상 두 가지

MacDroid 는 킨들을 `~/Library/CloudStorage/MacDroid-*/` 에 macOS File Provider 로
노출한다. 이 Provider 는 자기 DB 에 캐시된 값으로 답하는데, 그 값이 기기 현재
상태보다 뒤처지는 경우가 두 가지 확인됐다.

**(1) 디렉터리 목록이 비어 보인다**

    $ ls "바람의 그림자 2 ….sdr"
    assets                       # ← .yjr 없음. 실제로는 하이라이트 64개

`fileproviderctl evaluate` 로 물어보면 `childItemCount = 1` — Provider 가 자식이
1개라고 믿고 있다. materialize(다운로드) 문제가 아니라 **재열거** 문제다.

**(2) 파일 내용이 낡았다**

목록에는 `.yjr` 이 보이는데 그 내용이 옛 스냅샷이다. 실측 사례: `먼저 온 미래`
의 `.yjr` 은 6장까지(char offset 최대 106,804)만 담고 있었고 7~10장 하이라이트가
없었다. Provider 는 `isMostRecentVersionDownloaded = 1` 이라고 주장했다.

## 왜 코드로 못 고치나

동작하는 방법을 전부 실측으로 시험했다:

| 시도 | 결과 |
|---|---|
| `readdir(2)` / `getattrlistbulk(2)` / 이름 직접 `stat` | 셋 다 같은 낡은 값 |
| `NSFileCoordinator` (ForUploading · WithoutChanges · plain) | **무효** — 내용 그대로 |
| `NSFileProviderManager.evictItem` | **거부** `NSFileProviderErrorDomain -2001` |
| QuickLook · 파일 전체 read · atime 갱신 | 무효 |
| Finder 로 폴더 열기 | 디렉터리 목록만 고쳐짐. **파일 내용은 안 고쳐짐** |

`evictItem` 이 정답이지만 **제3자 앱은 남의 Provider 항목을 축출할 수 없다**
(macOS 권한). 그리고 Finder 를 자동으로 여는 코드는 사용자가 명시적으로 금지했다
(일괄 처리 때 창이 수십 번 떴다).

MTP 직접 읽기(`kindle.device.mtp_direct_session`)는 캐시를 아예 우회하지만
MacDroid 가 USB 인터페이스를 점유해 `libusb_claim_interface() = -3` 로 실패한다.
**MacDroid 를 종료해야만** 쓸 수 있다.

## 그래서 이 모듈이 하는 일

고치는 척하지 않는다. **탐지해서 알린다.** 사용자가 할 수 있는 조치는 `HINT` 에
있고, 그중 무엇이 통하는지는 상황에 따라 다르다.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Set

__all__ = [
    "HINT",
    "is_fileprovider_path",
    "looks_unmaterialized",
    "stale_report",
]

logger = logging.getLogger(__name__)

# 어노테이션 파일 확장자 — 이게 하나도 없으면 아직 열거가 안 됐다고 본다.
_ANNOTATION_EXTS = (".yjr", ".yjf", ".mbp")

HINT = (
    "MacDroid 가 낡은 캐시를 주고 있을 수 있습니다 (앱에서 고칠 수 없는 제약입니다). "
    "다음 중 하나를 해보세요:\n"
    "  1) Finder 에서 해당 .sdr 폴더를 한 번 엽니다 — 목록이 비어 보일 때 효과가 있습니다\n"
    "  2) Finder 에서 그 폴더를 우클릭 → MacDroid 의 캐시 비우기(Evict)\n"
    "  3) MacDroid 를 종료했다 다시 연결합니다 — 파일 내용까지 낡았을 때 필요합니다\n"
    "  대상: {path}"
)


def _cloud_storage_root() -> Path:
    """File Provider 마운트의 공통 접두사.

    모듈 상수로 굳히지 않는다 — import 시점에 HOME 이 고정되면 테스트에서
    갈아끼울 수 없고, 실행 중 HOME 이 바뀌는 경우도 못 따라간다.
    """
    return Path.home() / "Library" / "CloudStorage"


def is_fileprovider_path(path: Path) -> bool:
    """`~/Library/CloudStorage/` 아래(MacDroid·iCloud 등)인지."""
    try:
        Path(path).resolve().relative_to(_cloud_storage_root().resolve())
        return True
    except (ValueError, OSError):
        return False


def _entries(directory: Path) -> Set[str]:
    try:
        return {e.name for e in Path(directory).iterdir()}
    except OSError:
        return set()


def looks_unmaterialized(sdr: Path) -> bool:
    """`.sdr` 이 낡은 캐시를 보여주고 있을 가능성이 높은지.

    어노테이션 파일이 하나도 안 보이면 True. 진짜로 하이라이트가 없는 책도
    True 가 되므로(오탐) 단정하는 문구로 쓰면 안 된다 — 실측에서 29권 중 26권이
    오탐이었다. 놓치면 하이라이트가 통째로 안 보이므로 이쪽으로 치우치게 뒀다.
    """
    return not any(n.lower().endswith(_ANNOTATION_EXTS) for n in _entries(sdr))


def stale_report(books: List[dict]) -> List[dict]:
    """가려졌을 가능성이 있는 책 목록.

    books: `sync_kfx.list_kfx_books()` 결과(+title 보강 가능).
    Returns: 같은 dict 들 중 의심되는 것만. 판단은 호출부(사람)가 한다.
    """
    out = []
    for b in books:
        sdr = b.get("sdr")
        if sdr is None or not is_fileprovider_path(sdr):
            continue
        if b.get("yjr_count", 0) == 0 and looks_unmaterialized(sdr):
            out.append(b)
    return out
