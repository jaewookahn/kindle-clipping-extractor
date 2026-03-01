# Kindle Clipping Extractor

Kindle 기기의 하이라이트·메모·북마크를 여러 형식에서 파싱해 JSON / CSV / Markdown / 텍스트로 내보내는 파이썬 스크립트.

## 지원 형식

| 파일 | 내용 |
|------|------|
| `My Clippings.txt` | 모든 Kindle 기기가 생성하는 표준 클리핑 텍스트 |
| `.yjr` | Kindle 사이드카 — 하이라이트·북마크·메모 (KFX/AZW3 전용) |
| `.yjf` | Kindle 사이드카 — 마지막 읽은 위치·타이머 통계 |
| `.sdr/` | 위 두 파일이 들어 있는 사이드카 디렉터리 (재귀 스캔) |
| `.apnx` | Amazon Page Number Index — 페이지 맵 메타데이터 |
| `.mbp` | Mobipocket 어노테이션 바이너리 |

---

## 사용법

```bash
# .sdr 폴더 파싱 (옆에 .kfx 파일이 있으면 자동으로 텍스트·페이지 추출)
python parse_clippings.py "Book.sdr/" -o clippings.md -f markdown

# ebook 경로를 직접 지정
python parse_clippings.py "Book.sdr/" -o clippings.md --ebook "Book.kfx"

# 텍스트/페이지 추출 건너뜀
python parse_clippings.py "Book.sdr/" -o clippings.md --no-text --no-pages

# My Clippings.txt
python parse_clippings.py "My Clippings.txt" -o clippings.json

# 디렉터리 전체 스캔
python parse_clippings.py clippings/ -o all.csv -f csv
```

### 출력 형식 (`-f`)
`json` (기본) · `csv` · `markdown` · `text`
출력 파일 확장자(`.json` / `.csv` / `.md` / `.txt`)로 자동 추론됩니다.

---

## KFX 텍스트·페이지·로케이션 추출 (선택 기능)

`.kfx` 파일이 있을 경우, Calibre의 **KFX Input 플러그인**(`kfxlib`)을 이용해:

- 하이라이트된 **텍스트 원문** 복원
- **출판사 페이지 번호** 추출
- **Kindle Location 번호** (리더 UI에 표시되는 숫자) 변환

을 한 번에 수행합니다.

### 의존성

Calibre가 설치되어 있고 KFX Input 플러그인이 있어야 합니다.

```
/Applications/calibre.app/Contents/MacOS/ebook-convert
~/Library/Preferences/calibre/plugins/KFX Input.zip
```

kfxlib는 플러그인 zip 안에 번들되어 있으며, 스크립트가 실행 시 임시 디렉터리에 자동으로 압축 해제합니다. 별도 설치 불필요.

---

## 개발 노트 — 시도한 방법들과 발견한 것들

이 스크립트를 만들면서 꽤 많은 역공학 작업이 필요했습니다. 막혔던 부분과 돌파구를 기록합니다.

### 1. YJR/YJF 바이너리 포맷 파싱

`.yjr` / `.yjf`는 공식 문서가 없는 독점 바이너리 포맷입니다.
헥스 덤프와 반복 실험으로 TLV(Type-Length-Value) 구조임을 파악했습니다.

```
0xFE + 3바이트 키 길이 + 키 문자열  → 새 레코드 시작
0x01 + 4바이트                       → uint32 값
0x02 + 8바이트                       → uint64 타임스탬프 (빅엔디언, epoch ms)
0x03 + 3바이트 길이 + 바이트열       → 가변 길이 문자열
0x07 + 2바이트 스킵                  → 복합 컨테이너 (내부 아이템 인라인)
0xFF                                 → 레코드 종료
```

**위치 문자열** 형식: `"AT4EAABpAAAA:13927"`
- 콜론 앞 base64 부분: (타입 바이트, 프래그먼트 ID, 패딩, 로컬 char offset) 인코딩
- **콜론 뒤 숫자**: KFX 내부 절대 char offset (Kindle UI의 Location 번호가 아님!)

**색상 태그**: `content` 필드 앞에 `[yellow]`, `[pink]` 등의 접두사로 저장됨.

---

### 2. 하이라이트 텍스트 복원 — 실패한 방법

YJR에서 추출한 char offset으로 실제 하이라이트 텍스트를 가져오는 것이 핵심 과제였습니다.

#### ❌ 시도 1: Calibre `ebook-convert`로 KFX → TXT 변환 후 슬라이싱

```python
# ebook-convert Book.kfx /tmp/book.txt
text = open("/tmp/book.txt").read()
snippet = text[loc_start:loc_end]  # ← 엉뚱한 위치 반환
```

**결과: 실패.** Calibre의 변환기는 내부적으로 단락 구분, 공백 처리 방식이 KFX 원본과 달라서 **위치 드리프트(position drift)**가 발생합니다. char offset 7306이 가리키는 텍스트가 변환된 TXT와 KFX 원본 사이에서 달랐습니다.

예: KFX char offset 7306–7372가 가리키는 실제 하이라이트는 `"근대의 부르주아지 자체가 장구한 발전 과정의 산물이며…"` 인데, Calibre TXT의 같은 위치에는 `"발전은 다시금 산업 확대에 영향을 미쳤으며…"` 가 있었습니다.

#### ✅ 시도 2: kfxlib `collect_content_position_info()` 직접 활용

kfxlib(Calibre KFX Input 플러그인 내부 라이브러리)의 `collect_content_position_info()`는 책의 텍스트를 **KFX 내부 char offset을 그대로 보존하는 청크(ContentChunk) 리스트**로 반환합니다.

```python
chunks = book.collect_content_position_info()
# 각 ContentChunk:
#   .pid   → KFX 절대 char offset (YJR의 위치 숫자와 동일한 기준)
#   .text  → 해당 위치의 실제 텍스트
#   .length → 텍스트 길이 (글자 수)
```

이 청크들을 pid 순으로 정렬한 뒤 이어 붙이면(갭은 공백으로 채움), `full_text[7306:7372]`가 정확히 하이라이트된 원문을 돌려줍니다.

```python
chunks_with_text = sorted([c for c in chunks if c.text], key=lambda c: c.pid)
parts, pos = [], 0
for c in chunks_with_text:
    if c.pid > pos:
        parts.append(" " * (c.pid - pos))   # 갭 채우기
    parts.append(c.text)
    pos = c.pid + c.length
book_text = "".join(parts)
```

**결과: 성공.** 모든 하이라이트의 원문이 정확하게 복원되었습니다.

---

### 3. Kindle Location 번호 — 실패한 방법

#### ❌ 시도 1: YJR의 콜론 뒤 숫자를 Location으로 직접 표시

처음에는 `"AT4EAABpAAAA:13927"` 에서 `13927`을 Kindle Location으로 그대로 출력했습니다.

**결과: 오류.** `13927`은 KFX 내부 **char offset**이며, Kindle 리더 UI에 표시되는 Location 번호(예: `138`)와는 완전히 다른 단위입니다. 테스트 책(119,882자)의 경우 Location은 1–2419, char offset은 0–118,216 범위로 약 50배 차이가 납니다.

#### ✅ 시도 2: kfxlib `collect_location_map_info()` + bisect 변환

kfxlib가 KFX에 저장된 Location 경계 테이블을 읽어줍니다.

```python
pos_info = book.collect_position_map_info()
loc_info = book.collect_location_map_info(pos_info)
kindle_loc_offsets = [entry.pid for entry in loc_info]
# kindle_loc_offsets[i] = Kindle Location (i+1)이 시작하는 char offset
```

char offset → Location 번호 변환:

```python
import bisect
kl = bisect.bisect_right(kindle_loc_offsets, char_offset)
# kl이 Kindle 리더에 표시되는 Location 번호
```

**결과: 성공.** char offset 7306 → Location 157 등 Kindle UI와 일치하는 번호가 출력됩니다.

---

### 4. 페이지 번호 추출

KFX 파일 안에 출판사 페이지 번호가 Ion 바이너리 데이터로 포함되어 있습니다.
kfxlib로 디코딩하면 `$389` 프래그먼트(내비게이션)의 `$237` 타입(페이지 목록)에서 꺼낼 수 있습니다.

```python
nav_fragment = book.fragments.get("$389")
# $392 컨테이너 → $235 == "$237"(page list) 필터
# 각 항목: $241.$244 = 페이지 레이블, $246.{$155, $143} = eid + eid_offset
pid = book.pid_for_eid(eid, eid_offset, pos_info)  # → char offset
```

char offset을 bisect로 페이지 맵에서 이진 탐색하면 "이 어노테이션은 몇 페이지" 를 알 수 있습니다.

**중요한 파이프라인 순서**: 세 작업 모두 raw char offset을 기준으로 하므로, Location 번호 변환(`fill_clipping_kindle_locations`)은 반드시 **마지막**에 실행해야 합니다. 변환 후에는 `location_start/end`가 char offset이 아니라 Location 번호가 됩니다.

```
파싱(YJR → char offset) → 텍스트 복원 → 페이지 번호 → Location 변환
```

---

### 5. LZPC 페이지네이션 캐시 (미해결)

`.sdr/data/.pagination.cache/` 안에 Amazon 독점 LZPC 압축 포맷의 캐시 파일이 있습니다.
헤더에 `PGNC` 매직 바이트, 버전 `1.0.6981.0`, 책 GUID가 들어 있는 것까지 확인했으나
압축 알고리즘을 해독하지 못해 내용을 읽는 데는 실패했습니다. (미구현)

---

## 예제

`examples/` 폴더에 실제 KFX 책(`공산당선언`)의 `.sdr` 사이드카와 파싱 결과 마크다운이 포함되어 있습니다.

```bash
# examples/ 폴더에 .kfx 파일을 옆에 두고 실행 (KFX 파일은 크기 때문에 미포함)
python parse_clippings.py "examples/gongsandangseoneon - kareul mareukeuseu _ peurideurihi enggelseu.sdr/" \
    -o out.md -f markdown
```

`examples/sample_output.md`에서 실제 출력 결과를 확인할 수 있습니다.
