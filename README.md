# Kindle Clipping Extractor

Kindle 기기의 하이라이트·메모·북마크를 여러 형식에서 파싱해 JSON / CSV / Markdown / 텍스트로 내보내거나 Notion 데이터베이스에 직접 동기화하는 파이썬 스크립트.

## 지원 형식

| 파일 | 내용 |
|------|------|
| `My Clippings.txt` | 모든 Kindle 기기가 생성하는 표준 클리핑 텍스트 |
| `.yjr` | Kindle 사이드카 — 하이라이트·북마크·메모 (KFX/AZW3 전용) |
| `.yjf` | Kindle 사이드카 — 마지막 읽은 위치 |
| `.sdr/` | 위 두 파일이 들어 있는 사이드카 디렉터리 |
| `.apnx` | Amazon Page Number Index |
| `.mbp` | Mobipocket 어노테이션 바이너리 |

---

## 스크립트 구성

| 스크립트 | 용도 |
|----------|------|
| `sync_kfx.py` | **메인 워크플로** — KFX+YJR 기반 증분 동기화, Notion 업로드 |
| `sync_clippings_to_notion.py` | **보충용** — My Clippings.txt → Notion (수동 실행) |
| `parse_clippings.py` | 단일 파일/디렉터리 파싱 후 파일 출력 |
| `sync_clippings.py` | My Clippings.txt 기반 증분 동기화 (파일 출력 전용) |
| `recover_clippings.py` | My Clippings.txt 한도 초과 텍스트 복구 |

---

## 메인 워크플로: KFX+YJR → Notion

킨들을 USB로 연결한 뒤 실행합니다.

```bash
# 환경변수 설정 (한 번만)
export NOTION_TOKEN=secret_xxx
export NOTION_DB=your_database_id

# 동기화
python sync_kfx.py --notion-db $NOTION_DB
```

- KFX+YJR 쌍이 있는 모든 책을 자동 탐색
- 신규 클리핑만 Notion에 추가 (fingerprint 기반 중복 제거)
- KFX에서 실제 하이라이트 텍스트·페이지 번호·Kindle Location 복원
- 책 표지를 Google Books API에서 자동으로 가져옴

### 옵션

```
--notion-token TOKEN    Notion 통합 토큰 (NOTION_TOKEN 환경변수 대체 가능)
--notion-db DB_ID       Notion 데이터베이스 ID
--notion-state FILE     상태 파일 경로 (기본값: ~/.kindle_notion_sync.json)
--no-cover              책 표지 추가 안 함
-o FILE                 Notion과 별개로 파일에도 저장
-f FORMAT               출력 형식: json | csv | markdown | text
--dry-run               저장 없이 신규 항목 목록만 출력
--reset                 상태 초기화 후 전체 재동기화
--list-books            KFX+YJR 쌍 목록만 출력
--kindle PATH           킨들 마운트 경로 직접 지정 (생략 시 자동 감지)
--log FILE              로그 파일 경로 (기본값: kindle_sync.log)
```

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

상태 파일 (`~/.kindle_notion_sync.json`)에 책별로 동기화한 클리핑의 fingerprint를 저장합니다.

```
fingerprint = SHA-1(책제목 | 타입 | 위치시작 | 위치끝)
```

- KFX+YJR과 My Clippings.txt 두 소스가 **같은 상태 파일을 공유**하므로 소스가 달라도 같은 클리핑이 두 번 올라가지 않습니다.
- Notion의 Highlights 카운트 비교(구 kindle2notion 방식) 대신 위치 기반으로 판단합니다.

---

## 보충 동기화: My Clippings.txt → Notion

KFX+YJR이 없는 책(구형 포맷, 기기에서 삭제된 전자책 등)을 수동으로 보충합니다.

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

## KFX 텍스트·페이지·Location 추출

`.kfx` 파일이 있을 경우, Calibre의 **KFX Input 플러그인**(`kfxlib`)을 이용해 하이라이트 원문·출판사 페이지 번호·Kindle Location 번호를 추출합니다.

### 의존성

Calibre와 KFX Input 플러그인이 설치되어 있어야 합니다.

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

## 개발 노트

역공학 과정에서 시도한 방법들과 발견한 것들은 [DEVLOG.md](DEVLOG.md)에 정리되어 있습니다.
