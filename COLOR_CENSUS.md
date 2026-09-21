# 색상 값 전수 조사 (2026-09-20)

세션 `킨들클리핑new` · 지시: 리딩총괄2 · 원본 데이터: `COLOR_CENSUS.json`
조사 스크립트: `/private/tmp/…/scratchpad/color_census.py` (세션 임시 폴더 — 재현하려면
`kindle/ksdk.py`·`kindle/parsers/yjr.py` 를 그대로 부르면 된다, 아래 참조)

## 왜

`kindle/keys.py` 의 색상태그 제거를 처음엔 사양이 준 4색
(`yellow`·`blue`·`pink`·`orange`) 화이트리스트로 구현했다. KSDK DB 실측
검증에서 `dark_blue` 가 안 벗겨지는 것이 드러났고, 리딩총괄2 판단으로
"표본을 센 쪽이 항상 옳았다" — 목록을 늘리는 대신 **실제로 몇 종류가 도는지
전수로 세었다.**

## 조사한 소스

| 소스 | 방법 |
|---|---|
| KSDK DB (`live`, `ksdk-recovered-20260919-0331`) | `nonsyncable_annotations`·`server_view` 의 `serialized_payload.json_metadata.mchl_color` 를 직접 질의 |
| YJR 사이드카 282개 | `parse_yjr()` 이 붙인 `content` 앞머리 `"[색상] "` 태그를 정규식으로 추출 |
| JSON 산출물 3벌 (Colorsoft 소실 전 백업·Scribe·현재 저장소) | `books[].clippings[].content` 앞머리 태그 추출 |
| `My Clippings.txt` 병합본 (8,501 엔트리) | 대괄호 색상 표기 유무만 확인 — **없음** (킨들 공식 내보내기 포맷엔 색상이 안 실린다, 예상대로) |

## 결과 — 전체 distinct 6종

```
yellow      8,346
dark_blue   1,632
pink          134
blue           12
green           4
orange          4
```

**`dark_blue` 가 전체의 15.9%** 를 차지한다 — 두 번째로 흔한 색이 화이트리스트에
없었다. `blue`(정확히 이 이름)와 `green` 도 소량 있다.

소스별 세부는 `COLOR_CENSUS.json` 의 `by_source` 참조. Colorsoft/recovered 두
KSDK DB 사본은 완전히 동일한 분포다(같은 데이터의 다른 백업이므로 당연).

## 결론

**패턴이 화이트리스트보다 맞다.** `kindle/ksdk.py:224` 가 `f"[{color}] "` 로
`mchl_color` 값을 그대로 조립하고, `parse_yjr()` 도 페이로드의 색상 문자열을
그대로 읽는다 — 어느 파서도 고정 어휘를 쓰지 않으므로, 앞으로 새 색이 생겨도
(펌웨어 업데이트·다른 기기 종류) 화이트리스트는 계속 샌다. 지금 6종 전부를
목록에 넣어도 다음 미확인 색상에서 같은 문제가 재발한다.

→ `kindle/keys.py` 의 `_COLOR_TAG` 를 `^\[[A-Za-z][A-Za-z_]*\]\s?` 패턴으로
교체했다 (선행 위치·알파벳 시작·한 번만). 회귀 테스트에 이 6종 전부와
`light_purple`·`vermillion` 같은 목록에 없는 색도 포함했다.
