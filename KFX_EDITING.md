# KFX 목차·표지 편집 — 하이라이트를 지키면서 고치기

사이드로드한 KFX 책의 **목차가 너무 성기거나**(장 5개뿐인데 소제목이 66개) **표지가
저해상도일 때** 고치는 도구들이다. 핵심 제약은 하나다 — **이미 쌓인 하이라이트를 잃지 않는 것.**

관련 문서: 역공학 과정과 실패한 시도는 [DEVLOG.md](DEVLOG.md) §9.

---

## 왜 파일을 직접 고쳐야 하나

킨들 하이라이트는 책 파일 옆 `.sdr` 폴더의 `.yjr`에 저장되고, 위치를 이렇게 적는다:

```
AT4EAABpAAAA:13927
└ base64 → 01 3e040000 69000000 → 버전 1, eid=1086, offset=105
                                   └ 본문 요소 id   └ 그 안에서의 문자 위치
```

즉 **본문 요소의 eid + 문자 오프셋**에 걸려 있다. 그래서:

| 방법 | 결과 |
|---|---|
| 캘리버로 EPUB 변환 → KFX 재생성 | ❌ eid 가 전부 새로 매겨져 하이라이트 전멸 |
| 캘리버로 표지 교체 | ❌ 위와 같다 (KFX 를 다시 만든다) |
| 목차를 xhtml 요소로 삽입 | ❌ 본문이 늘어나 추정 페이지·로케이션이 밀린다 |
| **nav 프래그먼트만 수술** | ✅ eid 불변 → 하이라이트 그대로 |

목차는 `$389`(book_navigation) 안에, 표지는 `$164`+`$417`에 들어 있다. **이미 존재하는
eid 를 가리키기만** 하면 본문은 한 바이트도 안 바뀐다.

---

## 도구

| 도구 | 하는 일 |
|---|---|
| `kfx_toc.py` | 목차 편집 — `plan` / `apply` / `verify` |
| `kfx_cover.py` | 표지 교체 — `show` / `replace` |
| `tools/kfx_toc_survey.py` | 라이브러리를 훑어 목차가 성긴 책 찾기 |
| `tools/bible_chapter_plan.py` | 성경 전용 장 목차 생성기 (책별 생성기의 예) |
| `tools/mtp_put.c` | libmtp 로 기기 폴더에 파일 넣기 (MacDroid 가 안 될 때) |

`plan`과 `apply`를 나눈 이유: **목차 제목은 기계가 정하면 안 되는 값**이다. `plan`이
JSON 초안을 내고 사람이 검토·수정한 뒤 `apply`가 쓴다. 덕분에 성경처럼 구조가 특이한 책은
전용 생성기를 붙이고 `apply`는 그대로 쓸 수 있다.

---

## 작업 순서

하이라이트가 있는 책은 이 순서를 지켜야 한다. 특히 **2번을 건너뛰지 말 것** — 캘리버
라이브러리 사본과 기기 사본은 다를 수 있다(실제로 세 권 모두 12바이트 달랐다).

### 1. 기기 파일과 `.sdr` 백업

```bash
KP="…/MacDroid-…/Internal Storage/documents"
B=~/Documents/kindle-toc-backup/$(date +%F)_bookname
mkdir -p "$B"
cp "$KP/책이름.kfx" "$B/"
cp -R "$KP/책이름.sdr" "$B/"
```

### 2. 기기본과 라이브러리본이 같은 빌드인지 확인

```bash
python kfx_toc.py verify "라이브러리/책.kfx" "$B/책이름.kfx" --expect '$164'
```

`$164`(표지 메타)만 다르면 같은 빌드다. 본문·페이지·로케이션이 다르면 **기기본을
원본으로 삼아야 한다.** 어느 쪽이든 아래는 **기기본**으로 진행한다.

### 3. 스타일을 눈으로 고르고 계획 만들기

```bash
python kfx_toc.py plan "$B/책이름.kfx" --list-styles        # 스타일 분포부터
python kfx_toc.py plan "$B/책이름.kfx" --heading-styles s59,s6A -o plan.json
```

**자동 탐지는 후보를 좁히는 용도일 뿐 선택은 사람이 한다.** 그리스 신화에서 자동 탐지는
각주 번호 스타일을 골랐고 실제로 쓴 건 `s59`/`s6A`였다. 길이·문장부호로 짐작하면 소설
대사("아무것도 안 보여.")와 그림 캡션이 섞인다.

`plan.json`을 열어 제목을 다듬고 필요 없는 항목을 지운다.

### 4. 적용하고 검증

```bash
python kfx_toc.py apply "$B/책이름.kfx" plan.json -o new.kfx
python kfx_toc.py verify "$B/책이름.kfx" new.kfx
```

통과 조건 — 이 넷이 다 맞아야 기기에 넣는다:

```
달라진 프래그먼트 : [$389]          ← 목차 하나뿐
추정 페이지 맵    : 동일
킨들 로케이션 경계 : 동일
본문 텍스트·오프셋 : 동일
```

표지도 바꿨다면 `--expect '$389,$164,$417'`로 허용 목록을 넓힌다.

### 5. 실제 하이라이트로 끝단 확인 (권장)

백업해 둔 `.yjr`로 추출을 돌려 본문·오프셋·페이지가 그대로인지 본다. 챕터 breadcrumb만
바뀌어야 한다.

```python
from kindle.parsers.yjr import parse_yjr
from kindle.ebook import extract_kfx_info, fill_clipping_text
# 원본과 수정본 각각에 대해 돌려 결과를 비교
```

### 6. 기기로 복사

```bash
cp new.kfx "$KP/책이름.kfx"      # 파일명을 그대로 유지할 것 (.sdr 가 이름으로 붙는다)
```

⚠️ **`cp` 성공은 증거가 아니다.** MacDroid 는 FileProvider 방식이라 `cp`가 로컬 복제본에
즉시 쓰고(18MB 가 0.01초) 기기 업로드는 비동기다. 복제본을 다시 읽으면 md5 까지 일치하므로
대조도 소용없다. **MacDroid Activities 창에서 업로드 완료를 확인할 것.**

---

## 표지 교체

```bash
python kfx_cover.py show book.kfx -o current.jpg          # 현재 표지 꺼내보기
python kfx_cover.py replace book.kfx new.jpg -o out.kfx
python kfx_toc.py verify book.kfx out.kfx --expect '$164,$417'
```

- 새 이미지는 **억지로 확대하지 말 것.** 없는 디테일이 생기지 않고 파일만 커진다.
  확대는 킨들이 표시할 때 한다. 킨들 콜로소프트 화면은 1264×1680.
- 표지 찾기는 크기로 짐작하지 않고 `kfxlib.get_cover_image_data()`가 표지로 인정한
  바이트와 대조한다. `$164`가 여러 개라 크기만 보면 본문 삽화를 집는다.

---

## 목차가 성긴 책 찾기

```bash
python tools/kfx_toc_survey.py "~/Calibre Library" -o survey.json
```

책마다 목차 항목 사이 최대 간격과, **목차에 없는 소제목 후보 수**를 낸다. 중단돼도 같은
`-o`로 다시 돌리면 이어서 한다.

'이미 목차에 있는지'는 **제목 문자열로 판정한다.** eid 나 위치로 비교하면 안 된다 —
자세한 이유는 DEVLOG §9.3.

결과는 후보 목록일 뿐이다. 책을 열어 `--list-styles`와 `plan`을 눈으로 확인해야 한다.
출판사 도서목록, 용어집, 화보 저작권 표시가 소제목으로 잡히는 경우가 남아 있다.

2026-09-12 조사 결과와 오탐 유형: **[KFX_SURVEY_RESULTS.md](KFX_SURVEY_RESULTS.md)**
(원본 JSON은 `tools/data/`). 적용한 세 권의 계획 JSON은 `examples/toc_plans/` 에 있다 —
`apply` 에 그대로 먹이면 같은 결과가 재현된다.

---

## 지금까지 적용한 책

| 책 | 목차 | 표지 | 하이라이트 |
|---|---|---|---|
| 스티븐 프라이 『그리스 신화: 트로이 전쟁』 | 12 → 78 | — | 23건 보존 |
| 『권력과 진보』 | 18 → 124 | — | 없음 |
| 『성경전서 새번역』 | 67 → 1,256 (장 1,189) | 271×392 → 735×980 | 201건 보존 |

성경은 목차 1,256항목도 킨들이 감당했다. 다만 **색인에 시간이 오래 걸린다**(본문 276만 자).
중간에 재시작하면 처음부터 다시 도니 케이블 꽂고 화면 켜둔 채 기다릴 것.
