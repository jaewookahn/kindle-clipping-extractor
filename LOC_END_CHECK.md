# loc_end 소스 간 일치 실측

세션 `킨들클리핑new` · 지시: 리딩총괄2 · 원본: `~/kindle_annotation_backup/`, reading_manager `DATA_MODEL.md` §2.1 방식 재현 + end 분리

## A) POST-KL 간 — 과거 KFX+YJR 추출본(Scribe) vs My Clippings.txt

- Scribe 과거 추출본 하이라이트: 4,952건 (`kindle_sync_scribe.json`, 2026-06-26 동기화)
- My Clippings.txt 병합본 하이라이트: 7,374건 (8,501 엔트리 중)
- 정규화 20자 이상 & 내용 1:1 매칭: **3,795쌍** (중복 내용이라 1:1 아닌 것 9건 제외)

### loc_start 차이 분포 (My Clippings − Scribe추출)

```
       0 :  3,795  ← 일치
```

### loc_end 차이 분포 — 양쪽 다 end 있는 3,421쌍

```
       0 :  3,386  ← 일치
      -1 :     32
      -2 :      3
```

🔎 **패턴**: 불일치 35건 중 **33건(94%)이 하이라이트 끝이 문장 종결 부호(`.` 등)로 끝난다.** 나머지 2건만 예외. 무작위가 아니라 **끝이 마침표인 경우에 쏠린 것**으로 보인다 — KL맵 변환(`bisect_right`)이 마침표 위치를 기기가 매기는 Location 보다 1~2 높게 넣는 경계 처리 차이로 추정된다.

**end 불일치 예시**:

- 차이 -1 · `… 더 큰 모델들의 추론 확장성을 향상시킵니다.` · scribe=(3972,3975) myclippings=(3972,3974)
- 차이 -1 · `…수 산출 단계 직전에 토큰 표현에 추가됩니다.` · scribe=(4096,4099) myclippings=(4096,4098)
- 차이 -1 · `… 전능을 대기(大氣) 삼아 꽃핀다는 것입니다.` · scribe=(506,509) myclippings=(506,508)
- 차이 -1 · `…절히 원한다는 전혀 다른 메시지가 들어 있다.` · scribe=(807,810) myclippings=(807,809)
- 차이 -1 · `…고 정권을 비판한 저항 세력의 책만 남았을까?` · scribe=(3519,3522) myclippings=(3519,3521)

## B) PRE-KL 간 — KSDK DB vs YJR 사이드카

- YJR 사이드카 책: 41개 폴더 스캔 (껍데기·파편 다수 포함)
- KSDK DB 책(asin): 34권
- 좌표 집합 겹침(Jaccard≥0.5, 교집합≥5)으로 확정한 책 쌍: **26권**

| YJR 폴더 | KSDK asin | Jaccard | 겹치는 좌표 수 |
|---|---|---|---|
| demian - hereuman hese | 75B03680B0ZUXVFAUQG8 | 1.00 | 189 |
| katalronia canga - joji owel | YSDAVN35WJ6SPKHXZ5V7 | 1.00 | 180 |
| meosjin sinsegye - oldeoseu heogseulri | K8SKBVN4GHDP9FJYUV3T | 1.00 | 166 |
| nareul bonaeji ma - gajeuo isiguro | 89EH7J5BPV3HKJ6JE0AA | 1.00 | 148 |
| nyuromaenseo - wilrieom gibseun | Q0M79E91I2ON20724UQ7 | 1.00 | 132 |
| gusbai, kolreombeoseu - pilrib roseu | CWZAY7V6HXWNUBD3FA1R | 1.00 | 119 |
| ceon gaeyi canranhan taeyang - halredeu  | MPYIRYJGJB3X9KGLZSA5 | 1.00 | 110 |
| poreutunayi seontaeg 1 - kolrin maekeolr | X5Z9PZ2K1VMHS682Y7SY | 1.00 | 91 |
| nania yeondaegi - C. S. ruiseu | H73W9KZDAT8L00U15O7L | 1.00 | 86 |
| Deep Learning with Python, Third Edition | H99J2FXWFWFRQ22U12BJ | 1.00 | 84 |
| eljeoneonege ggoceul - daenieol kiseu | 9CLMRW8A3V1SPQ2NIPMO | 1.00 | 77 |
| keulrarawa taeyang - gajeuo isiguro | B57V46SUCZ69ZXKQNRUT | 1.00 | 76 |
| oneuliraneun yebae - tisi haeriseun weor | 3N6LUN4ZZJ4RUR385D9Q | 1.00 | 76 |
| caesigjuyija - hangang | MJLCVO19RTLASSC2X14L | 1.00 | 65 |
| baramyi geurimja 2 - kareulroseu ruiseu  | PV4V22T81EZNKIYZ4U10 | 1.00 | 64 |

- location_start 로 짝지어진 클리핑: 2,057건 (북마크 포함)
### loc_end 차이 분포 — 하이라이트·노트만, 양쪽 다 end 있는 1,487건

```
       0 :  1,487  ← 일치
```

**end 불일치 예시 없음 — 전부 일치.**

## 결론

- **B) PRE-KL (KSDK ↔ YJR)**: end 일치 1,487 / 불일치 0  (표본 1,487건) — **완전 일치.** 같은 저장소가 마이그레이션 전후로 나뉜 것뿐이니 예상대로다
- **A) POST-KL (Scribe추출 ↔ My Clippings)**: end 일치 3,386 / 불일치 35  (표본 3,421건, 1.0%) — **거의 일치하지만 완전하지 않다.** 무작위가 아니라 하이라이트 끝이 마침표로 끝나는 경우에 쏠려 있고(94%), 값도 대부분 -1(간혹 -2)로 방향이 일정하다. 그런데 크기가 -1 로 고정이 아니라 -1/-2 로 갈려 **단일 상수로는 안 흡수된다** — 마침표 뒤에 따옴표·괄호가 더 붙는지에 따라 갈리는 것으로 보인다

### 권장 (판단은 총괄 몫)

- **B(KSDK↔YJR, 킨들 내부 두 소스)는 loc_end 를 키에 넣어도 안전하다** — 1,487/1,487 완전 일치
- **A(추출본↔My Clippings)는 그대로 넣으면 매칭된 것 중 약 1%가 갈린다.** 다만 원인이 무작위가 아니라 **끝이 문장부호인 하이라이트라는 식별 가능한 경계 케이스**다. 옵션:
  1. loc_end 그대로 키에 포함 — 이 1% 는 소스 간 키가 갈라짐(중복 신원 발생)
  2. 끝이 `.`/`」`/`”` 등 문장부호·닫는 괄호일 때 loc_end 를 ±2 허용 범위로 정규화(스냅) — 정확한 상수 오프셋이 아니라 **경계 마진**으로 접근
  3. 이 경계 케이스만 loc_start 로 폴백