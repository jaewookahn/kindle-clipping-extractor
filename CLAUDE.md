# CLAUDE.md — Kindle Clipping Extractor

## 프로젝트 개요

Kindle 기기 클리핑을 파싱해 Notion에 동기화하는 파이썬 툴.  
두 개의 독립적인 소스 파이프라인이 하나의 Notion 데이터베이스로 합쳐진다.

---

## 패키지 구조

```
kindle/
├── models.py          — Clipping, Chapter, APNXInfo 데이터클래스
├── ebook.py           — kfxlib 로딩(_kfxlib_context), KFX 텍스트·페이지·TOC 추출
├── device.py          — 킨들 마운트 자동 감지 + libmtp ctypes MTP 백엔드
├── scanner.py         — 디렉터리 재귀 스캔 → 모든 파서 디스패치 (scan_path)
├── exporters.py       — export_* (parse_clippings용) / sync_export_* (sync용)
├── notion_export.py   — Notion 동기화 (_NotionAPI·fingerprint·상태 파일)
├── title_cache.py     — KFX 메타데이터(제목·저자) 디스크 캐시
├── cli.py             — parse_clippings.py 의 실제 구현 (argparse + 파싱 흐름)
├── __main__.py        — `python -m kindle` 진입점 → cli.main()
└── parsers/
    ├── __init__.py     — 파서 4종 re-export
    ├── my_clippings.py — My Clippings.txt 파서, is_limit_exceeded()
    ├── yjr.py          — YJR/YJF 바이너리 파서 (TLV, char offset)
    ├── apnx.py         — APNX 페이지 인덱스 파서
    └── mbp.py          — MBP 어노테이션 파서

sync_kfx.py                  — 메인 워크플로 (KFX+YJR → Notion). --titles, --refresh-titles
tui.py                       — Textual TUI (책 목록 + 클리핑 미리보기 + sync 옵션 모달)
sync_clippings_to_notion.py  — 보충 워크플로 (My Clippings.txt → Notion)
parse_clippings.py           — 단일 파일/디렉터리 파싱 후 파일 출력 (kindle.cli 래퍼)
sync_clippings.py            — My Clippings.txt 증분 동기화 (파일 출력 전용, Notion 없음)
recover_clippings.py         — 한도 초과 텍스트 KFX 복구
notion_create_db.py          — 필요한 스키마로 Notion DB 생성 (raw REST)
notion_refresh_covers.py     — 기존 Notion 페이지의 표지만 일괄 재검색·교체

tests/  — pytest. fixture 는 examples/ 의 실제 KFX 사용
```

**진입점은 8개** (`*.py` 루트). 그중 `.env` 를 읽는 것은 5개 —
`sync_kfx`, `tui`, `sync_clippings_to_notion`, `notion_create_db`, `notion_refresh_covers`.

---

## 핵심 데이터 모델

```python
@dataclass
class Clipping:
    book_title: str = ""
    author: str = ""
    clip_type: str = ""             # "highlight" | "note" | "bookmark" | "last_position"
    page: Optional[int] = None
    location_start: Optional[int] = None   # ⚠️ 좌표계 주의 (아래)
    location_end: Optional[int] = None
    added_date: Optional[str] = None
    content: str = ""               # 색상은 "[yellow] 본문" 처럼 접두사로 붙음
    source_file: str = ""
    recovered: bool = False         # True이면 KFX에서 한도 초과 텍스트 복구됨
    chapter: Optional[str] = None   # TOC breadcrumb ("1장 › 1.1 서론"), KFX 전용
```

- **`location_start/end`** — 파이프라인 위치에 따라 의미가 바뀐다.
  YJR 파싱 직후에는 **KFX char offset**, `fill_clipping_kindle_locations()` 이후에는
  **Kindle Location 번호**. `My Clippings.txt` 파서는 처음부터 Location 번호를 넣는다.
  이 차이가 fingerprint 문제의 원인 → "Notion 상태 파일" 절 참조.
- **`added_date`** — KFX+YJR 경로만 `"YYYY-MM-DD HH:MM:SS"` 로 정규화된다.
  `My Clippings.txt` 는 로캘 문자열이 그대로 들어간다 (미해결 이슈 3번).

---

## 두 파이프라인 비교

| | KFX+YJR (`sync_kfx.py`) | My Clippings.txt (`sync_clippings_to_notion.py`) |
|---|---|---|
| 트리거 | 킨들 연결 후 자동 | 수동 실행 |
| 텍스트 정확도 | 원문 그대로 (char offset) | 한도 초과 시 누락 |
| 메타데이터 | 페이지·Location 완전 | Location만, 저자 없을 수 있음 |
| 챕터·목차 | 지원 (KFX TOC) | 없음 |
| 날짜 | `"YYYY-MM-DD HH:MM:SS"` 정규화 | 로캘 문자열 그대로 (미해결) |
| 대상 | KFX/AZW3 포맷 | 모든 포맷 (구형 포함) |

---

## Notion 상태 파일

- 경로: `~/.kindle_notion_sync.json`
- 두 파이프라인이 **공유** (단, 아래 "좌표계" 주의 — 현재 교차 소스 dedup 은 미작동)
- fingerprint = `SHA-1(book_title|clip_type|location_start|location_end)`

```json
{
  "books": {
    "책제목": {
      "notion_page_id": "abc...",
      "synced_fingerprints": ["sha1hash", ...]
    }
  }
}
```

`kindle/notion_export.py` 의 `fingerprint()`, `load_state()`, `save_state()` 가 관리한다.

### ⚠️ fingerprint 좌표계 (PRE-KL vs POST-KL)

`location_start/end` 가 **어느 시점의 값이냐**에 따라 완전히 다른 해시가 나온다.

```
YJR 파싱 직후   location_start = 5391   (KFX char offset)   ← PRE-KL
KL 변환 후      location_start = 113    (Kindle Location)   ← POST-KL
```

`sync_kfx` 는 비싼 KFX 추출 **전에** 신규 항목을 걸러야 하므로 `seen_keys` 에
PRE-KL 을 쓴다 (KL 번호는 추출 후에야 나온다). `sync_to_notion(clip_fps=…)` 로
그 값을 그대로 넘겨 Notion 상태 파일도 PRE-KL 로 맞춘다.

**`clip_fps` 는 `clippings` 와 위치가 1:1 대응해야 한다.** 클리핑만 정렬하면
짝이 어긋나 엉뚱한 fingerprint 가 저장되고 dedup 이 통째로 깨진다. 반드시 함께
정렬할 것 (`sync_kfx.py` 의 `_paired`). `sync_to_notion` 에 길이 불일치 가드가 있다.

**미해결**: `My Clippings.txt` 의 location 은 애초에 POST-KL 이라, 같은 하이라이트라도
`SHA1("책|highlight|5391|17951")` vs `SHA1("책|highlight|113|358")` 로 절대 안 맞는다.
→ 상태 파일을 공유해도 **소스가 다르면 중복 제거가 안 된다.** 책 제목도 소스마다
달라서(KFX 메타데이터 vs My Clippings 제목 줄) 안정적인 `book_id` 가 선결 과제.
자세한 내용은 `DEVLOG.md` 7절.

`--rewrite-bodies` (sync_kfx) / `sync_to_notion(rewrite=True)` 는 dedup 을 무시하고
각 책의 Notion 페이지 본문을 통째로 지운 뒤 다시 쓴다 (`_rewrite_page_body`).
챕터 정보 등 포맷 변경을 기존 페이지에 백필할 때 사용. fingerprint·표지·속성은 보존.

---

## Notion 클라이언트 (`_NotionAPI`)

`kindle/notion_export.py`. `requests.Session` 기반이며 **notional 은 쓰지 않는다**.

- **429**: `Retry-After` 헤더를 그대로 존중. **5xx**: 지수 백오프 + 지터.
  Notion 은 integration 당 평균 3 req/s. `--rewrite-bodies` 는 블록을 하나씩
  지우므로 권당 수백 요청이 나가 재시도가 없으면 중간에 끊긴다.
- 4xx(429 제외)는 재시도하지 않고 즉시 반환한다.

**title 속성은 `rich_text` 필터로 매칭되지 않는다** (조회가 늘 비어 같은 책의
페이지가 중복 생성됨):

```python
{"filter": {"property": "Title", "title": {"equals": title}}}   # ✅
```

블록 payload 는 직접 만든다. 본문은 `_split_chunks()` 로 2000자 이하로 쪼갠다
(Notion rich_text 한도). `cut <= 0` 판정 필수 — `max_len` 구간 첫 문자가 `\n` 이면
`rfind` 가 0 을 반환해 무한 루프에 빠진다.

`_update_page_properties(highlight_count=None)` 이면 `Highlights` 를 건드리지 않는다.
증분 sync 는 "새 클리핑" 수만 알아서, 그대로 쓰면 기존 누적 카운트를 덮어쓴다.

---

## 표지 캐시

- 이미지 파일 캐시(TUI): `~/.cache/kindle_covers/`
  - KFX 내장 표지: `kfx_<sha1(path|mtime|size)>.jpg`
  - 외부 다운로드: `<sha1(url)>.jpg`
- URL 캐시: `~/.kindle_cover_cache.json` — `(title|author) → url`, hit 만 디스크 저장
- **TUI 우선순위**: ① KFX 내장 표지(`extract_kfx_cover` → `get_cover_image_data`,
  고해상도·오프라인·정확) → ② 알라딘 `cover500` → `cover200` → Yes24 → Google Books
- **Notion 표지**: 외부 **URL** 방식(`ExternalFile[url]`, `_get_cover_url`).
  이미지 바이트 업로드가 아니라 알라딘 등 공개 URL 참조. KFX 내장 표지는
  호스팅 URL 이 없어 Notion 에는 못 씀 (TUI 전용)
  - 계획: Notion File Upload API 로 내장 표지 업로드 → 커버 지정 (README 향후 계획)
- 표지 렌더러: tmux 안=HalfcellImage, Ghostty/Kitty/WezTerm=TGPImage,
  그 외 AutoImage. `KINDLE_TUI_IMAGE` 로 강제 가능 (`ClippingPreview._image_widget_cls`)

---

## 제목 캐시

- 경로: `~/.kindle_kfx_titles.json`
- kfxlib 로 추출한 제목·저자를 절대 경로 키로 보관
- 무효화: 파일의 `mtime` 또는 `size` 가 캐시와 다르면 자동 재추출
- `sync_kfx.py --titles` (list 표시) 및 `process_book` (sync 시 메타데이터) 양쪽에서 공유
- 강제 재추출: `--refresh-titles`

```python
# kindle/title_cache.py
load_cache(path) -> dict
get_or_extract(cache, kfx_path, extractor, refresh=False) -> {"title", "author"}
save_cache(path, cache)
```

---

## kfxlib 로딩 방식

`kindle/ebook.py` 의 `_kfxlib_context()` 컨텍스트 매니저 사용.  
Calibre KFX Input 플러그인 ZIP에서 `kfxlib/` 폴더를 임시 디렉터리에 추출한 뒤  
`sys.path` 에 추가하고, 종료 시 제거·삭제한다.

플러그인 경로 탐색 순서 (`_KFX_PLUGIN_PATHS`):
1. `~/Library/Preferences/calibre/plugins/KFX Input.zip` (macOS)
2. `~/.config/calibre/plugins/KFX Input.zip` (Linux)

플러그인 없으면 텍스트·페이지 추출을 건너뛰고 나머지는 정상 동작.

---

## YJR 파싱 파이프라인 순서 (변경 금지)

`kindle/parsers/yjr.py` → `kindle/ebook.py` 호출 순서가 고정되어 있다.  
순서를 바꾸면 char offset이 잘못된 위치를 가리킨다.

```
parse_yjr()                  # char offset 기반 클리핑 위치 추출
  → fill_clipping_text()     # char offset으로 원문 슬라이싱
  → fill_clipping_pages()    # APNX 페이지 번호 매핑
  → fill_clipping_chapters() # TOC breadcrumb 매핑 (KFX $389 nav → $212)
  → fill_clipping_kindle_locations()  # Kindle Location 번호 변환
```

`fill_clipping_chapters()` 도 raw char offset 을 쓰므로 반드시
`fill_clipping_kindle_locations()` **이전**에 호출한다 (페이지 매핑과 동일 제약).
`extract_kfx_info()` 는 `(page_map, kl_offsets, book_text, toc)` 4-tuple 을 돌려준다.
`toc` 는 `[(char_offset, breadcrumb), …]` — breadcrumb 은 중첩 챕터를 ` › ` 로 연결.

`build_chapter_ranges(toc, page_map, kl_offsets, text_len)` 도 **KL 변환 전**에
호출한다 (page_map·kl_offsets 와 좌표계를 맞춰야 함).

---

## 챕터 범위 (Chapter)

`kindle/models.py` 의 `Chapter` — 각 TOC 항목이 차지하는 페이지·Location 범위.

```python
Chapter(title="제1장 › 1. 부르주아와 프롤레타리아",  # breadcrumb
        char_start=5391, char_end=17951,
        page_start=13, page_end=32,
        location_start=113, location_end=358)
c.level  # 중첩 깊이 (breadcrumb 의 " › " 개수)
c.leaf   # breadcrumb 마지막 조각
```

**챕터 끝 = "다음에 오는, 자기 자손이 아닌 TOC 항목의 시작"**.
KFX 는 부모 챕터와 첫 자식이 같은 char offset 을 가리키는 경우가 많아,
단순히 "다음 항목까지"로 자르면 부모 범위가 길이 0 이 된다.

출력 위치 (모두 클리핑 **앞**):
- Notion 본문 — `format_chapter_outline()`. **신규 페이지 생성 시와
  `--rewrite-bodies` 때만** 반영 (Notion append 는 끝에만 붙는다)
- JSON (`sync_export_json_grouped`) — 책 dict 의 `"chapters"` 키
- Markdown (`sync_export_markdown`) — `<details>` 접이식 목차

`--no-chapter-outline` 으로 끈다.

---

## 테스트

```bash
pytest tests/
```

fixture: `examples/gongsandangseoneon - *.sdr/` 실제 KFX 예제 사용.

**이 환경에서는 플러그인 자동로드를 꺼야 한다** — conda 의 `anyio` pytest 플러그인이
설치된 pytest 와 버전이 안 맞아 collection 단계에서 `ModuleNotFoundError:
_pytest.scope` 로 죽는다. 프로젝트 코드 문제가 아니다:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/ -q -p no:warnings
```

| 파일 | 대상 |
|---|---|
| `test_yjr.py` | YJR TLV 파싱 |
| `test_my_clippings.py` | My Clippings.txt 파싱 |
| `test_title_cache.py` | 제목 캐시 무효화 |
| `test_chapters.py` | `fill_clipping_chapters` breadcrumb 매핑 |
| `test_chapter_ranges.py` | `build_chapter_ranges` 페이지·Location 범위 |
| `test_notion_sync.py` | fingerprint 저장·dedup·카운트, `_split_chunks`, 재시도 |

`test_notion_sync.py` 는 네트워크를 타지 않는다 — Notion 호출부를 monkeypatch 한다.

---

## 알려진 미해결 이슈

우선순위 순. 손대기 전에 `DEVLOG.md` 6~8절을 함께 볼 것.

1. **교차 소스 dedup 미작동** — 위 "fingerprint 좌표계" 참조. 안정적인
   `book_id`(ASIN 우선, 정규화된 `제목+저자` 폴백)가 선결 과제.
2. **`Highlights` 카운트 고착** — 증분 sync 에서 갱신하지 않으므로(덮어쓰기
   방지) 값이 오래된 채 남는다. 기존 값을 읽어 더하는 방식이 필요.
3. **`My Clippings` 날짜 미정규화** — 파서가 로캘 문자열
   (`"Monday, January 1, 2024 10:00:00 AM"`)을 그대로 담는다. `models.Clipping`
   주석은 `"YYYY-MM-DD HH:MM:SS"` 라고 돼 있어 서로 안 맞는다. 그 결과
   `Last Highlighted` 가 이 소스에서는 절대 채워지지 않고,
   `max(added_date)` 도 사전순 비교라 무의미하다. 파서에서 정규화가 정답.
4. **`_META_RE` 가 영문 로캘 전용** — 한글 킨들의 "페이지/위치" 표기 미지원.
5. **LZPC 페이지네이션 캐시 미해독** — `DEVLOG.md` 5절.

---

## 실험적 기능

**챕터 목차** (`--no-chapter-outline` 으로 끔). 없어도 나머지는 그대로 동작한다.
제거할 경우 볼 구성 요소 목록은 README "챕터 목차" 절에 있다.
개별 클리핑에 챕터명을 붙이는 `fill_clipping_chapters()` 는 이 기능과 **별개로**
원래 있던 것이라 함께 지우면 안 된다.

---

## 의존성

`pip install -r requirements.txt` — 런타임/테스트 의존성 모두 포함.

- `python-dotenv` — `.env` 로드. 진입점 5개(`sync_kfx`, `tui`,
  `sync_clippings_to_notion`, `notion_create_db`, `notion_refresh_covers`)가
  import 직후 `load_dotenv()` 호출. `NOTION_TOKEN`·`NOTION_DB` 를 여기서 읽는다.
  우선순위: CLI 인자 > 셸 환경변수 > `.env`. 템플릿은 `.env.example`.
- `tqdm` — 진행 표시
- `requests` — Notion REST API 호출 + 표지 이미지 조회.
  Notion 클라이언트는 `kindle/notion_export.py` 의 `_NotionAPI` (429/5xx 백오프 재시도).
  **`notional` 은 제거됨** — pydantic v1 (`pydantic.main.ModelMetaclass`) 에 고정돼
  pydantic 2 환경에서 import 자체가 실패했다. 다시 추가하지 말 것.
- `pypdf` — kfxlib이 일부 KFX 변형에서 요구. 없으면 `extract_kfx_info failed: No module named 'pypdf'` 경고와 함께 본문·KL 맵이 비게 됨.
- Calibre + KFX Input 플러그인 — KFX 텍스트 추출 (선택, pip 외 별도 설치)
