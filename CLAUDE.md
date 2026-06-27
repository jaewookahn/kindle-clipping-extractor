# CLAUDE.md — Kindle Clipping Extractor

## 프로젝트 개요

Kindle 기기 클리핑을 파싱해 Notion에 동기화하는 파이썬 툴.  
두 개의 독립적인 소스 파이프라인이 하나의 Notion 데이터베이스로 합쳐진다.

---

## 패키지 구조

```
kindle/
├── models.py          — Clipping, APNXInfo 데이터클래스
├── ebook.py           — kfxlib 로딩(_kfxlib_context), KFX 텍스트·페이지 추출
├── device.py          — 킨들 마운트 자동 감지
├── scanner.py         — documents/ 스캔, KFX+YJR 쌍 탐색
├── exporters.py       — sync_export_* (CSV/Markdown/Text/JSON 출력)
├── notion_export.py   — Notion 동기화 (fingerprint·상태 파일·Notion API)
├── title_cache.py     — KFX 메타데이터(제목·저자) 디스크 캐시
└── parsers/
    ├── my_clippings.py — My Clippings.txt 파서, is_limit_exceeded()
    ├── yjr.py          — YJR/YJF 바이너리 파서 (TLV, char offset)
    ├── apnx.py         — APNX 페이지 인덱스 파서
    └── mbp.py          — MBP 어노테이션 파서

sync_kfx.py                  — 메인 워크플로 (KFX+YJR → Notion). --titles, --refresh-titles
tui.py                       — Textual TUI (책 목록 + 클리핑 미리보기 + sync 옵션 모달)
sync_clippings_to_notion.py  — 보충 워크플로 (My Clippings.txt → Notion)
parse_clippings.py           — 단일 파일/디렉터리 파싱 후 파일 출력
sync_clippings.py            — My Clippings.txt 증분 동기화 (파일 출력 전용)
recover_clippings.py         — 한도 초과 텍스트 KFX 복구
```

---

## 핵심 데이터 모델

```python
@dataclass
class Clipping:
    book_title: str
    author: str
    clip_type: str        # "highlight" | "note" | "bookmark" | "last_position"
    page: Optional[int]
    location_start: Optional[int]   # Kindle Location 번호
    location_end: Optional[int]
    added_date: Optional[str]       # "YYYY-MM-DD HH:MM:SS"
    content: str
    source_file: str
    recovered: bool       # True이면 KFX에서 한도 초과 텍스트 복구됨
```

---

## 두 파이프라인 비교

| | KFX+YJR (`sync_kfx.py`) | My Clippings.txt (`sync_clippings_to_notion.py`) |
|---|---|---|
| 트리거 | 킨들 연결 후 자동 | 수동 실행 |
| 텍스트 정확도 | 원문 그대로 (char offset) | 한도 초과 시 누락 |
| 메타데이터 | 페이지·Location 완전 | Location만, 저자 없을 수 있음 |
| 대상 | KFX/AZW3 포맷 | 모든 포맷 (구형 포함) |

---

## Notion 상태 파일

- 경로: `~/.kindle_notion_sync.json`
- 두 파이프라인이 **공유** → 소스 무관하게 중복 방지
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
  → fill_clipping_kindle_locations()  # Kindle Location 번호 변환
```

---

## 테스트

```bash
pytest tests/
```

fixture: `examples/gongsandangseoneon - *.sdr/` 실제 KFX 예제 사용.

---

## 의존성

`pip install -r requirements.txt` — 런타임/테스트 의존성 모두 포함.

- `notional` — Notion API 클라이언트
- `tqdm` — 진행 표시
- `requests` — Google Books API (표지 이미지)
- `pypdf` — kfxlib이 일부 KFX 변형에서 요구. 없으면 `extract_kfx_info failed: No module named 'pypdf'` 경고와 함께 본문·KL 맵이 비게 됨.
- Calibre + KFX Input 플러그인 — KFX 텍스트 추출 (선택, pip 외 별도 설치)
