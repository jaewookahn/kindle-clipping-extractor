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

---

## 9. 여러 킨들 동시 연결 시 MTP 읽기 실패 (Mass Storage vs MTP 충돌)

**증상**: TUI에서 모든 책의 YJR/KFX 읽기가 `[Errno 60] Operation timed out` 으로
동시에 실패. 디렉터리 목록(`iterdir`)은 정상 — 책 목록은 뜨는데 내용만 못 읽음.

### 진단 과정

디렉터리 목록이 되는데 내용 읽기만 실패하는 건 두 계층이 분리돼 있다는 뜻이다.
macOS FileProvider(MacDroid 백엔드)가 디렉터리 구조는 캐시로 즉시 응답하고,
파일 내용은 그때그때 기기에서 새로 받아와야 하기 때문.

```python
# 15권을 순회하며 실제 read_bytes() 소요 시간 측정
ok=1 fail=14 elapsed=0.65s   # 14개 실패가 0.65초 만에 끝남
```

**핵심 단서는 속도였다.** 진짜 MTP 타임아웃이면 프로토콜 레벨에서 응답을 기다리다
못 받는 데 시간이 걸려야 하는데, 실패가 파일당 0.03~0.06초 만에 났다. 이건 "기기에
요청을 보냈는데 응답이 없다"가 아니라 "요청 자체가 상위 레이어에서 즉시 거부당했다"는
신호다.

당시 연결 상태를 `system_profiler SPUSBDataType` 로 확인하니 서로 다른 두 킨들이
동시에 잡혀 있었다:

```
Amazon Kindle (Voyage)   Product ID 0x0004   Vendor 0x1949 (Lab126)
Kindle Scribe            Product ID 0x9981   Vendor 0x1949 (Lab126)
```

같은 벤더(Lab126)지만 macOS 에 붙는 방식이 다르다:

- **Voyage** (구형) — USB Mass Storage 클래스. macOS 커널 드라이버가 직접
  디스크 볼륨으로 마운트 (`/Volumes/Kindle`).
- **Scribe / Colorsoft** (신형) — MTP/PTP 프로토콜. MacDroid 가 FileProvider
  확장으로 흉내내 `~/Library/CloudStorage/MacDroid-*/` 에 노출.

### 재현/해결

`/Volumes/Kindle` (Voyage 마운트)을 언마운트하자 Scribe 읽기가 즉시 정상화됐다.

```
Voyage 마운트됨   → Scribe 파일 읽기 100% 실패, Errno 60 즉시 발생
Voyage 언마운트   → Scribe 파일 읽기 정상
```

### 원인 (추정)

이 프로젝트 코드는 두 경로 모두 순수 파일시스템 API(`Path.read_bytes()`,
`Path.iterdir()`) 만 쓴다 — USB/MTP 프로토콜에는 전혀 관여하지 않는다. 실패는
OS 레벨에서 이미 일어난 뒤 그대로 올라온 것이다.

Voyage 가 Mass Storage 볼륨으로 마운트돼 있으면, 같은 벤더 ID 를 다루는 macOS 의
이미지/PTP 관련 공용 서비스(Image Capture Core 등)가 그 볼륨 클레임에 걸린 채로
남아 있다가, 별도의 MTP 기기(Scribe)로 가는 파일 전송 요청까지 지연·거부시키는
것으로 보인다. 두 기기가 물리적으로 다른 포트에 꽂혀 있어도 발생한다 —
포트/허브 문제가 아니라 벤더 ID 또는 클래스 드라이버 레벨의 충돌로 보인다.

**이 프로젝트가 고칠 수 있는 범위 밖이다** (macOS 커널 드라이버 ↔ MacDroid 앱
사이의 상호작용). 코드에 결함이 있는 게 아니라 재현 가능한 하드웨어/OS 조합
문제로 확인됨.

### 회피 방법

**Mass Storage 클래스 킨들(구형 기기)과 MTP 클래스 킨들(신형 기기)을 동시에
USB 연결하지 말 것.** MTP 기기만 여러 대 연결하는 것은 문제없었다
(KindleScribe + KindleColorsoftSignatureEdition 두 MacDroid 마운트가 동시에
있어도 정상 — 실패는 항상 Mass Storage 기기가 같이 물려 있을 때만 발생).

증상이 재발하면 확인 순서:
1. `system_profiler SPUSBDataType | grep -A5 Kindle` 로 동시 연결된 기기 확인
2. `ls /Volumes` 에 Mass Storage로 잡힌 구형 킨들이 있으면 `diskutil unmount` 로 제거
3. TUI 재시도

---

## 10. TUI → PyQt6 GUI 전환 — subprocess 경계가 이미 있어서 수월했다

TUI(`tui.py`)를 macOS GUI 앱으로 바꾸는 작업. 처음엔 네이티브 Swift/SwiftUI를
검토했으나, kfxlib 로딩·YJR 바이너리 파싱·libmtp ctypes·Notion REST 같은
파이썬 백엔드를 Swift에서 다시 부르는 브릿지 설계 비용이 커서 PyQt6로 방향을
바꿨다 — 같은 프로세스에서 `kindle` 패키지를 그대로 import 해서 쓸 수 있다.

**TUI를 미리 읽어보니 이미 절반은 프레임워크 독립적이었다.** 동기화 실행
(`SyncOptions`)은 애초에 `subprocess.Popen(["python", "sync_kfx.py", ...])`
로 CLI를 그대로 불러 stdout을 스트리밍하는 구조였다 — Textual 전용 코드가
아니었다. 그래서 PyQt 이식은 `QProcess`로 껍데기만 바꾸면 됐고, 동기화 로직은
한 줄도 다시 구현하지 않았다.

반대로 클리핑 로드(`_load_clippings`)와 표지 조회(`_load_cover`)는 UI
프레임워크와 무관한 순수 로직인데도 `ClippingPreview` 클래스 안에 인라인으로
박혀 있었다. TUI·GUI가 코드를 중복하지 않도록 `kindle/clip_loader.py`,
`kindle/covers.py`로 먼저 뽑아내고 TUI가 그걸 호출하도록 리팩터한 뒤 GUI를
만들었다 — 순서를 반대로 했으면(GUI에서 로직을 새로 베껴 쓰고 나중에 합치기)
두 버전이 갈라져 하나만 고치고 잊어버리는 문제가 났을 것이다.

**터미널이라서 필요했던 코드가 전체의 상당 부분이었다.** Kitty/Sixel/Halfcell
그래픽 프로토콜 감지, tmux passthrough, `KINDLE_TUI_IMAGE` 환경변수 처리 —
표지 이미지 하나 보여주는 데 150줄 넘게 들어가 있었다. GUI에서는
`QPixmap(path)` 한 줄이면 끝난다.

**검증**: 실제 연결된 킨들(Scribe, 문서 146권)로 GUI를 직접 띄워 스크린샷
확인 + 헤드리스 스크립트로 전 기능(책 목록 로드, 필터, 클리핑 로드 171개,
클리핑 내 검색, 표지 로드, 단일 책 scope로 `--dry-run` subprocess 실행까지)
end-to-end 확인. 129권 전체로 dry-run 하면 시간이 오래 걸려 검증 시엔
`--book` 필터로 범위를 좁혔다 — SyncDialog가 scope_books 가 전체의 부분집합일
때 `--book` 을 자동으로 붙이는 로직 덕분에 별도 코드 없이 됐다.

---

## 11. MacDroid File Provider 가 `.sdr` 내용을 낡은 캐시로 답한다

**증상.** 기기에 하이라이트가 분명히 있는데 앱에서 클리핑이 하나도 안 뜬다.
사용자가 Finder 로 그 폴더를 클릭한 *다음에야* 앱에서도 보이기 시작한다.

```
$ ls "baramyi geurimja 2 ….sdr"      # readdir(2)
assets                               # ← .yjr 없음. 실제로는 하이라이트 64개
```

### 무엇이 아니었나

처음엔 "책 파일이 기기에서 삭제됐고 `.sdr` 껍데기만 남은 것"으로 오진했다.
근거로 든 `.sdr` 이 비어 있고 같은 stem 의 `.kfx` 가 없다는 관찰 자체는
맞았지만, **찾은 폴더가 틀렸다.** 킨들은 같은 책에 대해 이름 규칙 두 개를 쓴다:

| 형태 | 예 | 내용 |
|---|---|---|
| 한글 + ASIN | `먼저 온 미래_W48DPCW9….sdr` | 비어 있음 (껍데기) |
| 로마자 `제목 - 저자` | `meonjeo on mirae - janggangmyeong.sdr` | **실제 `.kfx` + `.yjr`** |

한글 이름으로만 찾으면 껍데기 쪽만 보게 된다. `.kfx` 도 로마자 쪽에 있다.
교차표로 확인하면 상관관계가 완벽했다 — 빈 `.sdr` 중 책 파일이 있는 경우 0건.

### 진짜 원인

`fileproviderctl evaluate <path>` 로 Provider 에게 직접 물으면 드러난다:

```
childItemCount = 1;      ← Provider 가 자식이 1개라고 믿고 있다
isDownloaded = 1;        ← 다운로드(materialize) 문제가 아니다
```

**재열거(re-enumeration) 문제**다. 그리고 이 캐시는 파일시스템 계층 전부에
일관되게 적용된다 — 아래 셋 다 같은 낡은 값을 본다:

- `readdir(2)` (`ls`, `Path.iterdir`)
- `getattrlistbulk(2)` — Finder 가 쓰는 대량 열거 syscall. 직접 C 로 호출해도 동일
- 이름 직접 조회 (`stat` on `<stem><HASH>.yjr`) — 파일명이
  `<stem>c55055a60ca2cff566c471532c243e4e.yjr` 로 기기 전체가 같은 해시라
  열거 없이 경로를 만들 수 있는데도 실패한다

창 없는 Finder 질의(AppleScript `count items of folder`)도 **1** 을 답한다.
즉 Finder 라서 되는 게 아니라, **폴더를 여는 행위가 새 enumerator 세션을
만들기 때문에** MacDroid 가 기기에 다시 물어보는 것이다.

### MTP 직접 읽기는 대안이 아니다

`kindle/device.py` 의 `mtp_direct_session()` 은 MacDroid 를 우회하지만,
MacDroid 가 USB 인터페이스를 점유하고 있어 함께 못 쓴다:

```
error returned by libusb_claim_interface() = -3
LIBMTP PANIC: Unable to initialize device
```

둘은 상호 배타적이다. MacDroid 를 끄면 마운트가 사라지고, 켜면 MTP 가 막힌다.

### 해결 — `kindle/fileprovider.py`

`materialize()` 가 `NSFileCoordinator` 로 "업로드용 읽기"(`ForUploading`) 의도의
코디네이트 읽기를 걸어 Provider 에게 진짜 내용을 요구한다.

**Finder 를 자동으로 여는 코드는 의도적으로 넣지 않았다.** 초판에는 `open -g`
폴백이 있었는데 — 동작은 확실하지만 — 일괄 처리 때 Finder 창이 29번 떴다 닫혔다.
사용자 화면에 창을 띄우는 건 라이브러리가 할 짓이 아니라 제거했다.

그래서 `materialize()` 는 **실패할 수 있다.** 조용히 "하이라이트 없음"으로
넘어가면 원래 증상과 똑같아지므로, 실패하면 호출부가 `HINT` 로 안내한다:

    MacDroid 가 이 책의 .sdr 내용을 낡은 캐시로 가리고 있습니다.
    Finder 에서 해당 폴더를 한 번 열면 풀립니다: <경로>

⚠️ **코디네이트 읽기가 실제로 재열거를 유발하는지는 미검증이다.** 검증하려면
아직 안 채워진 `.sdr` 이 있는 기기가 필요한데, 한 번 채워진 폴더로는 다시
시험할 수 없다 (Colorsoft 29권은 조사 중에 이미 다 소진). `.sdr/assets` 같은
깊은 디렉터리로 시험해봤지만 Finder 로도 안 채워져서 — 즉 진짜 빈 폴더라서 —
판별에 쓸 수 없었다. **Finder 로 폴더를 여는 것이 통한다는 사실만 확실하다.**

연결 지점:
- `kindle/clip_loader.py` — YJR 이 안 보이면 그 책만 재열거 요청 후 재시도,
  실패하면 `HINT` 를 에러 목록에 넣는다
- `sync_kfx.list_kfx_books(materialize_stale=True)` — 목록의 YJR 수치까지
  고치는 일괄 경로. 권당 대기가 있어 **기본값은 False**
- GUI 툴바 "숨은 클리핑 찾기" — 위 일괄 경로를 워커로 실행

### 실측 (Colorsoft, 책 146권)

```
materialize 대상(YJR 안 보임)     29권
실제로 살아난 책                    3권   ← 나머지 26권은 원래 하이라이트가 없었다
  바람의 그림자 2   하이라이트 64개
  마리아의 아들     YJR 만 생김 (북마크)
  레 미제라블 4     YJR 만 생김 (북마크)
일괄 소요                        153초
```

`looks_unmaterialized()` 는 "어노테이션 파일이 하나도 안 보임"으로 판정해서
**하이라이트가 원래 없는 책도 True 가 된다**(오탐 26/29). 반대로 놓치면
하이라이트가 통째로 안 보이므로 의도적으로 이쪽으로 치우치게 뒀다.

### 진단에 쓸 수 있는 교차 검증

`My Clippings.txt` 는 append-only 라 기기에서 파일이 가려져도 남아 있다.
"My Clippings 에는 N건 있는데 `.sdr` 에 YJR 이 안 보이는 책"을 뽑으면
가려진 책을 정확히 집어낼 수 있다 — 위 `바람의 그림자 2`(75건)를 이렇게 찾았다.

---

## 2026-09-19 — KSDK 어노테이션 중단 원인 규명 + 데이터 회수 완료

8/25 이후 클리핑이 `.yjr`·`My Clippings.txt` 어디에도 안 남던 문제의 전모.
전체 기록은 `KINDLE_ANNOTATION_OUTAGE.md` (§14~§16).

- **원인**: 웨브랩 `KSDKANNOTATIONS_*` T1 코호트에 어노테이션 저장소가
  `/mnt/us/system/ksdk/.annotations/<계정>/ksdk_annotation_v1.db` 로 이전된
  **의도된 사양 변경**. 구 저널·사이드카 경로는 차단/격하. `.bad_file`
  격리 루프와 빈 사이드카는 부수 증상. `;dm` 로그로 코드 레벨 확인.
- **회수**: Véra 탈옥(≤5.19.6 지원) → `;log mrpi` 가 사용자 저장소 스크립트를
  루트로 실행한다는 점을 이용해 스크립트 교체 → DB 전체 복사.
  08-25 이후 839개 포함 2015년분까지 전량 생존.
- **함의**: 사이드로드 신규 수집은 사이드카 경로로는 구조적으로 불가.
  앞으로는 KSDK DB 파서(`kindle/`)가 주 경로가 된다. `shortPosition` 이
  YJR 과 같은 PRE-KL char offset 이라 기존 fill 파이프라인·fingerprint 체계와
  그대로 호환 (`My Clippings.txt` 는 POST-KL 이라 교차 dedup 은 여전히 불가).
- **주의**: 5.19.x 에서 MRPI/KUAL 등 커뮤니티 ELF 바이너리는 라이브러리
  로딩 문제로 안 돈다 (셸 스크립트 경로만 생존). Scribe 는 미회수.
