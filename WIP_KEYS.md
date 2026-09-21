# 진행 중 — 파이썬 `book_key`/`clip_key` 이식

세션 `킨들클리핑new` · 2026-09-20 · **중단 지점 기록** (토큰 소진으로 총괄이 중지 지시)

## 상태

| 지시 | 상태 |
|---|---|
| ① 파이썬 `clip_key`/`book_key` 구현 | **구현·검증 완료. 미커밋** |
| ② `kindle/exporters.py:135` `source_file` strip 해제 | **미착수** |

테스트 **212개 통과 / 1개 skip**(공유 벡터 파일 미도착). 워킹트리에만 있고
커밋하지 않았다.

## 만든 것

```
kindle/keys.py          신규 — normalize_for_key / normalize_content /
                        strip_color_tag / book_key / compute_clip_key / clip_key
tests/test_keys.py      신규 — 38개
```

## JS 대조를 실제로 돌렸다

`~/prj/highlight-capture/src/utils/{bookKey,clipKey}.js` 의 정규화를 그대로 복사한
node 스크립트를 만들어 파이썬 출력과 대조했다 (`expo-crypto` 만 `node:crypto` 로 대체).

```
대조 102건 / 불일치 0건
```

스크립트는 스크래치패드에 있다 (`parity.mjs`, `parity_check.py`). 세션 임시
폴더라 사라지므로, 상시 검증은 `fixtures/clip_key_vectors.json` 이 올라온 뒤
`test_shared_vectors` 로 넘기는 것이 맞다. 그 테스트는 파일이 생기면 자동으로 켜진다.

## 지켜야 할 것 (구현에 반영돼 있음)

- **JS 동작을 그대로 옮겼다. 개선하지 않았다.** 눈에 거슬리는 부분도 그대로 뒀다 —
  특히 `normalize_for_key` 가 **하이픈에서 제목을 자른다** (`"Bill Bryson - Notes"`
  → `"billbryson"`). JS `split(/[:：–—-]/)[0]` 이 그렇다. 고치려면 JS 를 먼저 고치고
  함께 바꿔야 한다
- **북마크 `loc_end` 는 소스가 무엇을 주든 `None` 으로 정규화.** 회귀 테스트 3개
- **저자 토큰 정렬은 넣지 않았다** (사용자 보류). 현행 `t:` 규칙 그대로
- 색상 태그(`"[yellow] "`)는 `normalize_content` 에서 제거한다 — 사양 §7 의
  "색상태그 제거" 단계. JS 에는 없다 (종이책엔 색이 없다)
- ②④⑤ 갈래는 **POST-KL Location** 을 받는다. PRE-KL char offset 을 넣으면
  `My Clippings.txt` 쪽과 또 갈라진다

## 아직 안 한 것

1. **기존 파이프라인에 연결하지 않았다.** `kindle/keys.py` 는 아직 아무도 부르지
   않는다. `notion_export.fingerprint()` 교체는 상태 파일 마이그레이션이 얽혀 있어
   별도 판단이 필요하다 — 지금 바꾸면 이미 동기화한 전량이 신규로 잡힌다
2. 지시 ② `source_file` strip 해제 (main·docs·feature 세 브랜치 모두 동일)
3. 공유 벡터 도착 후 검증

## 재개할 때

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_keys.py -q -p no:warnings
```
