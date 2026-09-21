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

해법은 좌표 대신 **본문**으로 잇는 것이다. 20자 이상이면 내용만 해시하므로
판본·좌표계·소스가 달라도 같은 문장은 같은 키가 된다.

## JS 와 한 글자도 다르면 안 된다

같은 클리핑이 파이썬과 JS 에서 다른 키를 얻으면 통합 DB 에서 갈라진다. 그래서
**JS 동작을 그대로 옮긴다 — 개선하지 않는다.** 눈에 거슬리는 부분이 있어도
(예: `normalize_for_key` 가 하이픈에서 제목을 자르는 것 — 보류 중인 저자 토큰
정렬 작업에 묶여 있다) JS 를 먼저 고치고 함께 바꿔야 한다. 공유 검증 벡터는
`~/prj/reading_manager/fixtures/clip_key_vectors.json`.

**예외 둘 — 사양이 구현보다 먼저 옳았던 경우** (`DATA_MODEL.md` §7,
2026-09-20 명시, 커밋 `b877420`). 최초 이식 때 JS `clipKey.js` 를 그대로
옮겼더니 벡터 생성 과정에서 둘 다 사양과 어긋난 것이 드러났다 — 그래서
**여기서는 사양대로 고쳤고, `줄줄이`가 같은 규칙으로 JS 를 맞추는 중이다**:

1. **색상태그 제거는 `content_norm` 의 첫 단계다.** 원래 JS 에는 이 단계가
   없어 `"[yellow] 안녕"` 이 `"yellow안녕"` 이 됐다 — 대괄호만 문장부호로
   빠지고 색상 이름이 본문에 섞여 들었다. 색상 접두는 소스마다 다르므로
   (킨들 갈래는 접두, 종이책은 없음) 남겨 두면 "본문으로 신원을 잡는다"는
   설계가 소스별로 다른 키를 내며 무너진다. **화이트리스트가 아니라
   패턴으로 뗀다** — 처음엔 4색(yellow·blue·pink·orange) 목록이었지만
   실측(`COLOR_CENSUS.json`)이 `dark_blue`·`green` 을 더 찾아내 뒤집혔다.
   목록은 파서가 고정 어휘를 안 쓰는 한 계속 샌다. 상세 근거는 아래
   `_COLOR_TAG` 주석 참조 — 이 패턴은 JS 와 반드시 같아야 한다
2. **비어 있는 자리는 빈 문자열이다.** `loc_end=None` 등을 해시 입력에 넣을 때
   `"None"`/`"null"` 문자열이 아니라 `""` 다.

## 좌표계 주의

②④⑤ 갈래는 **POST-KL Location** 을 받는다. `fill_clipping_kindle_locations()`
이전의 PRE-KL char offset 을 넣으면 `My Clippings.txt` 쪽과 또 갈라진다.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

__all__ = [
    "SHORT_TEXT_THRESHOLD",
    "normalize_for_key",
    "normalize_content",
    "strip_color_tag",
    "book_key",
    "clip_key",
    "compute_clip_key",
    "ClipKey",
]

SHORT_TEXT_THRESHOLD = 20

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
# 생기면(펌웨어 업데이트·다른 기기) 화이트리스트는 계속 샌다.
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
# JS: n.split(/[:：–—-]/)[0] — 하이픈도 분리자다. 이상해 보여도 그대로 둔다.
_SUBTITLE_SEP = re.compile(r"[:：–—-]")
_NON_ISBN = re.compile(r"[^0-9Xx]")


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def normalize_for_key(s: Optional[str]) -> str:
    """제목·저자 정규화. JS `normalizeForKey` 와 동일.

    NFKC → 소문자 → 괄호 주석 제거 → 부제 분리자 이후 절단 → 공백 제거.
    """
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
    """본문 정규화. JS `normalizeContent` + 색상 태그 제거 (사양 §7).

    JS 는 `/[\\s\\p{P}\\p{S}]/gu` 를 쓴다. 파이썬 `re` 에는 `\\p{...}` 가 없어
    유니코드 카테고리로 같은 집합을 만든다 — P(구두점)·S(기호)·공백.
    """
    if not s:
        return ""
    n = unicodedata.normalize("NFKC", strip_color_tag(s)).lower()
    return "".join(
        ch for ch in n
        if not (ch.isspace() or unicodedata.category(ch)[0] in ("P", "S"))
    )


def book_key(title: str = "", author: str = "",
             isbn: str = "", asin: str = "") -> str:
    """`isbn:` → `asin:` → `t:` 순. JS `computeBookKey` 와 동일.

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
    branch: str                 # "1".."5"
    needs_review: bool = False


def _join(*parts) -> str:
    """JS 와 같은 방식으로 잇는다 — None 은 빈 문자열 (`page != null ? … : ''`)."""
    return "|".join("" if p is None else str(p) for p in parts)


def compute_clip_key(
    bk: str,
    clip_type: str = "highlight",
    content: str = "",
    page: Optional[int] = None,
    loc_start: Optional[int] = None,
    loc_end: Optional[int] = None,
    source: str = "kindle",          # "kindle" | "paper"
    truncated: bool = False,         # 한도 절단 & 복구 실패
) -> ClipKey:
    """사양 §7 의 5갈래. `loc_*` 은 **POST-KL Location** 이어야 한다."""
    bk = bk or ""

    # ④ 북마크 — 본문이 없다. loc_end 는 소스가 무엇을 주든 None 으로 정규화한다.
    #    KSDK 는 end == start 로 채우고 parse_yjr() 은 None 을 둔다. 맞추지 않으면
    #    이미 동기화한 북마크가 전부 신규로 오인된다 (실측 12건).
    if clip_type == "bookmark":
        if loc_start is None:
            return ClipKey(None, "4", needs_review=True)
        return ClipKey(_sha1(_join(bk, "bookmark", loc_start, None)), "4")

    norm = normalize_content(content)

    # ⑤ 절단 & 복구 실패 — ② 와 같은 식이지만 ① 과 절대 병합하지 않는다.
    if truncated:
        return ClipKey(_sha1(_join(bk, clip_type, loc_start, loc_end)),
                       "5", needs_review=True)

    # ① 내용만으로 유일 — 소스·판본·좌표계를 가로질러 이어지는 유일한 경로
    if len(norm) >= SHORT_TEXT_THRESHOLD:
        return ClipKey(_sha1(_join(bk, clip_type, norm)), "1")

    # ③ 종이책 — Location 이 없다. page 가 유일한 좌표
    if source == "paper":
        return ClipKey(_sha1(_join(bk, clip_type, page, norm)), "3")

    # ② 킨들 짧은 본문 — 내용만으로는 충돌한다 (2자 이하가 기기별 12~25%)
    return ClipKey(_sha1(_join(bk, clip_type, loc_start, loc_end)), "2")


def clip_key(*args, **kwargs) -> Optional[str]:
    """`compute_clip_key()` 의 키만. 격리 여부까지 보려면 그쪽을 쓸 것."""
    return compute_clip_key(*args, **kwargs).key
