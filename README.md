# Kindle Clipping Extractor

Kindle 기기의 하이라이트·메모·북마크를 여러 형식에서 파싱해 JSON / CSV / Markdown / 텍스트로 내보내거나 Notion 데이터베이스에 직접 동기화하는 파이썬 스크립트.

---

## 전체 워크플로우

```
킨들 USB 연결
       │
       ▼
┌──────────────────────────────────────────────────────┐
│  소스 선택                                            │
│                                                      │
│  ① KFX + YJR (권장)        ② My Clippings.txt        │
│     .kfx + .sdr/*.yjr          (구형 포맷 / 삭제된   │
│     ↓                           전자책 보충용)        │
│     정확한 원문 복원              ↓                   │
│     한도 초과 없음               한도 초과 시 누락    │
└──────┬───────────────────────────┬───────────────────┘
       │                           │
       ▼                           ▼
  sync_kfx.py              sync_clippings_to_notion.py
  (자동 실행)                    (수동 보충)
       │                           │
       └──────────┬────────────────┘
                  ▼
        ~/.kindle_notion_sync.json
        (공유 상태 파일 — fingerprint 중복 방지)
                  │
                  ▼
           Notion 데이터베이스
```

---

## 스크립트 구성

| 스크립트 | 실행 방식 | 용도 |
|----------|-----------|------|
| `tui.py` | 대화형 | Textual TUI — 책 탐색·정렬·필터·표지/클리핑 미리보기·동기화 |
| `sync_kfx.py` | 킨들 연결 후 자동 | KFX+YJR → Notion (메인 워크플로) |
| `sync_clippings_to_notion.py` | 수동 보충 | My Clippings.txt → Notion |
| `parse_clippings.py` | 단독 실행 | 단일 파일/디렉터리 파싱 후 파일 출력 |
| `sync_clippings.py` | 단독 실행 | My Clippings.txt 증분 동기화 (파일 출력 전용) |
| `recover_clippings.py` | 단독 실행 | My Clippings.txt 한도 초과 텍스트 복구 |
| `notion_create_db.py` | 1회 실행 | sync_kfx 가 기대하는 스키마로 Notion 데이터베이스 자동 생성 |
| `notion_refresh_covers.py` | 필요 시 | 기존 Notion 페이지 표지를 알라딘 표지로 일괄 교체(백필) |

---

## 대화형 TUI (`tui.py`)

`sync_kfx.py` 의 모든 기능을 키보드로 다룰 수 있는 Textual TUI. Catppuccin Frappé
(밝은 다크) 팔레트로 256-color 터미널에서 보기 좋게 렌더링된다.

```bash
python tui.py                                # 자동 감지
python tui.py --kindle "<경로>"              # 경로 직접 지정
```

### 구성

- **상단**: 그라데이션 로고 + 현재 키 힌트
- **Status Bar**: 현재 디바이스 / 책 개수 / 클리핑 책수 / Notion 업로드 책수 / last sync
- **Books Table**: 책 목록 — `제목 / 저자 / 포맷 / YJR / Notion / 최종수정`
- **Footer**: 키바인딩 표시

### 키바인딩

| 키 | 동작 |
|---|---|
| `↑/↓` | 책 이동 |
| `/` | 제목·저자·파일명 필터 (Esc 로 해제) |
| `Enter` | 클리핑 미리보기 모달 (표지 + 클리핑 표) |
| `s` | 동기화 옵션 모달 (dry-run / 파일 저장 / Notion 업로드 / reset) |
| `k` | Kindle 기기 변경 (여러 후보 감지 시) |
| `r` | 현재 필터 책의 제목 강제 재추출 (캐시 무효화) |
| `1`–`6` | 컬럼별 정렬 (제목/저자/포맷/YJR/Notion/최종수정), 같은 키 두 번 = 방향 토글 |
| `0` | 원래(stem) 정렬 |
| `q` | 종료 |

### 클리핑 미리보기

- 좌측 32 칸: **책 표지** — KFX 파일에 내장된 정품 표지(고해상도·오프라인) 우선,
  없으면 외부 검색(알라딘 → Yes24 → Google Books) 폴백
- 우측: 클리핑 표 (`# / 타입 / 색 / 페이지 / 위치 / 날짜 / 챕터 / 내용`)
- 타입 아이콘: 하이라이트 `✎`(노랑) / 노트 `✐`(초록) / 위치 `➤`(보라) / 북마크 `🔖`
- 색상: yellow/blue/pink/orange 등 배경색 chip 으로 시각화
- **챕터**: KFX 목차에서 추출한 breadcrumb (예: `제1장 › 1. 서론`)
- 북마크는 의미 없는 단일-문자 텍스트 대신 `—` 표시
- 내용은 CJK 셀 너비를 인식해 자동 워드랩 (`w` 로 토글)
- 클리핑 표도 `1`–`7` 로 컬럼 정렬 (`7` = 챕터)
- **검색**: `/` 로 검색창 열어 내용·챕터·타입·페이지 즉시 필터, `Esc` 로 해제

### 표지 렌더링 (터미널 호환성)

터미널에 따라 렌더러를 자동 선택한다. 그래픽 프로토콜 지원 여부가 화질을 좌우:

| 환경 | 렌더러 | 화질 |
|---|---|---|
| Ghostty / Kitty / WezTerm (tmux 밖) | Kitty 그래픽(TGP) | 선명 |
| tmux·screen 안 | half-block(컬러 문자) | 저화질 — 멀티플렉서가 그래픽 escape 차단 |
| 기타 | AutoImage 자동 탐지 | 터미널에 따름 |

- **tmux 안에서도 선명하게** 하려면: tmux `set -g allow-passthrough on` 후
  `KINDLE_TUI_IMAGE=tgp python tui.py` 로 강제 (또는 tmux 밖에서 실행)
- `KINDLE_TUI_IMAGE` 값: `tgp` / `sixel` / `halfcell` / `unicode` / `auto`
- 표지 우선순위: **① KFX 내장 표지**(`get_cover_image_data`, 1500×2200급 고해상도)
  → ② 알라딘 cover500 → cover200. 모두 `~/.cache/kindle_covers/` 에 캐싱돼
  두 번째 열기부터 즉시 표시. 외부 URL 은 `~/.kindle_cover_cache.json` 에 캐싱

### 동기화 모달

- 옵션을 목적별 3개 섹션으로 그룹화:
  - **출력 대상**: 파일 저장(경로 지정) / Notion 업로드
  - **실행 모드**: 미리보기(dry-run) / 챕터 정보 다시 쓰기(`--rewrite-bodies`)
  - **상태 초기화 ⚠**: 로컬(`--reset`) / Notion(`--reset-notion`)
- 필터 적용 중이면 그 책들만 대상 (`--book` 으로 전달), 아니면 전체
- `sync_kfx.py --no-progress` 를 subprocess 로 호출, stdout 라인 스트리밍 (책별 색 구분)
- **실행 중**: 스피너 + "동기화 중…" 표시, `실행` 버튼 비활성 + `중단` 버튼으로 취소
- Notion 업로드 사용 시 `NOTION_TOKEN` + `NOTION_DB` 환경변수 필요

### 제목 캐시

KFX 메타데이터 추출은 kfxlib 호출이 권당 수백 ms 들기 때문에, 한 번 추출한 결과를
**절대 경로 → (제목, 저자, mtime, size)** 로 `~/.kindle_kfx_titles.json` 에 저장한다.
파일이 갱신되거나 교체되면 mtime/size 가 바뀌어 자동 무효화.

`sync_kfx.py --titles` 와 TUI 양쪽에서 공유 → 한 번 추출하면 이후엔 즉시.

---

## Notion 초기 설정

### 1. Integration 만들고 `NOTION_TOKEN` 얻기

1. https://www.notion.so/profile/integrations 접속
2. **New integration** 클릭 → 이름(예: `kindle-clipping`), 워크스페이스 선택 → Save
3. **Internal Integration Secret** 복사 — `secret_…` 또는 `ntn_…` 로 시작
4. 이 값이 `NOTION_TOKEN`

### 2. 데이터베이스 만들고 `NOTION_DB` 얻기

#### 방법 A — 스크립트로 자동 생성 (권장)

부모 페이지(아무 빈 페이지)만 만들고 거기에 integration 권한을 주면 끝:

1. Notion 에 빈 페이지 하나 만듦 (예: "Kindle")
2. 그 페이지 우상단 `…` → `+ Add connections` → 위에서 만든 integration 추가
3. 부모 페이지 URL 의 32자 hex 부분 복사
4. 실행:
   ```bash
   export NOTION_TOKEN=ntn_xxx
   python notion_create_db.py --parent <부모페이지ID-또는-URL통째로>
   # → export NOTION_DB=<생성된DB_ID> 한 줄 출력
   ```
5. 출력된 `export NOTION_DB=…` 줄을 그대로 실행하면 끝

또는 한 줄로:
```bash
export NOTION_DB=$(python notion_create_db.py --parent <부모페이지ID> --quiet)
```

스키마(아래 표)는 자동으로 들어가니 사용자가 컬럼 만들 필요 없음.

#### 방법 B — 수동 생성

빈 페이지에서 `+ New database` (Full page 추천). 아래 속성을 정확히 같은 이름으로 추가:

| 속성 이름 | 타입 |
|---|---|
| Title | 제목 (기본 생성됨) |
| Author | 텍스트 |
| Highlights | 숫자 |
| Last Highlighted | 날짜 |
| Last Synced | 날짜 |

만든 후 **데이터베이스 페이지 우상단 `…` → `+ Add connections` → 위에서 만든 integration 추가**. 이 단계 빼먹으면 API 가 권한 거부.

DB ID 는 URL 에서:

```
https://www.notion.so/<workspace>/<dbname>-1a2b3c4d5e6f7890abcdef1234567890?v=...
                                            └─────────── DB_ID ────────────┘
```

`?v=...` 뒷부분은 view ID 라 잘라낼 것. 하이픈은 있어도/없어도 됨.

### 3. 환경변수 등록

```bash
# 한 번만
export NOTION_TOKEN=ntn_xxxxxxxxxxxxxxxxxx
export NOTION_DB=1a2b3c4d5e6f7890abcdef1234567890

# 영구 — ~/.zshrc 또는 ~/.bashrc 에 추가
echo 'export NOTION_TOKEN=ntn_xxx' >> ~/.zshrc
echo 'export NOTION_DB=1a2b3c4d...' >> ~/.zshrc
```

두 변수가 잡히면 TUI 의 동기화 모달이 "Notion 업로드" 를 자동 ON 으로 시작.

### 자주 막히는 곳

- **데이터베이스에 integration Connect 안 함** → `Object not found` / 권한 오류. 각 DB 마다 integration 권한 부여 필요
- **일반 페이지 vs 데이터베이스 페이지 혼동** — `+ New page` 가 아닌 `+ New database` 사용 (full-page DB 가 가장 안전)
- **URL 의 `?v=...` 같이 복사** → view ID 가 섞임. 32자 hex 부분만 떼어내야 함

설정 확인:

```bash
python sync_kfx.py --book <짧은stem> --notion-db $NOTION_DB --dry-run
```

---

## 메인 워크플로: KFX+YJR → Notion

### 파이프라인

```
킨들 마운트 감지
      │
      ▼
documents/ 스캔
      │  .kfx 파일마다 짝꿍 .sdr/*.yjr 탐색
      ▼
YJR 파싱 (char offset 기반 클리핑 위치)
      │
      ▼
fingerprint 비교 → 신규 항목만 선별
      │
      ▼
KFX에서 원문·페이지·챕터(목차)·Kindle Location 추출
      │  (kfxlib — Calibre KFX Input 플러그인 필요)
      ▼
파일 출력 (선택)  +  Notion 업로드 (선택)
      │
      ▼
상태 파일 업데이트 (~/.kindle_notion_sync.json)
```

### 실행

```bash
# 환경변수 설정 (한 번만)
export NOTION_TOKEN=secret_xxx
export NOTION_DB=your_database_id

# 동기화
python sync_kfx.py --notion-db $NOTION_DB
```

### 옵션

```
--notion-token TOKEN    Notion 통합 토큰 (NOTION_TOKEN 환경변수 대체 가능)
--notion-db DB_ID       Notion 데이터베이스 ID
--notion-state FILE     상태 파일 경로 (기본값: ~/.kindle_notion_sync.json)
--no-cover              책 표지 추가 안 함
-o FILE                 Notion과 별개로 파일에도 저장
-f FORMAT               출력 형식: json | csv | markdown | text
--dry-run               저장 없이 신규 항목 목록만 출력
--reset                 로컬 상태 초기화 후 전체 재동기화
--reset-notion          Notion 상태 파일도 비움 (페이지 중복 주의)
--rewrite-bodies        이미 동기화된 책도 Notion 페이지 본문을 통째로 다시 씀
                        (챕터 정보 등 포맷 변경 백필용; fingerprint·표지·속성 보존)
--list-books            documents/ 의 KFX 책 목록 + 클리핑 상태 출력
--titles                --list-books 에 KFX 메타데이터로 실제 제목·저자 표시
--refresh-titles        --titles 캐시 무시하고 강제 재추출
--title-cache FILE      제목·저자 캐시 경로 (기본값: ~/.kindle_kfx_titles.json)
--book PATTERN          특정 책만 처리 (stem substring, 여러 번 지정 가능)
--no-progress           tqdm 진행 바 끄고 책당 1줄 print (TUI·로그 캡처용)
--kindle PATH           킨들 마운트 경로 직접 지정 (생략 시 자동 감지)
--log FILE              로그 파일 경로 (기본값: kindle_sync.log)
```

---

## 보충 워크플로: My Clippings.txt → Notion

KFX+YJR 파이프라인이 처리하지 못한 책(구형 포맷, 기기에서 삭제된 전자책 등)을 수동으로 보충합니다.

### 언제 필요한가

```
KFX+YJR 있음? ──Yes──→ sync_kfx.py 로 처리됨 (끝)
      │
      No
      │
      ▼
My Clippings.txt에 있음? ──Yes──→ sync_clippings_to_notion.py 로 보충
      │
      No
      │
      ▼
   해당 클리핑 복구 불가
```

### 실행

```bash
python sync_clippings_to_notion.py "My Clippings.txt" --notion-db $NOTION_DB

# 특정 책만
python sync_clippings_to_notion.py "My Clippings.txt" --notion-db $NOTION_DB \
    --book "채식주의자"

# KFX가 있으면 한도 초과 텍스트 복구 후 업로드
python sync_clippings_to_notion.py "My Clippings.txt" --notion-db $NOTION_DB \
    --kfx "book.kfx"

# 현재 동기화 현황 확인
python sync_clippings_to_notion.py "My Clippings.txt" --stats

# 업로드 없이 신규 항목 미리 보기
python sync_clippings_to_notion.py "My Clippings.txt" --notion-db $NOTION_DB \
    --dry-run
```

---

## 지원 입력 형식

| 파일 | 내용 |
|------|------|
| `My Clippings.txt` | 모든 Kindle 기기가 생성하는 표준 클리핑 텍스트 |
| `.yjr` | Kindle 사이드카 — 하이라이트·북마크·메모 (KFX/AZW3 전용) |
| `.yjf` | Kindle 사이드카 — 마지막 읽은 위치 |
| `.sdr/` | 위 두 파일이 들어 있는 사이드카 디렉터리 |
| `.apnx` | Amazon Page Number Index |
| `.mbp` | Mobipocket 어노테이션 바이너리 |

---

## Notion 데이터베이스 구조

아래 속성으로 데이터베이스를 만들어 주세요.

| 속성 이름 | 타입 |
|-----------|------|
| Title | 제목 |
| Author | 텍스트 |
| Highlights | 숫자 |
| Last Highlighted | 날짜 |
| Last Synced | 날짜 |

책당 하나의 페이지가 생성되며, 클리핑은 페이지 본문에 추가됩니다.

```
근대의 부르주아지 자체가 장구한 발전 과정의 산물이며…
* Location: 157–162, Page: 45, 2024-01-01 10:00:00, yellow

> NOTE:
이 부분이 핵심 논지
* Location: 42, 2024-01-01
```

---

## 중복 제거 방식

두 소스(KFX+YJR, My Clippings.txt)가 **같은 상태 파일**(`~/.kindle_notion_sync.json`)을 공유합니다.

```
클리핑 fingerprint = SHA-1(책제목 | 타입 | 위치시작 | 위치끝)
```

```
sync_kfx.py          sync_clippings_to_notion.py
     │                          │
     └──────────┬───────────────┘
                ▼
   ~/.kindle_notion_sync.json
   {
     "books": {
       "책제목": {
         "notion_page_id": "abc...",
         "synced_fingerprints": ["sha1hash", ...]
       }
     }
   }
```

- 위치가 같으면 소스가 달라도 같은 fingerprint → 중복 업로드 없음
- Notion Highlights 카운트 비교(구 방식) 대신 위치 기반으로 판단

---

## KFX 텍스트·페이지·Location 추출

`.kfx` 파일이 있을 경우 Calibre의 **KFX Input 플러그인**(`kfxlib`)을 이용해 하이라이트 원문·출판사 페이지 번호·Kindle Location 번호를 추출합니다.

### 의존성

```
/Applications/calibre.app/Contents/MacOS/ebook-convert
~/Library/Preferences/calibre/plugins/KFX Input.zip
```

플러그인이 없으면 텍스트·페이지 추출을 건너뛰고 나머지는 정상 동작합니다.

---

## 단일 파일 파싱

```bash
# .sdr 폴더 파싱
python parse_clippings.py "Book.sdr/" -o clippings.md -f markdown

# ebook 경로 직접 지정
python parse_clippings.py "Book.sdr/" -o clippings.md --ebook "Book.kfx"

# My Clippings.txt
python parse_clippings.py "My Clippings.txt" -o clippings.json

# 디렉터리 전체 스캔
python parse_clippings.py clippings/ -o all.csv -f csv
```

---

## 한도 초과 텍스트 복구

My Clippings.txt에서 한도 초과된 클리핑을 KFX 파일로 복구합니다.

```bash
python recover_clippings.py "My Clippings.txt" book.kfx -o recovered.json
python recover_clippings.py "My Clippings.txt" book.kfx --book "책 제목" -o out.md
```

---

## 예제

`examples/` 폴더에 실제 KFX 책(공산당선언)의 `.kfx` 원본, `.sdr` 사이드카, 파싱 결과 마크다운이 포함되어 있습니다.

```bash
python parse_clippings.py \
    "examples/gongsandangseoneon - kareul mareukeuseu _ peurideurihi enggelseu.sdr/" \
    -o out.md -f markdown
```

---

## 향후 계획

- **Notion 표지에 KFX 내장 표지 사용** — 현재 Notion 페이지 커버는 외부 URL(알라딘)
  참조 방식이라, KFX 파일에 내장된 정품 고해상도 표지를 못 쓴다. Notion File Upload
  API(2025) 로 이미지 바이트를 업로드(multipart → `file_id` 참조)하면 "내가 가진
  정확한 표지" 를 Notion 에도 박을 수 있다. 2단계 업로드라 구현 복잡도가 올라감.

---

## 개발 노트

역공학 과정에서 시도한 방법들과 발견한 것들은 [DEVLOG.md](DEVLOG.md)에 정리되어 있습니다.
