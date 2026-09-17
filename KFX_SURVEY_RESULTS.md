# 라이브러리 목차 조사 결과 — 2026-09-12

`tools/kfx_toc_survey.py` 로 캘리버 라이브러리 **378권**을 훑은 결과다.
원본 데이터: `tools/data/survey-2026-09-12.json`

```bash
python tools/kfx_toc_survey.py "~/Calibre Library" -o tools/data/survey-YYYY-MM-DD.json
```

- 후보 203권 (중복본 제거 후), 그중 **킨들에 올라가 있는 것 88권**
- 제외 160권 — 소제목 스타일이 없는 책. 장 구분이 빈 줄뿐인 소설이 대부분이라
  목차에 넣을 재료가 아예 없다 (『듄』 2~6부, 『뉴로맨서』, 『클라라와 태양』 등)

⚠️ **이 목록은 후보일 뿐이다.** 실제로 손대기 전에 반드시 `--list-styles` 와 `plan`
결과를 눈으로 확인해야 한다. 아래 표에도 오탐이 남아 있다 — 자세한 건 맨 아래.

## 킨들에 있고 추가 여지가 큰 책

| 추가 | 현재 목차 | 최대구간 | 책 | 스타일 | 뽑힌 제목 예 |
|---:|---:|---:|---|---|---|
| +364 | 59 | 2% | nayi saranghaneun caeg - jon seutoteu | `s3T` | 제1주 일요일 / 제1주 월요일 |
| +230 | 1 | 100% | Deep Learning with Python, Third Edition - F | `sVB` | 2.2.1 스칼라(랭크-0 텐서) / 2.2.2 벡터 (1차 텐서) |
| +115 | 99 | 6% | bijeongongjado ihaehal su issneun AI jisig - | `s2P5` | • 모라벡의 역설Moravec’s Paradox / • 인공 신경망Artificial Neural Netwo |
| +109 | 19 | 11% | System Design Interview - An Insider's Gui - | `s4A` | Which databases to use? / Cache tier |
| +106 | 18 | 15% | gweonryeoggwa jinbo - daereon asemogeulru_sa ✅ | `sC1` | 진보의 밴드왜건 / 자동화의 우울 |
| +101 | 91 | 57% | AI Engineering - Chip Huyen | `sBT` | 언어 모델 / 자기 지도 학습 |
| +85 | 27 | 9% | myeongjageul ilgneun gisul - baggyeongseo | `s6Y` | 문학의 기능 / 플라톤의 이상 국가 |
| +79 | 24 | 9% | hananimyi yeolsim - bagyeongseon | `s2Y` | 갈 바를 알지 못하고 나아갔으며 / 마침내 가나안 땅에 들어갔더라 |
| +68 | 6 | 92% | re mijerabeul 3 - bigtoreu wigo | `s1R` | 2. 그의 특색 몇 가지 / 3. 그는 유쾌하다 |
| +63 | 12 | 31% | seutibeun peuraiyi geuriseu sinhwa _ teuro - ✅ | `s59` | 트로이의 창건 / 저주 |
| +62 | 7 | 94% | re mijerabeul 1 - bigtoreu wigo | `s2C` | 2. 미리엘 씨, 비앵브뉘 예하가 되다 / 3. 착한 주교에 어려운 주교구 |
| +61 | 6 | 96% | re mijerabeul 4 - bigtoreu wigo | `s2F` | 2. 서투른 봉합(縫合) / 3. 루이 필립 |
| +58 | 27 | 10% | sinhagyi sigtag - juweonjun, bagtaesig, bagh | `s11` | 신학의 식탁 / 들어가며 구약학과 구약신학 |
| +57 | 114 | 5% | Build a Large Language Model (From Scratch - | `sBZ` | 트랜스포머 vs. LLM / GPT-3 데이터셋 상세 |
| +48 | 45 | 5% | sopiyi segye - yosyutain gaadeo | `sAV` | 철학이란 무엇인가? / 이상한 존재 |
| +41 | 11 | 16% | kiruseuyi gyoyug - keusenopon , bagmunjae ol | `s2XJ` | 루 월리스 · 서미석 옮김 · 813쪽 / 한스 크리스티안 안데르센 · 윤후남 옮김 · 1,280쪽 |
| +31 | 8 | 18% | hangseolbaegmuleo - hanggane ddeodoneun ba - | `s20` | 산골짝 개울에 가서 팥을 씻고 있는데 / 동숙하는 중이 앙심을 품고 |
| +31 | 37 | 8% | segyemunhag danpyeonseon 12 peulraeneori o - | `s3H` | The Geranium / The Barber |
| +28 | 199 | 8% | re mijerabeulII - bigtoreu wigo | `s2PV` | 앙드레 모르아/이희영 옮김 / “말(mots)은 영혼을 지나가는 신비스러운 나그네”―위고 |
| +26 | 9 | 29% | poseuteumodeon sidae, eoddeohge yesureul d - | `s4K` | 어째서 이야기가 중요한가 / ‘사실’인 것만으로는 충분하지 않다 |
| +23 | 29 | 10% | badi _ uri mom annaeseo - bil beuraiseun | `s1SZ` | 1 사람을 만드는 방법 / 2 바깥 : 피부와 털 |
| +23 | 14 | 22% | tangbu hananim - tim kelreo | `s77` | 따로 있다 / 교회 |
| +22 | 249 | 3% | re mijerabeulI - bigtoreu wigo | `s1A` | 샤를르 프랑스와 비앵브뉘 미리엘 / 바띠스띤느 |
| +21 | 29 | 8% | sapienseu - yubal harari | `s28` | 독자들에게 / 별로 중요치 않은 동물 |
| +20 | 48 | 6% | mariayi adeul - jingyuseon | `s4R` | 예수는 언제 태어났을까 / 예수의 형제자매 |
| +19 | 24 | 13% | Why Machines Learn_ The Elegant Math Behin - | `s4CS` | calculating average error, 83–84 / ADALINE and, 94 |
| +19 | 25 | 7% | naneun wae segyegidoggyoini doeeossneunga -  | `s7B` | 시더래피즈 / 종교개혁으로 구조되다 |
| +18 | 31 | 21% | AI jegug_ gweonryeog, jabon, nodong - karen  | `s234` | 프롤로그 / 2장 문명화 임무 |
| +17 | 13 | 42% | Pompeii (Harris, Robert) - Harris, Robert | `s25` | Chapter 2 / Chapter 3 |
| +15 | 24 | 16% | soseol ilgneun sinjaege saenggineun il - kae | `s179` | 1. 분별: 헨리 필딩의 《톰 존스의 모험》 / 2. 절제: F. 스콧 피츠제럴드의 《위대한 개츠비》 |
| +15 | 26 | 23% | tim kelreoyi gido - tim kelreo | `s54` | 기도 말고는 달리 도리가 없었다 / 기도만큼 위대한 것은 없다 |
| +15 | 17 | 16% | daineoseuti - tom holraendeu | `s1FT` | 율리우스가 사람들 / 클라우디우스가 사람들 |

✅ = 이미 적용한 책.

## 남아 있는 오탐 유형

조사 도구가 걸러내지 못하고 통과시키는 것들이다. 판정 기준을 더 조이면 진짜 소제목까지
떨어져 나가서, 사람이 보고 거르는 쪽을 택했다.

| 유형 | 예 |
|---|---|
| 출판사 도서목록 (책 뒤) | 키루스의 교육 +41 — `루 월리스 · 서미석 옮김 · 813쪽` |
| 용어집·색인 항목 | 비전공자도 이해할 수 있는 AI 지식 +115 — `• 모라벡의 역설` |
| 화보 저작권 표시 | 비커밍 — `© 퍼블릭 앨라이스, 필 슈미츠 제공` |
| 표기만 다른 중복 | 나니아 연대기 +8 — 목차 `제1장 마법사의 조카` vs 본문 `마법사의 조카` |

거꾸로 **걸러낸** 것들(이건 도구가 잘 잡는다): 소설의 짧은 대사, `1 2 3` 번호,
`◆`·`* * *` 장식 기호, `감사207-208` 같은 색인 항목.

## 곁가지 발견

라이브러리에 **Deep Learning with Python 3판 사본이 둘**인데 하나가 깨져 있다:

```
(673) 목차   1항목  ← 깨진 사본
(675) 목차 350항목  ← 정상. 기기에 올라간 것은 이쪽 (크기 12바이트 차이 = 표지 메타)
```
