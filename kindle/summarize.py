"""장별 상세 요약 — 하이라이트를 챕터 단위로 묶어 LLM 에 넘긴다.

## 무엇을 요약하나

**그 장에서 사용자가 하이라이트한 것들**이지, 책 원문이 아니다. 하이라이트가
없는 장은 아예 건너뛴다. 클리핑에는 `fill_clipping_chapters()` 가 붙여둔
`chapter` breadcrumb("1장 › 1.1 서론")이 이미 들어 있어 그대로 그룹핑 키로 쓴다.

## 왜 클리핑 "뒤"에 붙나

Notion 의 블록 append 는 **끝에만** 붙는다. 앞에 끼워 넣으려면 페이지 본문을
통째로 다시 써야 한다(`--rewrite-bodies`). 요약을 클리핑 뒤에 두면 그 제약을
피해서 기존 페이지에도 증분으로 붙일 수 있다.

## 제공자

DeepSeek (OpenAI 호환 `/chat/completions`). 프로젝트가 이미 `requests` 를 쓰므로
SDK 를 새로 넣지 않고 REST 를 직접 부른다. 키는 `.env` 의 `DEEPSEEK_API_KEY`.

## 캐시

`~/.kindle_summaries.json`. 키는 `책 제목 › 챕터` + **하이라이트 내용 해시**라
하이라이트가 추가/수정되면 자동으로 다시 요약한다. 챕터가 그대로면 재사용한다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import requests

from kindle import backup
from kindle.models import Clipping

DEFAULT_PATH = Path.home() / ".kindle_summaries.json"
_VERSION = 2   # 2: 요약문에 [n] 인용 표식 추가 (옛 요약은 표식이 없어 무효화)

_API_BASE = "https://api.deepseek.com"
_DEFAULT_MODEL = "deepseek-chat"
_ENV_KEY = "DEEPSEEK_API_KEY"

# 챕터 breadcrumb 이 없는 클리핑을 모아둘 이름.
_NO_CHAPTER = "(장 정보 없음)"

logger = logging.getLogger(__name__)


@dataclass
class ChapterSummary:
    """한 장의 요약 + 그 근거가 된 클리핑들.

    클리핑을 함께 들고 다녀야 요약 안의 [n] 과 인용 목록의 [n] 을 같은 순서로
    맞출 수 있다. 요약문만 넘기면 번호의 의미가 사라진다.
    """
    chapter: str
    summary: str
    clips: List[Clipping] = field(default_factory=list)

_SYSTEM_PROMPT = (
    "당신은 독서 노트를 정리하는 편집자입니다. 사용자가 전자책에서 직접 "
    "하이라이트한 문장들을 장(chapter) 단위로 받습니다.\n"
    "이 하이라이트들이 무엇을 말하는지 **상세하게** 정리하세요.\n\n"
    "규칙:\n"
    "- 한국어 존댓말로 씁니다.\n"
    "- 하이라이트에 실제로 담긴 내용만 씁니다. 책의 다른 부분을 추측하거나 "
    "지어내지 마세요.\n"
    "- 여러 하이라이트를 관통하는 논지가 있으면 그것을 중심으로 엮고, "
    "서로 무관하면 무리해서 하나로 묶지 마세요.\n"
    "- 3~6문장 정도의 문단으로 쓰되, 갈래가 뚜렷하면 '- ' 목록을 써도 됩니다.\n"
    "- 인용부호로 원문을 그대로 반복하지 마세요. 요약해서 설명하세요.\n"
    "- **각 문장 끝에 근거가 된 하이라이트 번호를 [1] 또는 [2][5] 처럼 답니다.**\n"
    "  번호는 입력에 붙은 것을 그대로 쓰고, 없는 번호를 지어내지 마세요.\n"
    "- 제목이나 머리말 없이 본문만 출력하세요."
)


# --------------------------------------------------------------------------
# 캐시
# --------------------------------------------------------------------------

def load_cache(path: Path = DEFAULT_PATH) -> dict:
    """캐시 로드. 없거나 손상되면 빈 캐시."""
    if not Path(path).exists():
        return {"version": _VERSION, "chapters": {}}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("요약 캐시를 읽지 못해 새로 시작합니다 (%s): %s", path, e)
        return {"version": _VERSION, "chapters": {}}
    if not isinstance(data, dict) or data.get("version") != _VERSION:
        return {"version": _VERSION, "chapters": {}}
    data.setdefault("chapters", {})
    return data


def save_cache(path: Path = DEFAULT_PATH, cache: Optional[dict] = None) -> None:
    if cache is None:
        return
    try:
        with backup.guard(Path(path), "summaries"):
            Path(path).write_text(
                json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("요약 캐시를 저장하지 못했습니다 (%s): %s", path, e)


def _content_digest(clips: Sequence[Clipping]) -> str:
    """하이라이트 내용의 해시 — 내용이 바뀌면 캐시가 무효가 되게 한다."""
    h = hashlib.sha1()
    for c in clips:
        h.update((c.content or "").encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _cache_key(book_title: str, chapter: str) -> str:
    return f"{book_title}›{chapter}"


# --------------------------------------------------------------------------
# 그룹핑
# --------------------------------------------------------------------------

def group_by_chapter(clips: Iterable[Clipping]) -> List[Tuple[str, List[Clipping]]]:
    """하이라이트를 챕터 breadcrumb 으로 묶는다. 등장 순서를 유지한다.

    북마크는 보여줄 본문이 없으므로 제외한다.
    """
    groups: Dict[str, List[Clipping]] = {}
    for c in clips:
        if c.clip_type == "bookmark":
            continue
        if not (c.content or "").strip():
            continue
        groups.setdefault(c.chapter or _NO_CHAPTER, []).append(c)
    return list(groups.items())


def _clip_text(c: Clipping) -> str:
    """색상 접두사('[yellow] ')를 떼고 본문만."""
    t = (c.content or "").strip()
    if t.startswith("[") and "] " in t:
        t = t.split("] ", 1)[1]
    return t.strip()


def _citable(clips: Sequence[Clipping]) -> List[Clipping]:
    """인용 번호를 붙일 대상 — 본문이 있는 것만, 입력 순서 그대로.

    프롬프트의 [n] 과 인용 목록의 [n] 이 **같은 리스트**에서 나와야 어긋나지
    않는다. 두 곳에서 따로 거르면 번호가 밀린다.
    """
    return [c for c in clips if _clip_text(c)]


def _cite_location(c: Clipping) -> str:
    """'p57 · Loc 881–885' — 있는 정보만."""
    parts = []
    if c.page is not None:
        parts.append(f"p{c.page}")
    if c.location_start is not None:
        loc = str(c.location_start)
        if c.location_end is not None and c.location_end != c.location_start:
            loc += f"–{c.location_end}"
        parts.append(f"Loc {loc}")
    return " · ".join(parts)


def format_citations(clips: Sequence[Clipping], max_chars: int = 220) -> str:
    """요약 뒤에 붙일 인용 목록. 번호는 프롬프트에 준 것과 1:1 대응한다."""
    items = _citable(clips)
    if not items:
        return ""
    lines = ["  인용:"]
    for i, c in enumerate(items, 1):
        text = _clip_text(c)
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "…"
        where = _cite_location(c)
        head = f"  [{i}] {where}" if where else f"  [{i}]"
        lines.append(f"{head} — “{text}”")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# DeepSeek 호출
# --------------------------------------------------------------------------

class SummarizerError(RuntimeError):
    pass


def _post_chat(api_key: str, model: str, messages: list,
               timeout: int, max_retries: int) -> str:
    url = f"{_API_BASE}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}",
               "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "stream": False}
    delay = 1.0
    for attempt in range(max_retries + 1):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as e:
            if attempt == max_retries:
                raise SummarizerError(f"DeepSeek 요청 실패: {e}") from e
            time.sleep(min(delay, 30.0)); delay *= 2
            continue
        if r.status_code == 429 or r.status_code >= 500:
            if attempt == max_retries:
                raise SummarizerError(f"DeepSeek {r.status_code}: {r.text[:200]}")
            wait = float(r.headers.get("Retry-After", delay)) + random.uniform(0, 0.3)
            time.sleep(min(wait, 30.0)); delay = min(delay * 2, 30.0)
            continue
        if r.status_code >= 400:
            raise SummarizerError(f"DeepSeek {r.status_code}: {r.text[:200]}")
        try:
            return r.json()["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, ValueError) as e:
            raise SummarizerError(f"DeepSeek 응답 형식이 예상과 다릅니다: {e}") from e
    raise SummarizerError("DeepSeek 재시도 소진")   # 도달 불가


def summarize_chapter(clips: Sequence[Clipping], book_title: str, chapter: str,
                      api_key: str, model: str = _DEFAULT_MODEL,
                      timeout: int = 120, max_retries: int = 3) -> str:
    lines = []
    for i, c in enumerate(_citable(clips), 1):
        lines.append(f"[{i}] {_clip_text(c)}")
    user = (f"책: {book_title}\n장: {chapter}\n"
            f"하이라이트 {len(lines)}개:\n\n" + "\n".join(lines))
    return _post_chat(api_key, model,
                      [{"role": "system", "content": _SYSTEM_PROMPT},
                       {"role": "user", "content": user}],
                      timeout, max_retries)


def summarize_book(clips: Sequence[Clipping], book_title: str,
                   api_key: Optional[str] = None,
                   cache: Optional[dict] = None,
                   model: str = _DEFAULT_MODEL,
                   min_clips: int = 1,
                   progress=None) -> List[ChapterSummary]:
    """책 하나의 장별 요약. `[ChapterSummary, …]` 를 챕터 등장 순서로 돌려준다.

    api_key: 없으면 `DEEPSEEK_API_KEY` 환경변수. 그것도 없으면 SummarizerError.
    cache:   `load_cache()` 결과를 넘기면 재사용하고 새 요약을 채워 넣는다
             (저장은 호출부가 `save_cache()` 로).
    min_clips: 하이라이트가 이 개수 미만인 장은 건너뛴다.
    progress: `progress(done, total, chapter)` 콜백.
    """
    key = api_key or os.environ.get(_ENV_KEY)
    groups = [(ch, cs) for ch, cs in group_by_chapter(clips) if len(cs) >= min_clips]
    if not groups:
        return []

    chapters_cache = (cache or {}).get("chapters", {})
    out: List[ChapterSummary] = []
    for i, (chapter, cs) in enumerate(groups, 1):
        if progress is not None:
            progress(i, len(groups), chapter)
        ck = _cache_key(book_title, chapter)
        digest = _content_digest(cs)
        hit = chapters_cache.get(ck)
        if hit and hit.get("digest") == digest and hit.get("summary"):
            out.append(ChapterSummary(chapter, hit["summary"], list(cs)))
            continue
        if not key:
            raise SummarizerError(
                f"{_ENV_KEY} 가 없습니다 — .env 에 DeepSeek API 키를 넣으세요.")
        text = summarize_chapter(cs, book_title, chapter, key, model=model)
        out.append(ChapterSummary(chapter, text, list(cs)))
        if cache is not None:
            cache.setdefault("chapters", {})[ck] = {
                "digest": digest, "summary": text,
                "model": model, "clips": len(cs),
            }
    return out


# --------------------------------------------------------------------------
# 출력 포맷
# --------------------------------------------------------------------------

def format_chapter_summaries(summaries: Sequence[ChapterSummary],
                             model: str = _DEFAULT_MODEL,
                             with_citations: bool = True) -> str:
    """Notion 본문(클리핑 뒤)에 붙일 텍스트. 빈 입력이면 빈 문자열.

    요약문 안의 [n] 은 바로 아래 인용 목록의 [n] 을 가리킨다 — 페이지·Location
    이 함께 나오므로 어느 하이라이트에서 나온 말인지 되짚을 수 있다.
    """
    if not summaries:
        return ""
    parts = [f"\n\n───────── 장별 요약 ({model}) ─────────\n"]
    for cs in summaries:
        parts.append(f"\n▪ {cs.chapter}\n{cs.summary}\n")
        if with_citations:
            cites = format_citations(cs.clips)
            if cites:
                parts.append(cites + "\n")
    return "".join(parts)
