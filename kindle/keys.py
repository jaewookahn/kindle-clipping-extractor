"""`book_key` / `clip_key` — 네 소스를 잇는 식별자.

사양 정본: `~/prj/reading_manager/DATA_MODEL.md` §7
JS 정본:  `~/prj/highlight-capture/src/utils/{bookKey,clipKey}.js`

## 왜 필요한가

지금 이 저장소의 dedup 은 `SHA-1(title|type|location_start|location_end)` 인데,
`location_*` 이 **파이프라인 위치에 따라 의미가 바뀐다.** `sync_kfx` 는 비싼 KFX
추출 전에 걸러야 해서 PRE-KL char offset 을 쓰고, `My Clippings.txt` 파서는
처음부터 POST-KL Location 을 넣는다. 그래서 같은 하이라이트라도

    SHA1("책|highlight|5391|17951")  vs  SHA1("책|highlight|113|358")

로 절대 안 맞는다 — **소스가 다르면 중복 제거가 통째로 안 된다.** 종이책(줄줄이)
까지 합치면 좌표가 아예 없는 소스가 하나 더 는다.

## 키 설계 이력 — 본문만으로는 부족했다

최초 판(2026-09-20)은 20자 이상이면 **좌표를 빼고 내용만** 해싱했다 — 판본·
좌표계·소스가 달라도 같은 문장이면 같은 키가 되도록. 그런데 이러면 **같은
문장을 책의 여러 곳에서 하이라이트했을 때 구분이 안 된다** (같은 날 다른
페이지에서 같은 명언을 두 번 밑줄 그은 경우 등). 사용자 결정으로 **좌표를
신원에 도로 넣었다** — Location 이 갈릴 정도면 그건 키 설계가 아니라 판본
관리 문제로 본다.

**`loc_end` 는 키에 넣지 않는다.** 실측(`LOC_END_CHECK.md`) 결과 킨들 내부
두 PRE-KL 소스(KSDK↔YJR)는 1,487/1,487 완전 일치했지만, POST-KL 변환본과
`My Clippings.txt` 사이에서는 3,421쌍 중 35건(1.0%)이 어긋났다 — 무작위가
아니라 **하이라이트가 마침표로 끝나는 경계**에 94% 가 쏠렸고 값도 -1/-2 로
갈려 상수 보정도 안 된다. 게다가 넣어서 얻는 게 없다 — `loc_start` 가 같은데
`loc_end` 만 다르면 그건 길이가 다르다는 뜻이라 `content_norm` 이 이미
다르므로 구분은 `loc_end` 없이도 된다. 위험만 있고 이득이 없어 뺐다.
`loc_end` 자체는 `Location End` 속성과 `Positions` 에 계속 보존한다 — 키
계산에만 안 쓴다.

이 개정으로 **다섯 갈래가 둘로 줄었다**: 20자 임계(과거 ①②③ 분기), 북마크
특례(과거 ④ — `loc_end` 를 키에서 뺐으니 본문 없는 클리핑도 그냥
`content_norm=""` 인 같은 식으로 흡수된다), 절단 특례(과거 ⑤ — 복구 성공
이면 본문이 같아져 자동으로 원문과 한 키, 실패하면 본문이 달라 자동으로
다른 키가 되어 "절대 병합 금지"를 따로 명시할 필요가 없다)가 전부 사라졌다.

## 이번 구현은 JS 를 보지 않고 사양만으로 한다

지난 번(색상태그 제거)까지는 "JS 를 그대로 옮긴다"는 원칙이었지만, 이번
개정은 **반대로 지시됐다** — 사양(§7)만 보고 독립적으로 구현한 뒤 공유
벡터로 대조한다. JS 를 베끼면 JS 의 오해까지 그대로 옮겨 와 벡터 검증이
"같은 걸 두 번 확인"하는 셈이 되기 때문이다. 독립 구현 둘이 같은 벡터에서
같은 결론에 도달해야 진짜 검증이다. **어긋나면 어느 쪽이 틀렸는지 리딩총괄에
올린다 — 이쪽에서 맞추지 않는다.**

`normalize_for_key`·`normalize_content`(색상태그 패턴 포함)·`book_key` 는
이 원칙이 생기기 전에 이미 JS 실행 결과·전수조사·공유 벡터 세 번으로 독립
검증됐으므로 그대로 둔다. 이번에 사양만 보고 새로 쓰는 것은 `compute_clip_key`
뿐이다.

## 좌표계 주의

`loc_start` 는 **POST-KL Location** 이어야 한다. `fill_clipping_kindle_locations()`
이전의 PRE-KL char offset 을 넣으면 `My Clippings.txt` 쪽과 갈라진다.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

__all__ = [
    "normalize_for_key",
    "normalize_content",
    "strip_color_tag",
    "book_key",
    "clip_key",
    "compute_clip_key",
    "ClipKey",
]

# 킨들 하이라이트 본문은 "[yellow] 실제 문장" 처럼 색상 접두사가 붙는다
# (`kindle/ksdk.py`, `parsers/yjr.py`). 색을 바꿨다고 다른 클리핑이 되면 안 된다.
#
# 처음엔 사양이 준 4색(yellow·blue·pink·orange) 화이트리스트로 구현했으나
# **실측이 그 목록을 뒤집었다** (COLOR_CENSUS.json, 2026-09-20 전수 조사):
#
#     yellow 8,346 · dark_blue 1,632 · pink 134 · blue 12 · green 4 · orange 4
#
# `dark_blue` 가 두 번째로 흔한데 목록에 없었다. 근본 원인은 화이트리스트
# 자체가 틀린 접근이라는 것 — `kindle/ksdk.py` 가 `mchl_color` 값을 그대로
# 조립해 태그를 만들고(`f"[{color}] "`), `parse_yjr()` 도 페이로드의 색상
# 문자열을 그대로 읽는다. **어느 파서도 고정 어휘를 쓰지 않으므로** 새 색이
# 생기면(펌웨어 업데이트·다른 기기 종류) 화이트리스트는 계속 샌다.
#
# 그래서 **패턴으로 뗀다** — 태그는 우리 파서가 만든 것이라 모양(대괄호+영문
# 단어)을 안다. 선행 위치에서 **한 번만**, 알파벳으로 시작하는 토큰만 문다.
# "[1]"·"[2]" 같은 숫자 각주는 숫자로 시작해 안 걸리고, 본문 중간 대괄호는
# 애초에 `^` 앵커라 안 걸린다.
#
# `[sic]` 처럼 본문이 실제로 그 모양이면 잘못 벗겨질 수 있다 — 그래도 이쪽이
# 맞다. `content_norm` 은 해시 입력일 뿐 저장되는 본문은 원문 그대로다. 양쪽
# (파이썬·JS) 이 똑같이 과하게 벗기면 키는 일치해 손해가 없고, 덜 벗기면
# 소스별로 키가 갈린다 — 위험이 비대칭이다.
_COLOR_TAG = re.compile(r"^\[[A-Za-z][A-Za-z_]*\]\s?")

_PAREN_NOTE = re.compile(r"[（(][^）)]*[）)]")
# JS: n.split(/[:：–—-]/)[0] — 하이픈도 분리자다. 이상해 보여도 그대로 둔다
# (저자 토큰 정렬 작업에 묶인 보류 사안 — DATA_MODEL.md §7).
_SUBTITLE_SEP = re.compile(r"[:：–—-]")
_NON_ISBN = re.compile(r"[^0-9Xx]")


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def normalize_for_key(s: Optional[str]) -> str:
    """제목·저자 정규화. NFKC → 소문자 → 괄호 주석 제거 → 부제 분리자 이후 절단 → 공백 제거."""
    if not s:
        return ""
    n = unicodedata.normalize("NFKC", s).lower()
    n = _PAREN_NOTE.sub("", n)
    n = _SUBTITLE_SEP.split(n)[0]
    n = re.sub(r"\s+", "", n)
    return n.strip()


def strip_color_tag(s: Optional[str]) -> str:
    """앞머리 색상 태그 하나를 뗀다 (`"[yellow] 본문"` → `"본문"`)."""
    if not s:
        return ""
    return _COLOR_TAG.sub("", s, count=1)


def normalize_content(s: Optional[str]) -> str:
    """본문 정규화 — 색상태그 제거 → NFKC → 소문자 → 공백·문장부호 제거 (사양 §7)."""
    if not s:
        return ""
    n = unicodedata.normalize("NFKC", strip_color_tag(s)).lower()
    return "".join(
        ch for ch in n
        if not (ch.isspace() or unicodedata.category(ch)[0] in ("P", "S"))
    )


def book_key(title: str = "", author: str = "",
             isbn: str = "", asin: str = "") -> str:
    """`isbn:` → `asin:` → `t:` 순.

    ⚠️ KSDK 의 `book_data.asin` 은 **대부분 진짜 ASIN 이 아니다** (사이드로드
    98.5%, 기기 생성 32자 내부 ID). 종이책 ISBN 과 잇는 별칭 키로 쓰면 안 되므로
    그 값을 이 함수의 `asin` 으로 넘기지 말 것 (`kindle/ksdk.py` 상단 참조).
    """
    norm_isbn = _NON_ISBN.sub("", isbn or "").upper()
    if norm_isbn:
        return f"isbn:{norm_isbn}"
    a = (asin or "").strip()
    if a:
        return f"asin:{a}"
    norm = f"{normalize_for_key(title)}|{normalize_for_key(author)}"
    return f"t:{_sha1(norm)[:16]}"


@dataclass(frozen=True)
class ClipKey:
    """`key` 가 None 이면 키를 만들 수 없다 — Needs Review 로 격리한다."""
    key: Optional[str]
    branch: str                 # "kindle" | "paper"
    needs_review: bool = False


def compute_clip_key(
    bk: str,
    clip_type: str = "highlight",
    content: str = "",
    page: Optional[int] = None,
    loc_start: Optional[int] = None,
    source: str = "kindle",          # "kindle" | "paper"
    truncated: bool = False,         # 한도 절단 & 복구 실패 — Needs Review 표시용
) -> ClipKey:
    """사양 §7 (2026-09-20 개정, 두 갈래).

        킨들    sha1(book_key | type | loc_start | content_norm)   ※ POST-KL
        종이책  sha1(book_key | type | page | content_norm)

    좌표(`loc_start`/`page`)가 없으면 키를 만들지 않고 Needs Review 로
    격리한다 — 본문만으로는 "같은 문장을 여러 곳에서 하이라이트"를
    구분할 수 없어서다. `loc_end` 는 갈래 어디에도 들어가지 않는다
    (모듈 docstring "loc_end 는 키에 넣지 않는다" 참조) — 호출부가
    `Location End` 속성·`Positions` 보존에만 별도로 쓴다.

    북마크(본문 없음)도 특례가 아니다 — `content` 를 안 주면 `content_norm`
    이 자연히 빈 문자열이 되어 `sha1(book_key|bookmark|loc_start|"")` 로
    좌표만으로 유일해진다.

    `truncated=True`(한도 절단 후 복구 실패)는 해시 공식을 바꾸지 않는다.
    잘린 본문 자체가 `content_norm` 을 원문과 다르게 만들어 자동으로 다른
    키가 되므로, "원문과 병합 금지"를 따로 코드로 강제할 필요가 없다 —
    `needs_review` 플래그로 사람이 살펴보라는 표시만 남긴다.
    """
    bk = bk or ""
    norm = normalize_content(content)

    if source == "paper":
        if page is None:
            return ClipKey(None, "paper", needs_review=True)
        key = _sha1(f"{bk}|{clip_type}|{page}|{norm}")
        return ClipKey(key, "paper", needs_review=truncated)

    if loc_start is None:
        return ClipKey(None, "kindle", needs_review=True)
    key = _sha1(f"{bk}|{clip_type}|{loc_start}|{norm}")
    return ClipKey(key, "kindle", needs_review=truncated)


def clip_key(*args, **kwargs) -> Optional[str]:
    """`compute_clip_key()` 의 키만. 격리 여부까지 보려면 그쪽을 쓸 것."""
    return compute_clip_key(*args, **kwargs).key
