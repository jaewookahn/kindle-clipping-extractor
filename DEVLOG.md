# 개발 노트 — 시도한 방법들과 발견한 것들

이 스크립트를 만들면서 꽤 많은 역공학 작업이 필요했습니다. 막혔던 부분과 돌파구를 기록합니다.

## 1. YJR/YJF 바이너리 포맷 파싱

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

## 2. 하이라이트 텍스트 복원 — 실패한 방법

YJR에서 추출한 char offset으로 실제 하이라이트 텍스트를 가져오는 것이 핵심 과제였습니다.

### ❌ 시도 1: Calibre `ebook-convert`로 KFX → TXT 변환 후 슬라이싱

```python
# ebook-convert Book.kfx /tmp/book.txt
text = open("/tmp/book.txt").read()
snippet = text[loc_start:loc_end]  # ← 엉뚱한 위치 반환
```

**결과: 실패.** Calibre의 변환기는 내부적으로 단락 구분, 공백 처리 방식이 KFX 원본과 달라서 **위치 드리프트(position drift)**가 발생합니다. char offset 7306이 가리키는 텍스트가 변환된 TXT와 KFX 원본 사이에서 달랐습니다.

예: KFX char offset 7306–7372가 가리키는 실제 하이라이트는 `"근대의 부르주아지 자체가 장구한 발전 과정의 산물이며…"` 인데, Calibre TXT의 같은 위치에는 `"발전은 다시금 산업 확대에 영향을 미쳤으며…"` 가 있었습니다.

### ✅ 시도 2: kfxlib `collect_content_position_info()` 직접 활용

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

## 3. Kindle Location 번호 — 실패한 방법

### ❌ 시도 1: YJR의 콜론 뒤 숫자를 Location으로 직접 표시

처음에는 `"AT4EAABpAAAA:13927"` 에서 `13927`을 Kindle Location으로 그대로 출력했습니다.

**결과: 오류.** `13927`은 KFX 내부 **char offset**이며, Kindle 리더 UI에 표시되는 Location 번호(예: `138`)와는 완전히 다른 단위입니다. 테스트 책(119,882자)의 경우 Location은 1–2419, char offset은 0–118,216 범위로 약 50배 차이가 납니다.

### ✅ 시도 2: kfxlib `collect_location_map_info()` + bisect 변환

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

## 4. 페이지 번호 추출

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

## 5. LZPC 페이지네이션 캐시 (미해결)

`.sdr/data/.pagination.cache/` 안에 Amazon 독점 LZPC 압축 포맷의 캐시 파일이 있습니다.
헤더에 `PGNC` 매직 바이트, 버전 `1.0.6981.0`, 책 GUID가 들어 있는 것까지 확인했으나
압축 알고리즘을 해독하지 못해 내용을 읽는 데는 실패했습니다. (미구현)

---

## 6. notional 의존성 제거 — pydantic 2 환경에서 프로젝트 기동 불가

`notional 0.8.2` 는 pydantic v1 API 에 고정돼 있습니다.

```python
# notional/core.py
from pydantic.main import ModelMetaclass, validate_model   # pydantic 2 에는 없음
```

pydantic 2 가 깔린 환경에서는 `import notional` 이 `ImportError` 로 죽고,
`kindle/notion_export.py` 를 **최상위에서 import** 하는 `sync_kfx.py` 와 `tui.py`
까지 전부 기동 불가가 됩니다. Notion 을 안 쓰는 `--list-books` 조차 실패했습니다.

`pydantic<2` 로 내리는 대신 **notional 자체를 걷어냈습니다**. 이 프로젝트가 쓰는
Notion 호출은 6종뿐이었고, 그중 절반(블록 추가·본문 재작성·속성 갱신)은
이미 raw REST 였습니다 — notional 이 만드는 블록 payload 에 API 가 거부하는
read-only 필드가 섞여 나와서, 진작에 우회해 둔 상태였습니다.

남은 3개(페이지 검색·생성·존재 확인)를 옮기면서 발견한 것:

**title 속성은 `rich_text` 필터로 매칭되지 않습니다.**

```python
# ❌ 조회가 항상 비어서 같은 책의 페이지가 중복 생성됨
.filter(property="Title", rich_text=TextCondition(equals=title))

# ✅
{"filter": {"property": "Title", "title": {"equals": title}}}
```

**레이트 리밋**: Notion 은 integration 당 평균 3 req/s 이고 초과하면 429 +
`Retry-After` 를 줍니다. `--rewrite-bodies` 는 블록을 **하나씩** 지우므로 권당
수백 요청이 나가는데, 재시도가 없어서 429 한 번에 그대로 중단됐습니다.
`_NotionAPI` 가 429 는 `Retry-After` 를 존중하고 5xx 는 지수 백오프로 재시도합니다.

---

## 7. fingerprint 좌표계 — PRE-KL vs POST-KL

`fingerprint = SHA1(제목|타입|location_start|location_end)` 인데, **`location_*` 이
어느 시점의 값이냐**에 따라 완전히 다른 해시가 나옵니다.

```
YJR 파싱 직후   location_start = 5391   (KFX char offset)   ← PRE-KL
KL 변환 후      location_start = 113    (Kindle Location)   ← POST-KL
```

`sync_kfx` 는 비싼 KFX 추출 **전에** 신규 항목을 걸러야 하는데, KL 번호는 추출
후에야 나옵니다. 그래서 로컬 `seen_keys` 는 PRE-KL 을 씁니다.

여기서 두 가지가 걸립니다.

**(1) 정렬이 짝을 깨뜨림.** 클리핑과 fingerprint 를 별도 리스트로 들고 다니다가
클리핑만 정렬하면 인덱스가 어긋납니다. 위치로 `zip` 해서 쓰던 곳이라 정렬 직후부터
엉뚱한 fingerprint 가 저장되고 dedup 이 통째로 깨졌습니다. 반드시 함께 정렬해야
합니다 (`sync_to_notion` 에 길이 불일치 가드 추가).

**(2) 교차 소스 dedup 은 여전히 미해결.** `My Clippings.txt` 의 location 은 애초에
Kindle Location 번호(POST-KL)입니다. 그래서 같은 하이라이트라도

```
sync_kfx        SHA1("책|highlight|5391|17951")
My Clippings    SHA1("책|highlight|113|358")
```

로 절대 안 맞습니다. 상태 파일을 공유해도 소스가 다르면 중복 제거가 안 됩니다.
로컬 `seen_keys` 는 PRE-KL(빠른 필터), Notion 상태 파일은 POST-KL(교차 소스)로
역할을 나누는 게 맞아 보이지만, 책 제목도 소스마다 달라서(KFX 메타데이터 제목 vs
`My Clippings` 제목 줄) 안정적인 `book_id` 가 먼저 필요합니다. (미해결)

---

## 8. 챕터 범위 — 부모와 첫 자식이 같은 offset 을 가리킨다

KFX TOC(`$212`)를 평탄화하면 부모 항목과 그 첫 자식이 **같은 char offset** 을
갖는 경우가 흔합니다.

```
(100, "1장")            ← 부모
(100, "1장 › 1.1 도입")  ← 첫 자식, 같은 위치
(300, "1장 › 1.2 본문")
(600, "2장")
```

"챕터 끝 = 다음 항목의 시작" 으로 잡으면 부모 `1장` 이 `100–100`, 즉 **길이 0** 이
됩니다. 실제로는 자식들을 전부 포함해야 하므로:

> 챕터 끝 = 다음에 오는, **자기 자손이 아닌** 항목의 시작

자손 판정은 breadcrumb 접두사(`child.startswith(parent + " › ")`)로 합니다.

끝 페이지는 exclusive 경계(`char_end`)가 아니라 **마지막 문자**(`char_end - 1`)로
조회해야 합니다. 그러지 않으면 `1장` 의 끝페이지가 `2장` 의 첫 페이지가 됩니다.

실제 예제(공산당선언)에서 32개 챕터, 부모 `제1장`(5389–36840)이 자식들
(5391–35500)을 정확히 감싸고 페이지·Location 이 빈틈없이 이어지는 것을 확인.
