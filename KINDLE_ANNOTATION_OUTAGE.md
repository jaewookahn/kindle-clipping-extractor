# 킨들 어노테이션 내보내기 중단 — 조사 기록

**작성**: 2026-09-19 · **상태**: 미해결, 호스트 쪽 조치 소진

---

## 0. 한 줄 요약

**Kindle Colorsoft SE(FW 5.19.6)와 Kindle Scribe 두 대 모두, 어느 시점부터 하이라이트를
`.yjr` 사이드카와 `My Clippings.txt` 어디에도 쓰지 않는다.** 기기 화면에는 정상적으로
보인다.

**원인은 2026-09-19 `;dm` 로그로 완전 규명됐다 (§14.12)**: 어노테이션 저장소가
KSDK 신규 경로 `/mnt/us/system/ksdk/.annotations/<account>/`(SQLite DB)로
**의도적으로 이전된 사양 변경**이다 (`/var/local/ENABLE_INIT_KSDK_SYNC_CLIENT`
존재 = 활성, 구 journal·사이드카 경로는 명시적 차단/격하). 사이드카가 빈
껍데기가 되면서 터지는 `.bad_file` 격리 루프(§14.10)는 이 변경의 **부수
증상**일 뿐 근본 원인이 아니다.

**회수는 2026-09-19 완료됐다 (§16)**: Véra 탈옥(≤5.19.6 지원)으로 루트 획득
→ `ksdk_annotation_v1.db` 전체 확보 — 08-25 이후 **839개 전량 생존**, 2015년
분까지 포함. 파서 작업과 Scribe 회수가 남았다. **Amazon 은
`AnnotationCacheLossMetrics` 로 격리 루프를 계측 중이다 (인지하고 있음).**

---

## 1. 환경

| 항목 | 값 |
|---|---|
| 기기 1 | Kindle Colorsoft Signature Edition (VID 0x1949 / PID 0x9981), FW **5.19.6** |
| 기기 2 | Kindle Scribe (동일 증상, 더 이른 시점에 중단) |
| 호스트 | macOS 24.6.0 (Darwin), Apple Silicon |
| 마운트 | MacDroid → `~/Library/CloudStorage/MacDroid-*/Internal Storage` (File Provider) |
| 직접 접근 | libmtp 1.1.21 (ctypes, `kindle/device.py`) |
| 책 | 전부 **사이드로드** KFX (클라우드 동기화 없음) |

---

## 2. 증상

1. 기기에서 하이라이트를 만들어도 `My Clippings.txt` 에 추가되지 않는다
   (**기기 내부 화면에서도** 마지막 항목이 2026-08-25로 고정).
2. 책의 `.sdr/*.yjr` 사이드카는 **생성되지만 어노테이션 레코드가 0개**다.
3. 킨들이 그 사이드카를 손상으로 판정해 `.bad_file` 로 격리하고, **바이트 단위로 동일한
   파일을 다시 만드는 루프**를 반복한다.
4. 하이라이트 자체는 기기 노트북 화면에 정상적으로 보인다.

---

## 3. 타임라인 (실측)

```
2026-02-04  Scribe 성경전서 첫 하이라이트
2026-05-26  Colorsoft 성경전서 첫 하이라이트
2026-06-25  Scribe 마지막 어노테이션      20:02:03  (성경전서, Location 66,797)
2026-07-26  Colorsoft .mbs 에 whisperstore.migration.status 기록  23:41:55
2026-07-29  '먼저 온 미래' .yjr 마지막 어노테이션  21:29:30
2026-08-04  펌웨어 5.19.6 배포
2026-08-25  Colorsoft 마지막 어노테이션    21:41:34  (성경전서, Location 72,307)
2026-09-19  조사 시점 — 여전히 중단 상태
```

`My Clippings.txt` 마지막 엔트리와 성경전서 `.yjr` 마지막 어노테이션의 시각이
**초 단위까지 일치**(`2026-08-25 21:41:34`)한다 → 두 출력이 같은 쓰기 경로의 산출물.

---

## 4. 파일 수준 증거

### 4.1 정상 `.yjr` (참고)

```
먼저 온 미래 (meonjeo on mirae - janggangmyeong.sdr)
  11,054 B   sha1 76600469881b
  annotation.personal.highlight  77
  annotation.personal.bookmark   12
  annotation.personal.note        4
  + saved.avl.interval.tree(3), font.prefs, next.in.series.info.data, annotation.cache.object
  파싱 결과 93개, 최신 2026-07-29 21:29:30
```

### 4.2 고장난 `.yjr` — **서로 다른 책인데 해시가 같다**

```
권력과 진보   215 B   sha1 622b97111511   어노테이션 0개
belkin iyagi 215 B   sha1 622b97111511   어노테이션 0개   ← 성경 제거 후 새로 만든 하이라이트
내용:
  font.prefs
  next.in.series.info.data
  annotation.cache.object     ← 키만 있고 값 0바이트
```

`.yjr` 과 짝인 `.yjr.bad_file` 도 **동일 해시**. 같은 지점에서 같은 방식으로 반복 실패.

### 4.3 `mbp1` — 쓰다가 끊긴 증거

```
정상 (My Clippings 0.sdr)   128 B   헤더 레코드수 = 04
  sync_lpr / next.in.series.info.data / annotation.cache.object / language.store='ko'

깨짐 (My Clippings.sdr)      98 B   헤더 레코드수 = 03
  sync_lpr / next.in.series.info.data / annotation.cache.object
  → language.store 가 통째로 없음. mbp1 과 mbp1.bad_file 이 바이트 동일
```

헤더의 레코드 개수 필드가 `04 → 03` 으로 다르다. **4개를 쓰려다 3개에서 중단**되었고,
킨들 자체 검증기가 그걸 손상으로 판정한다.

### 4.4 `.bad_file` 누적

```
84개 (조사 초) → 98개 (조사 말). 그중 82~96개가 .yjr
기간: 2025-02 ~ 2026-09 (장기간 누적)
확장자: yjr 82 / mbp1 1 / azw3r 1
```

### 4.5 `cache.db` — 어노테이션 없음

`My Clippings.sdr/*.cache.db` 는 SQLite지만 테이블이 `per_book_speed_cache` 하나뿐
(읽기 속도 통계). 어노테이션과 무관.

### 4.6 `My Clippings.txt` 는 손상되지 않았다

```
827,142 B   sha1 b6963d9899d5   BOM 있음
구분자 "==========\r\n" 2,804개, 전부 CRLF
파일 끝: ... PM\r\n\r\n\r\n==========\r\n   ← 정상 종료
NUL 바이트 0개
북마크 블록 609개가 전부 동일 형태 (마지막 항목 포함)
```

Scribe 쪽 파일은 **사용자 관찰상 잘려 있고 글자가 깨져 있다**(미확보 — 분석 필요).

---

## 5. 검증한 가설

| # | 가설 | 방법 | 결과 |
|---|---|---|---|
| 1 | 우리 파서가 레코드를 놓친다 | `.yjr` 전 레코드 덤프 (99 = 93 어노 + 6 메타) | ❌ 커버리지 완전 |
| 2 | MacDroid가 낡은 파일 내용을 준다 | MTP로 기기 원본 직접 대조 | ❌ 바이트 동일 |
| 3 | `My Clippings.txt` 크기/손상 문제 | 구조 검사 + 이름 변경 + 빈 파일 + BOM 파일 | ❌ 새 파일 생성 안 됨 |
| 4 | 파일 끝이 깨져 append 실패 | 마지막 200B raw 검사 | ❌ Colorsoft는 정상 종료 |
| 5 | Location > 65,535 오버플로 | 첫 초과 이후 기록 추적 | ❌ Colorsoft는 이후 2개월/635개 더 기록 |
| 6 | 성경전서(거대 책)가 원인 | `.kfx`+`.sdr` **완전 제거** 후 다른 책에서 테스트 | ❌ 동일하게 215B 빈 껍데기 |
| 7 | 일시적 오류 | 재부팅 수회 | ❌ 변화 없음 |
| 8 | 기기 개체 불량 | Scribe 대조 | ❌ 두 기기 동일 증상 |

### 성경 가설이 그럴듯했던 이유 (그리고 왜 기각됐는지)

```
두 기기 모두 마지막 어노테이션이 성경전서
Colorsoft 마지막 300개 중 성경 204개(68%), 마지막 날 100개 중 98개
성경만 Location 49,869~72,307 (다른 책 최대 15,920의 4.5배), page 3,758
```

→ 강한 상관으로 보였으나, **성경을 기기에서 완전히 제거한 뒤 전혀 다른 책
(`belkin iyagi`)에서 만든 하이라이트도 동일하게 실패**했다. 상관은 "그 시기에 성경을
읽고 있었다"는 사실의 반영일 뿐이었다.

---

## 6. 확정된 사실

1. **두 출력(`.yjr`, `My Clippings.txt`)이 같은 시각에 함께 멈췄다** → 같은 쓰기 경로.
2. **사이드카는 생성되지만 어노테이션 값이 비어 있다** (`annotation.cache.object` 가 0바이트).
3. **쓰기가 항상 같은 지점에서 끊긴다** (`annotation.cache.object` 직후, `language.store` 전).
4. **책과 무관하다** — 서로 다른 책의 빈 사이드카 해시가 동일(`622b97111511`).
5. **호스트(파서·MacDroid·MTP)는 무고하다** — MTP 원본과 마운트본이 바이트 동일.
6. `.mbs` 에 **2026-07-26 `whisperstore.migration.status`** 기록이 있고 그 이후 정상 활동 없음.

---

## 7. 추정 (미확증)

> **⚠️ 2026-09-19 정정**: 이 절의 핵심 추정("어노테이션이 `annotations.db` 로
> 이전됐다")은 **틀렸다**. `;dm` 로그 판독으로 원인이 코드 레벨로 특정됐다 —
> §14.10 참조. 사이드카는 여전히 정식 저장소이고 쓰기 경로는 살아 있다.
> 킨들이 자기가 쓴 사이드카를 파싱하지 못하는 역직렬화 루프가 원인이다.

- 펌웨어 **5.19.3(2026-03)** 에서 **PDF** 하이라이트가 `My Clippings.txt` 대신 기기 내부
  `annotations.db` 로 옮겨진 것은 문서화돼 있다.
- 저희 증거는 **그 변경이 사이드로드 KFX까지 확대**됐음을 시사한다. 사이드카를 쓰는
  레거시 코드 경로는 남아 있으나 채울 데이터가 없어 빈 파일을 만들고, 자체 검증기가
  그걸 `.bad_file` 로 격리한다.
- **의도된 사양 변경인지 마이그레이션 버그인지는 외부에서 구분 불가.**
  - 사양이면 → 이 저장소의 사이드로드 수집은 영구 불가
  - 버그면 → 수정 펌웨어로 복구 가능

---

## 8. 시도한 접근 경로와 차단 사유

| 경로 | 결과 |
|---|---|
| `NSFileCoordinator` (ForUploading/WithoutChanges/plain) | 무효 — 내용 변화 없음 |
| `NSFileProviderManager.evictItem` | **거부** `NSFileProviderErrorDomain -2001` (제3자 앱 불가) |
| `getattrlistbulk(2)` (Finder가 쓰는 syscall, C로 직접 호출) | readdir과 동일한 낡은 값 |
| 이름 직접 `stat` (파일명이 결정적이라 경로 구성 가능) | 무효 |
| Finder로 폴더 열기 | **디렉터리 목록만** 갱신됨. 파일 내용은 아님 |
| `qlmanage` / 전체 read / atime 갱신 | 무효 |
| MTP 직접 읽기 | **성공** (아래 §9). 단 기기가 안 쓴 데이터는 못 가져옴 |
| Send to Kindle | 개인 문서는 클라우드 동기화 안 됨 |
| 기기 노트북 → 내보내기 | 사이드로드 책은 메뉴 없음 |
| Calibre "Fetch Annotations" | experimental·미지원, 오래된 버그 다수 |
| `calibre-annotations` 플러그인 | `My Clippings.txt` 만 읽음. 2022-07 이후 미갱신 |
| KFX Input 플러그인 (jhowell) | 본문 변환 전용, 어노테이션 미지원 |
| `~usbNetwork` (Dropbear SSH) | **탈옥 필수**. 진단 SSH는 2012년부터 비활성 |
| 탈옥 (Sanctuary) | **5.19.x 미지원** |
| 펌웨어 다운그레이드 | **탈옥이 선행 조건** + 부트로더 강화로 5.18.x 이하 불가 → 순환 |
| `FILE_SYSTEM_ACCESSIBILITY_FLAG` (기기 루트, 0바이트) | 문서 없음. **2026-09-19 실측**: `'1'` 쓰고 재부팅 → 노출 변화 없음, 부팅 시 기기가 0바이트로 되돌림 → 읽기 전용 마커로 확정 (§14.7) |
| `;dm` 디버그 명령 | **미시도** — 로그를 `/mnt/us/documents` 로 덤프. 유일한 잔여 후보 |

---

## 9. 부수적으로 고친 실제 버그 (이 저장소)

### 9.1 MTP 열거 실패

`kindle/device.py` 가 쓰던 `LIBMTP_Get_Filelisting_With_Callback` 이 이 기기에서
**항상 0개**를 반환한다 (세션은 열리고 `LIBMTP_Get_Storage` 도 성공(0)인데 객체만 없음).

→ `_Storage`/`_Device` ctypes 구조체를 추가해 스토리지를 열거하고
`LIBMTP_Get_Files_And_Folders` 로 재귀 순회하도록 교체. 옛 호출은 폴백으로 유지.

```
이전: 폴더 0,   파일 0
이후: 폴더 778, 파일 1130 | 책 311권 | .yjr 142개
```

### 9.2 MacDroid File Provider 디렉터리 캐시 (실재하는 별개 문제)

MTP(기기 원본)는 `.yjr` 보유 `.sdr` 을 **142개** 보는데 MacDroid 마운트는 **119권**만
보여준다. 마운트가 아예 못 보는 `.sdr` 이 **23개** (`Hannah's child`, `Troy`,
`천 개의 찬란한 태양`, `필경사 바틀비` 등).

`fileproviderctl evaluate` 결과 `childItemCount = 1`, `isDownloaded = 1` —
**재열거(re-enumeration) 문제**이며 위 §8의 방법으로는 고칠 수 없다.
현재 `kindle/fileprovider.py` 는 **탐지·안내만** 한다 (고치는 척하지 않음).

### 9.3 기타

- `--book` 이 substring 매칭이라 stem이 다른 책의 접두사이면 여러 권이 딸려옴
  (`성경전서 … ver.1` ⊂ `… ver.1 … 2`). → `--book-exact` 추가.
- 기기에 **대소문자만 다른 중복 폴더** 존재 (`Hannah's child` / `Hannah's Child`).
  macOS 대소문자 무시 파일시스템에서 백업 시 충돌 → 접미사로 분리 처리.

---

## 10. 백업 현황

```
~/kindle_annotation_backup/2026-09-19/
  manifest.json          전 파일 sha1 기록
  documents/My Clippings.txt   827,142 B  sha b6963d9899d5  (기기 원본과 동일 확인)
  <책>.sdr/*.yjr .yjf .bad_file .mbs .mbp1 .cache.db
  _bible_books/          성경 .kfx 2개 (27 MB)
  _test_files/           BOM만 있는 빈 My Clippings.txt 등 테스트 파일

검증: 409/409 해시 일치
```

**2026-08-25 이전 데이터는 완전히 보존돼 있다. 이후 데이터는 기기 내부 DB에만 존재한다.**

---

## 11. 다른 에이전트에게 물을 만한 질문

1. 탈옥 없이 Kindle(FW 5.19.x)의 `annotations.db` 또는 시스템 파티션에 접근하는 방법이 있는가?
2. `;dm` 외에 스톡 펌웨어에서 어노테이션을 덤프하는 디버그 명령/진단 모드가 있는가?
3. 5.19.x 대상 탈옥이 진행 중인가? (Sanctuary 외)
4. `annotation.cache.object` 가 비는 것이 알려진 증상인가? 원인/우회법이 보고된 바 있는가?
5. 사이드로드 책 어노테이션이 `annotations.db` 로 이동한 것이 Amazon의 공식 사양인가,
   아니면 마이그레이션 버그인가?
6. Kindle이 사이드카를 손상 판정해 `.bad_file` 로 격리하는 조건(검증 로직)은 무엇인가?
7. 기기 초기화 없이 어노테이션 저장 경로를 레거시 방식으로 되돌릴 수 있는가?

---

## 12. 재현·검증 방법

```bash
# MTP로 기기 원본 직접 읽기 (MacDroid 종료 필요 — USB 점유 충돌)
python3 -c "
from kindle.device import mtp_direct_session
with mtp_direct_session() as s:
    print(len(s.folders), len(s.files))
    print(len([f for f in s.files if f['name'].endswith('.yjr')]))
"

# 사이드카 레코드 덤프 (0xFE [3B len] [key] 스캔)
#   → annotation.personal.* 가 0개면 빈 껍데기

# File Provider 상태 질의
fileproviderctl evaluate "<경로>"     # childItemCount, isDownloaded 확인

# 백업 무결성 검증
python3 -c "
import json,hashlib,pathlib
B=pathlib.Path.home()/'kindle_annotation_backup'/'2026-09-19'
m=json.loads((B/'manifest.json').read_text())
ok=sum(1 for e in m['files'] if (B/e.get('backup_path',f\"{e['folder']}/{e['name']}\")).exists())
print(ok,'/',len(m['files']))
"
```

---

## 13. 중요 주의사항

> **Colorsoft 는 2026-09-19 회수 완료** (§16) — 이제 초기화해도 데이터
> 손실이 없다. **Scribe 는 아직 미회수** — 초기화하면 2026-06-25 이후
> 하이라이트가 영구 소실된다. Scribe 회수 전까지는 초기화 금지.

---

## 14. 후속 조사 (2차, 2026-09-19 저녁)

### 14.1 백업 무결성 재확인 — 40권 온전

리포 파서(`kindle/parsers/yjr.py`)로 백업 `.yjr` 141개를 전수 재파싱했다.
**40권이 어노테이션을 온전히 보유**하고 있고(성경전서 201개, 데미안 189개 등),
`meonjeo on mirae` .yjr 은 sha1 `76600469881b` — 조사 초에 "건강"으로 잡은
파일과 바이트 동일하다. §10의 "8/25 이전 데이터 완전 보존" 주장은 유효하다.

### 14.2 책별 마지막 어노테이션 시각 — **기기 전역 단일 사건**

전수 스캔 결과, 어떤 책도 `2026-08-25 21:41:34` 이후의 어노테이션을 갖지 않는다.
08-25 09:50 이후 항목이 있는 책은 성경전서·그리스신화 단 2권뿐이며, 나머지는
마지막 사용 시각(07-30, 07-29 …)에 맞춰 흩어져 있다.

→ 중단은 책 단위 스테이지드 마이그레이션이 아니라 **08-25 21:41 경에 일어난
기기 전역 단일 사건**이다. `My Clippings.txt` 마지막 엔트리와 초 단위 일치는
이미 확인돼 있다. 이 시각에 기기가 무엇을 했는지(펌웨어 설치? 동기화?)가
원인의 핵심이며, `;dm` 로그가 이걸 알려줄 것이다.

### 14.3 `whisperstore.migration.status` 전수 검사 — 무죄에 가깝다

- 백업의 `.yjf`·`.mbs` 전부(133개)에 키가 있고, **값은 전부 `00 00 00 00`**.
  `.yjr` 에는 이 키가 없다.
- `kindle_formats` 크레이트(krds.rs)에 따르면
  `WhisperstoreMigrationStatus(bool, bool)` 튜플 구조이며 **저자도 의미를
  모른다고 명시**한다.
- 건강한 파일(07-29 작성)과 고장 이후 파일의 값이 동일하다 → 이 키는 새
  시리얼라이저가 항상 쓰는 플레이스홀더로 보이며, **§6.6의 "마이그레이션 이후
  정상 활동 없음" 해석은 근거가 약해졌다**. 마이그레이션 가설 자체가 기각된
  것은 아니지만, 이 마커는 증거가 되지 못한다.

### 14.4 새 단서 — `userannotlogsDir` (미확인)

스택익스체인지 기록: 킨들은 사용자 파티션 `/mnt/us/system/userannotlogsDir/` 에
`annotation_<epoch>` 어노테이션 **이벤트 로그**를 쓰는 경로가 있다 (구형 모델
기록). 마운트 목록에는 없지만 MacDroid 캐시가 숨겼을 수 있다 (§9.2의 전례).
**MTP 직접 열거로만 확인 가능** — 이전 조사에서 이 경로는 확인되지 않았다.

**→ 2026-09-19 MTP 전수 열거로 종결.** 루트 항목은 정확히 7개
(`audible/`, `screenshots/`, `fonts/`, `documents/` + calibre 파일 2개 +
`FILE_SYSTEM_ACCESSIBILITY_FLAG` 0B)뿐이고 `system/` 은 MTP 응답 자체에
없다. MTP 는 경로 기반 조회가 없어 열거에 없는 객체는 접근 자체가 불가능하다.
즉 5.19.6 기기의 어노테이션 데이터는 USB(MTP)로 닿을 수 있는 범위 **밖**에
있다는 것이 확정됐다.

### 14.5 외부 보고·펌웨어

- MobileRead 2026-05-11 "Annotations seem completely broken" — 5.19.x 대
  어노테이션 파이프라인 변경의 부수 증상 보고 (노트가 단어 단위로 분절되어
  My Clippings 에 중복 기록). 증상은 다르지만 "2026년 펌웨어에서 어노테이션
  취급이 바뀌었다"는 정황을 뒷받침한다.
- 스택익스체인지 해결 사례: 빈 `My Clippings.txt` → **파일 삭제 + 인터넷 연결
  + 하이라이트 생성**으로 복구됨. §5-3 검증은 이름 변경·빈 파일을 시도했지만
  **WiFi 연결 상태를 통제하지 않았다** → 미검증 조합이 남아 있다.
- 펌웨어: **5.19.6 (2026-08-03) 이 최신** (FTVDB Colorsoft 이력 15개 전수 확인).
  수정 버전은 아직 배포되지 않았다.

### 14.6 남은 기기 측 실험 (우선순위 순, 전부 비파괴)

1. **`;dm`** — 홈 화면 검색창에 입력 → 로그가 `/documents` 에 덤프됨 →
   마운트/MTP 로 판독. 08-25 21:41 전후 기기가 무엇을 했는지가 직접 나온다.
2. **WiFi 켠 상태로 새 하이라이트** — `My Clippings.txt` 는 이미 이름이
   바뀌어 있으므로 기기가 새 파일을 만들게 된다 (스택익스체인지 사례 재현).
3. **MacDroid 종료 → MTP 직접 세션** — ① `system/userannotlogsDir` 존재 확인
   ② Scribe `My Clippings.txt` 확보 (§4.6 미확보 항목) ③ 전수 재열거.

### 14.7 `FILE_SYSTEM_ACCESSIBILITY_FLAG` 실측 (2026-09-19)

사용자 승인 하에 값 쓰기 실험을 했다.

- `'1'`(1바이트) 을 쓴 채 재부팅 → 루트 열거 **변화 없음** (같은 7개, `system/`
  없음, 스토리지 여전히 1개)
- 부팅 시 기기가 플래그를 **0바이트로 되돌림** (기기 관리 마커)
- 부팅 시 MTP 객체 DB가 통째로 재생성됨 (객체 ID 전면 재할당) → 사용자 쓴
  값은 지속되지 않음

**결론: USB(MTP) 경로는 이것으로 완전히 소진됐다.** 노출 범위 스위치로 보이던
유일한 후보가 무효로 확인됐고, MTP 는 경로 기반 조회가 없어 열거에 없는
객체는 접근 불가다. 5.19.6 에서 어노테이션 데이터는 USB로 닿을 수 없는
영역에 있다.

### 14.8 "mtime 은 갱신, 내용은 미갱신" — 갱신 소스가 비어 있다

MTP 재열거(2026-09-19 저녁) 결과 **08-25 21:41 이후 mtime 을 가진 `.yjr` 이
7개** 있다. 대표 3개를 파싱:

| 파일 | 크기 | mtime | 내용 |
|---|---|---|---|
| troy_toc_korean | 231B | 09-12 | 클리핑 0 |
| meonjeo on mirae | 11,054B | 09-18 | 93개, 최신 07-29 (sha `76600469881b` 불변) |
| 그리스신화 트로이 | 2,975B | 09-16 | 23개, 최신 08-25 09:50 |

기기가 사이드카를 **다시 쓰지만**(mtime 갱신) **새 어노테이션은 하나도
들어가지 않는다**. §4.2의 "빈 껍데기 생성"을 넘어, 기존 내용은 보존한 채
**갱신 소스가 비어 있는** 그림이다. 새 하이라이트가 레거시 캐시
(`annotation.cache.object`)에 채워지지 않고, 시리얼라이저는 캐시에 남아 있는
것(없으면 빈 값)만 다시 쓴다 — §7의 "채울 데이터가 없어 빈 파일을 만든다"
해석을 강하게 지지한다. 쓰기 경로 자체는 살아 있다.

### 14.9 기기 기준 `My Clippings*` 실태

- `My Clippings.txt` 는 **기기에 없다** — 사용자가 `My Clippings 0.txt` 로
  바꾼 이름이 기기에도 그대로 반영돼 있고, 기기는 새 파일을 만들지 않았다.
- `My Clippings 0.sdr/` — mbp1 128B · mbp1.bad_file 128B · mbs 397B
  (mtime 전부 09-19 01:10~01:25 — 격리 루프 활동 시각)
- `My Clippings 1.sdr/` — cache.db 12,288B (mtime **07-26 23:41:28**),
  mbp1 98B · bad_file 98B · mbs 792B (09-19 01:03~01:07)
- cache.db mtime 이 `whisperstore.migration.status` 기록 시각(07-26 23:41:55)과
  **27초 차이** — 같은 밤의 같은 이벤트다. 07-26 23:41 경 기기가 한 일이
  무엇인지(펌웨어 설치? 동기화?)는 `;dm` 로그가 답할 수 있는 질문이다.

통계: .yjr 141 / .bad_file 98 / 전체 파일 1,113 / 폴더 771.

### 14.10 원인 코드 레벨 특정 — `;dm` 로그 판독

`;dm` 덤프(09-19 01:07~02:22, 마지막 부팅 이후 범위)에 테스트 하이라이트
생성 순간의 실패 시퀀스가 통째로 찍혀 있다:

```
E State:Error::Error loading State, extension ".bad_file" added to file to remove it.
com.amazon.ebook.booklet.reader.sdk.content.util.UnknownFormatConversionException: Invalid Data
    at ObjectReader.readInt(ObjectReader.java:217)
    at AnnotationCacheObject.a(AnnotationCacheObject.java:81)
```

바이너리 분석과 정확히 일치한다 — `annotation.cache.object` 레코드가 **키만
있고 값 0바이트**라 `readInt()` 가 읽을 정수가 없어 `Invalid Data` 가 난다.
루프가 완성된다:

```
1. 책 열기 → 사이드카 읽기 실패 (Invalid Data)
2. .bad_file 로 격리
3. 메모리에 어노테이션 없는 상태로 새 사이드카 작성 → 캐시 값 또 빔
4. 다음 열기 → 1번으로
```

`.bad_file` 98개, 격리본·재생성본 해시 동일, mtime 만 갱신되고 내용 정체 —
모든 증상이 이 루프 하나로 설명된다. Amazon 은 `AnnotationCacheLossMetrics`
(`cache_load_exception, LocalizedInvalidSideCarFileException`) 로 이 실패를
계측·전송하고 있다 — **인지하고 있는 버그**다.

**저장 방향 로그도 확보** — 읽기 실패가 쓰기까지 오염시키는 연쇄가 실증됐다:

```
W AnnotationCacheObject::Notes and Highlights not found while saving annotations   47회
I AnnotationCacheObject::ENOTES serialize() - Serializing 0 total annotations      47회
I AnnotationController#SimpleAnnotationCache::Clearing [0] items ... reseting state to uninitialized
I AnnotationsImpl::Handling AnnotationCachePopulatedEvent with [0] key-value pairs
```

```
① 책 열기 → ② 사이드카 읽기 실패 (Invalid Data)
③ .bad_file 격리 + 캐시 손실 메트릭 전송
④ 메모리 캐시가 빈 상태로 초기화 ("[0] key-value pairs")
⑤ 사용자 하이라이트 생성 (화면에 보임 — UI 레이어)
⑥ 책 닫기 → saveSideCars() → "Serializing 0 total annotations" (47회 전부 0)
⑦ 215B 빈 사이드카 → ②로 복귀
```

새 하이라이트조차 직렬화 대상에서 빠진다 — 캐시가 비었기 때문이다. 따라서
**②(읽기)만 성공하면 사슬 전체가 복원**되고, 온전한 사이드카를 가진 책은
"Serializing 93 → 새 하이라이트 후 94" 로 살아날 수 있다.

**§7 정정**: 사이드카는 여전히 정식 저장소이고 쓰기 경로도 살아 있다(건강한
책은 바이트 동일하게 재작성 확인). 킨들이 **자기가 쓴 파일을 파싱하지 못하는
것**이며, 사양 변경이 아니라 **역직렬화 버그**다. `My Clippings.txt` 정지는
그 자신의 사이드카(`My Clippings.sdr`)가 같은 루프에 빠졌기 때문으로 설명된다.

미확인: 08-25 21:41 최초 손상의 직접 원인 (로그 범위가 마지막 부팅 이후라
그 시점은 덮였다). 펌웨어 설치 중 쓰기 중단이 유력한 후보.

### 14.11 루프 차단 실험 (제안, 승인 필요 — 기기 쓰기)

건강한 사이드카를 MTP 로 주입하면 루프가 끊기고 파이프라인이 복구될 수 있다.

**0단계 — 쓰기 없이 A/B 판정 (먼저 할 것)**:
- 건강한 사이드카(11,054B, 어노테이션 93개)를 가진 `먼저 온 미래` 를 기기에서
  연다 → `;dm` 재실행
  - 로드가 에러 없이 지나가면 **A**: 깨진 사이드카만 못 읽는 것 → 아래 1단계로
  - 또 `Invalid Data` 가 나면 **B**: 역직렬화기 전면 고장 → 수정 펌웨어 대기뿐
- 이어서 그 책에 **새 하이라이트** 생성 → MTP 로 `.yjr` 이 94개로 늘었는지 확인.
  늘면 온전한 사이드카를 가진 책들은 **지금도 수집 가능**하다는 뜻.

**0단계 결과 (2026-09-19 저녁 실행) — A 확정**:

```
Serializing  0 total annotations   47회   ← belkin (깨진 사이드카)
Serializing 93 total annotations   13회   ← meonjeo (온전한 사이드카)
```

역직렬화기는 정상이다. 읽기 실패는 **이미 깨진 사이드카에 한정**된다
(meonjeo 세션에서 `UnknownFormatConversionException` 증가 없음, 캐시 손실
메트릭도 belkin ASIN 하나뿐). "전면 고장" 갈래는 배제.

**그러나 93 → 94 는 되지 않았다**: meonjeo `.yjr` 이 11,054B sha
`76600469881b` 그대로이고, 저장이 13회 일어나는 동안 계속 93 고정.
사용자가 하이라이트를 실제로 만들었는지·기기 노트북에 보이는지는 확인 중.
- 보임 → 새 어노테이션은 캐시/직렬화에 실리지 않는다 (다른 저장소 경유 확정)
- 안 보임 → 생성 자체 실패

**모순으로 남은 관측**: 사용자는 `먼저 온 미래` 하이라이트가 화면에 10장까지
보인다고 했는데 사이드카에는 7/29(6장)까지뿐이다. 화면의 7~10장은 사이드카가
아닌 다른 저장소에서 온다 — §7의 "새 어노테이션은 다른 저장소" 가설이 **신규
데이터에 한해서는** 여전히 살아 있을 수 있다. 0단계에서 로드가 A로 판정돼도
새 하이라이트가 사이드카에 안 실린다면 이쪽이 맞다.

1. **1권 선행 검증 (기기 쓰기)** — 백업에 건강한 `.yjr` 이 있는 책 하나를 골라,
   기기의 빈 껍데기(215B) + `.bad_file` 을 백업본으로 교체 → 열기 → 하이라이트
   → MTP 재열거로 성장 확인
2. 성공 시 전권 확대 + `My Clippings.sdr` 복원 (My Clippings.txt append
   경로 부활 시도)

실패(주입 후에도 새 어노테이션이 안 실림)는 "새 어노테이션이 사이드카 경로로
아예 안 온다"는 뜻이며, 그 경우 남은 것은 수정 펌웨어 대기뿐이다.

**주의**: 08-25 이후 어노테이션 자체는 회수 불가다 (기기 화면 상태로만 존재).

### 14.12 사양 변경 확정 — KSDK 어노테이션 저장소

`;dm` 로그에서 새 저장소와 활성화 스위치가 직접 확인됐다:

```
I ReaderUtils::isKSDKAnnotationsEnabled: file=/var/local/ENABLE_INIT_KSDK_SYNC_CLIENT, exists=true
I AnnotationJournal::KSDKAnnotations enabled - skipping Kindle journal write
I KSDKSyncCommons::FileUtils: Directory already exists at path /mnt/us/system/ksdk/.annotations
I KSDKSyncCommons::FileUtils: Directory already exists at path /mnt/us/system/ksdk/.annotations/amzn1.account.AG4IK4ZSHJ4AVFVDXU4ZYLIGSAKA
I KSDKAnnotations::AnnotationsCRUDModule: USB mode is disabled, opening DB connection ...
I KSDKAnnotations::AnnotationsCRUDModule: USB mode is enabled, closing DB connection ...
I KSDKAnnotations::BookStateManager: Found 9 SyncStateRecord entries to load into the SyncStateManager from disk
I ReaderSDKImpl::ENOTES loadLegacySidecar() - no annotations to import
I KSDKAnnotations::WeblabProvider: isOnDeviceMigrationEnabled is true
W ObjectReader::Skipped reading object with key: whisperstore.migration.status
```

확정 사항:

1. **새 저장소** = `/mnt/us/system/ksdk/.annotations/<amzn1.account.…>/` 의 SQLite DB
2. **활성화 스위치** = `/var/local/ENABLE_INIT_KSDK_SYNC_CLIENT` (파일 존재 = 활성)
3. **구 경로는 명시적 차단 또는 격하** — `skipping Kindle journal write`,
   `loadLegacySidecar()` (읽기 전용, 신규 기록 없음)
4. **§7 "사양 변경" 갈래 확정.** §14.10의 "읽기 실패 → 빈 캐시 → 빈 직렬화"
   사슬은 **이미 빈 껍데기가 된 사이드카(belkin 계열)에서만 도는 국소 부수
   증상**으로 격하된다 — meonjeo 가 93개를 정상 직렬화한 것이 반증이다.
5. **USB 연결 시 기기가 DB 연결을 닫는다** — USB 세션 중에는 KSDK 저장소가
   닫혀 있는 상태다.

접근은 여전히 막혀 있다 — 경로가 `/mnt/us`(documents 와 같은 사용자
파티션)인데도 MTP 루트 화이트리스트가 `documents`/`audible`/`fonts`/
`screenshots` 4개뿐이고, `system/` 은 응답에 없고 `.annotations` 는 점 숨김
폴더다. **단, 탈옥 시 목표가 단일 경로 하나로 압축됐다** — 같은 파티션이라
회수 난이도는 낮다.

**CRUDApi 로그로 최종 확정 (주장 A 완결)**:

```
KSDKAnnotations::CRUDApi: Successfully processed operation: createAnnotation   ×5
AnnotationsImpl: Handling AnnotationsChangedEvent with [1] annotations created, [0] updated, [0] deleted
```

새 하이라이트가 KSDK CRUD 로 들어가 성공 처리되는 동안, 같은 시간대 사이드카는
`Serializing 93` 으로 불변 — **두 경로가 분리돼 있다는 직접 증거**다.

## 15. 추출 경로 탐색 (2026-09-19 저녁, 사용자 지시)

목표: `/mnt/us/system/ksdk/.annotations/amzn1.account.…/` (SQLite 추정) 꺼내기.

### 15.1 netlog 분석 — 빈손

`all_netlog_logs`(2.5MB, 29,650줄)는 WiFi 연결 관리 로그뿐. HTTP 요청·헤더·
페이로드 없음 (`POST`/`GET`/`annot`/`ksdk`/`datamate` 0건).

### 15.2 Datamate GraphQL 엔드포인트 — 직접 질의는 순환

- `https://sync.datamate.kindle.amazon.dev/graphql` (212회) — 기기 동기화
  백엔드. 내부 도메인.
- **FastSync ≠ KSDKAnnotations**: `FastSyncClientSDK` 는
  `/mnt/us/system/.fastSync_v1/` 에 사전·단어장·BookInfo 만 동기화한다.
  어노테이션 스키마는 확인되지 않음. §14.13 의 "Datamate 오류 = 어노테이션
  동기화 실패" 추정은 **철회** (FastSync 쪽일 가능성이 큼).
- 인증 토큰이 기기 내부에만 있고 로그에 미기록 → 계정 토큰 직접 질의는
  토큰 확보가 선결인데 그게 다시 기기 접근을 요구한다 (순환).

### 15.3 Amazon 개인정보 데이터 요청 — 진단용 한정

`Devices.Kindle.ReadingActions.zip` 에 하이라이트 **행위** 기록은 있으나
본문 텍스트 포함은 확인되지 않는다. 회수 경로로는 낮은 가치 (소요 최대 30일).

### 15.4 탈옥 — **Véra (2026-08-10) 가 지원 범위 안** 🎯

- MobileRead 공식 스레드: "Vera - KT5/PW5/KT6/PW6/**CS**/**KS**/KS2 up to 5.19.6"
- **CS (Colorsoft) 5.18.1–5.19.6** — Colorsoft SE 5.19.6 은 **상한선 안**.
  Scribe(KS 5.17.1– / KS2 5.17.3–) 도 5.19.6 까지 지원.
- 브라우저 기반 (kindlemodding.org/vera), PC 불필요, 블랙리스트 기기 가능,
  hotfix 자동 설치 + OTA 자동 차단. wiki: 전원 버튼 재부팅은 안전.
- ⚠️ **5.19.7 이 나오기 전에** 진행해야 한다 (현재 5.19.6 이 최신. 기기가
  OTA 를 받으면 창이 닫힘). 진행 전 "저장소 채우기"로 업데이트 방지 권장.
- 탈옥 후: 셸 접근 → `/mnt/us/system/ksdk/.annotations/` 복사 — 목표 단일
  경로. 사용자 파티션이라 난이도 낮음.

### 15.5 권장 순서

① MTP 백업 갱신 (탈옥 전 스냅샷) → ② Kindle 앱 로그인 확인 (무탈옥 최종
판정, 5분) → ③ Véra 탈옥 → ④ KSDK DB 복사·분석

## 16. 회수 완료 — KSDK DB 전체 확보 (2026-09-19 새벽)

### 16.1 방법

1. **Véra 탈옥** (브라우저 기반) — `privesc_marker.txt` 에 `uid=0 euid=0`.
   루트 획득 확정. 탈옥 전 백업 419개 파일 해시 검증 완료.
2. MTP 는 폴더 생성·`.bin` 업로드 불가 → **MacDroid 마운트로 파일 배치**.
3. MRPI 는 실행됐으나 kindletool 이 `libz.so.1 internal error` 로 실패 —
   5.19.x 에서 커뮤니티 ELF 바이너리가 못 도는 알려진 문제 (KUALA 도 동일).
   **MRPI/KUAL 은 우회했을 뿐 미해결** (원인은 §16.6 — noexec 아님,
   라이브러리 비호환).
4. **`;log` 스크립트 복제**: `;log mrpi` 가 이미 `mrinstaller.sh` 를
   실행한다는 사실을 이용 — 그 스크립트를 루트 셸 스크립트로 **교체**(원본
   보관) → `;log mrpi` 재입력 → `id=uid=0(root)` 확인, `cp_rc=0`.
5. `/mnt/us/system/ksdk/.annotations/<계정>/` 전체 복사 → MTP 다운로드.
   보관: `~/kindle_annotation_backup/ksdk-recovered-20260919-0331/`
   (기기 원본과 sha 일치 `1dcf8b5eab0398b7`).

### 16.2 확보한 DB

```
ksdk_annotation_v1.db  3,682,304 B  SQLite 3
```

| 테이블 | 행 | 의미 |
|---|---|---|
| `nonsyncable_annotations` | 3,353 | **사이드로드 어노테이션 전량** — NonsyncableAnnotationDAO 의 실체. "nonsyncable" = 클라우드 미전송 |
| `server_view` | 180 | 클라우드 동기화 대상 (구매본) |
| `key_value_storage` | 171 | 책별 `MigrationStatus-<ASIN>-…=MIGRATED` + `ANNOTATION_RECOVERY_STATUS=SUCCESS` |
| `book_state` | 11 | 책 상태 |
| 동기화 부기 테이블 | — | `delta_sync_tokens`·`legacy_delta_sync_tokens`·`local_edit`·`staging_server_view`·`migration_states`(=2\|0, 완료) |

기간 **2015-07-04 ~ 2026-09-19**, 책 **147권**. **2026-08-25 21:41 이후
839개 전량 생존** — "회수 불가"로 기록했던 구간 포함. 마이그레이션이
과거분까지 옮겨왔다는 §14.15 추정도 확인 (2015년 기록 존재).

### 16.3 dataset 코드 해독

| dataset | 의미 | 행 | payload 단서 |
|---|---|---|---|
| 1 | **highlight** | 2,204 | `type:HIGHLIGHT`, `json_metadata.mchl_color` (본문 텍스트 없음 — 위치만, KFX 슬라이싱 필요) |
| 2 | **bookmark** | 668 | `kindle.bookmark-*`, mchl_color |
| 3 | **note** | 105 | `kindle.note-*`, `json_metadata.note_text` (노트 본문 포함) |
| 7 | waypoint | 189 | `kindle.waypoint-*` (My Clippings 0 포함) |
| 8 | last_read | 147 | `kindle.local_most_recent_read` |
| 18 | popularHighlight | 40 | 구매본 인기 하이라이트 |

- 위치 형식 (실측 정정): `longPosition` = 9바이트 바이너리 base64 —
  `[0x01][LE u32 eid][LE u32 eid_offset]` (KFX `$246` eid 쌍).
  `shortPosition` 은 **KFX char offset (PRE-KL)** — '먼저 온 미래' DB 값과
  .yjr char offset **92/92 완전 일치, YJR 에만 있는 항목 0**. DB 전용 157개는
  YJR 마지막 offset(106,804) **직후 구간(113,365~247,038)** 에서 이어진다 —
  "화면엔 10장까지 보인다"고 한 바로 그 구간.
- 좌표계: PRE-KL → `parse_yjr()` 출력과 동일. **기존 fill 파이프라인
  (text→pages→chapters→KL) 그대로 재사용** + fingerprint 가 기존 PRE-KL
  체계와 일치해 `sync_kfx` seen_keys·Notion 상태 파일과 호환 (중복 업로드
  없음). 단 `My Clippings.txt`(POST-KL)와의 교차 dedup 은 여전히 불가 —
  미해결 이슈 1 그대로 (앞서 "dedup 에 유리"라고 한 판단은 철회).
- `type` 필드가 `"HIGHLIGHT"`/`"NOTE"`/`"BOOKMARK"` 로 **명시적** — dataset
  코드 추론 불필요. `json_metadata` 는 **문자열 안의 JSON**(이중 인코딩).
  `book_id` = `<ASIN>-<contentType>-<guid>-0`.
- 책 매칭: `book_data.asin` + `contentType` → .sdr 폴더 ASIN
  (`APNXInfo.asin`) / 제목 캐시와 결합.
- 시간: `created_time` (ms epoch) → `YYYY-MM-DD HH:MM:SS` 정규화 그대로.
- 색상: `json_metadata.mchl_color` → `Clipping.content` 접두사 규칙 재사용.

### 16.4 정정

- §0·§8·§13 의 "회수 불가" → **회수 완료** (Colorsoft).
- §14.13 클라우드 회수 후보 → **기각 확정**: `nonsyncable_annotations` 라는
  이름이 뜻한 것은 "클라우드 동기화 대상 아님". 사이드로드 어노테이션은
  구조적으로 클라우드에 안 올라간다. Kindle 앱 확인은 불필요해졌다.
- 동료 세션의 "NonsyncableAnnotationDAO 는 My Clippings 읽기 진행률용" 추정도
  **틀림** — 사이드로드 어노테이션 저장 테이블이 맞다.

### 16.5 남은 것

- ~~파서~~ **완료 (2026-09-19)** — `kindle/ksdk.py` + `tests/test_ksdk.py`
  (14개, 임시 SQLite fixture). 전체 테스트 109개 통과. 회수본 2,977건
  (highlight 2,204 + bookmark 668 + note 105) 위치·날짜 전부 파싱. E2E:
  '먼저 온 미래' 250건 → fill_* 체인으로 본문 239/250(11은 북마크)·챕터
  250/250·페이지 250/250 — 사이드카에 없던 7~10장 구간까지 복원.
- **통합 (남음)**: `sync_kfx.py` 에 KSDK 소스 연결. **필수 조건 둘 (실측)**:
  ① book_title 은 제목 캐시로 채운 뒤 fingerprint — ASIN 플레이스홀더로는
  교집합 0/93, 전량 중복 업로드됨
  ② **북마크는 `location_end = None`** (YJR 관례) — end=start 로 채우면
  fingerprint 가 12건 갈라짐. 파서에서 수정 완료, 회귀 테스트 3개 추가.
  제목+None 보정 후: 교집합 93/93, YJR 전용 0, 신규 157 — 기존 동기화분
  전부 skip 확인.
- **Scribe**: 탈옥 안 됨 — 같은 절차(Véra + `;log` 교체)로 Scribe 의 KSDK DB
  회수 필요. 펌웨어 5.19.6 이하면 창은 열려 있다
- **MRPI/KUAL ELF 문제**: 미해결 (셸 스크립트 경로로 우회만 함, 원인은
  §16.6 — noexec 아님, 라이브러리 비호환)

### 16.7 WiFi 푸시 + sync_kfx 통합 완료 (2026-09-19)

- **책 매칭 난제 해결**: `extract_kfx_metadata()` 가 `asin`(KSDK
  `book_data.asin` 과 동일)·`cde_content_type` 을 반환하도록 확장, 제목
  캐시에 함께 저장 — 제목 채우기·책 매칭이 구조적으로 보장된다.
- **옛 캐시 함정**: asin 필드 추가 전에 만들어진 캐시는 hit 시 `asin=""` →
  KSDK 모드에서는 캐시된 asin 이 비면 그 책만 강제 재추출. `get_cached()` 는
  옛 항목에 `asin: ""` 을 채워 하위호환 (`test_old_cache_entry_without_asin`).
- **dry-run (WiFi 수신본, 책 34권)**: '먼저 온 미래' **+157 new / skip 93**
  — §16.5 예측치와 정확히 일치. KL 변환·색상·본문·챕터 정상.
- 구현: `kindle/wifi.py`(WifiReceiver — LAN 바인드·1회용 토큰·수신 후 자동
  종료, WAL/SHM·ota_status.txt 허용), `tools/ksdk_push.sh`(기기),
  `tools/ksdk_receiver.py`(독립 실행), `sync_kfx.py` 에 `--wifi/--wifi-port/
  --wifi-bind/--wifi-timeout/--ksdk-db` + `process_book(ksdk_by_asin=…)`.
  테스트 115개 통과.
- **WiFi 는 MacDroid 와 공존** (USB 배타는 MTP 뿐) — "어노테이션은 WiFi,
  KFX 본문은 마운트" 조합이 성립. dry-run 도 그 상태로 수행.
- KSDK 모드는 **YJR 유무로 책을 거르지 않는다** (사이드카 고아화 대응,
  `.sdr` None 방어 포함).
- 미확인: ① 기기에 남은 `ota_status.txt` (다음 푸시 때 수신 예정)
  ② dry-run 34권 vs DB 전체 147권 차이 — 147권은 waypoint·last_read
  (dataset 7·8) 포함 수치로 보임.

### 16.8 후속 정정 (2026-09-19 오후)

**MacDroid 마운트 쓰기는 기기에 도달하지 않는다** — MTP 실측으로 확정.
GUI 가 04:05 에 기기 스크립트의 `PUSH_URL` 을 마운트에 갱신했지만, 9시간 후
MacDroid 종료 뒤 MTP 로 읽은 기기 원본은 **옛 토큰 그대로**였다. 13:47 의
재배포 시도도 마찬가지로 미도달. 읽기 방향의 낡은 캐시(§9.2)와 짝을 이루는
**쓰기 방향 캐시** — File Provider 는 로컬 캐시에만 쓰고 기기에 플러시하지
않는다. (그래서 04:05 `;log mrpi` 는 옛 토큰으로 404 → USB 폴백만 동작했다)

- **기기 파일 변경은 MTP 직접 쓰기만 신뢰 가능**: `LIBMTP_Delete_Object` +
  `LIBMTP_Send_File_From_File` 로 교체 성공 (sha 검증, 중복 없음 확인).
- **토큰 동적화로 경쟁 제거**: 기기 스크립트는 `BASE_URL`(IP:포트)만 갖고
  실행 때 수신기의 `GET /token` 에서 토큰을 받는다 — 스크립트를 매번
  갱신할 필요가 없다. (`kindle/wifi.py` + `tools/ksdk_push.sh` 개정,
  tests/test_wifi.py 7개 추가)
- **배포 후 주의**: MacDroid 를 다시 켜면 로컬에 캐시된 옛 스크립트 내용을
  기기에 밀어 MTP 배포본을 덮을 수 있다. 재기동 후에는 MTP 로 재검증 필요.
- 기기 실행 로그(04:05) 회수 확인: `uid=0(root)`, curl 404(옛 토큰) →
  USB 폴백 복사 성공. 폴백본을 MTP 로 당겨온 결과 sha `1dcf8b5eab0398b7`
  — 기존 회수본과 동일 (그 이후 새 어노테이션 없음).
- `ota_status.txt` 는 옛 기기 스크립트에서 `exit 0` 뒤의 죽은 코드였다.
  새 스크립트에 OTA 점검 블록을 push **앞**에 되살려 넣었고, 전송은 하되
  **`pushed` 카운터는 DB 전용**으로 유지한다 (OTA 만 성공해 DB 가 실패한
  경우 USB 폴백이 죽는 결함을 배포 직전 검토에서 잡았다).

**기기 스크립트 최종 배포 (MTP, 검증 완료)**:

```
저장소본        sha1 0fae7533f59346b9ab1ef4f17e480084ee9e6f76
배포본(BASE_URL 채움)  sha1 6dfec7fe8dcac9146d8d0a3f3c0846f0a70127de  5656B
MTP delete/send rc=0, 기기 재다운로드 sha 일치 (별도 프로세스 검증)
```

- **MTP 세션 주의 (실측)**: 같은 프로세스에서 `MTPDirectSession` 을 두 번
  열면 안 된다 — `close()` 가 의도적으로 `LIBMTP_Release_Device` 를 부르지
  않아 인터페이스가 잡힌 채로 남고, 두 번째 열기는
  `libusb_claim_interface() = -3`. 검증은 별도 프로세스로 할 것.
- 남은 미검증: MTP 전송본의 **실행 권한** — `;log mrpi` 재시도에서
  `push.log` 가 아예 안 생기면 exec 차단이 원인.
- MacDroid 재기동 후에는 배포본이 캐시로 덮이지 않았는지 MTP 재검증 필요
  (MacDroid 를 끈 상태에서만 MTP 가능).
- 기기 원상복구: mrinstaller.sh 원본 복원 등 (동료 세션 진행 중)

### 16.6 기기 환경 조사 — WiFi 푸시 가능

- 네트워크 도구: wget·curl·nc·telnet·ftpget/ftpput·openssl·sh 존재.
  ssh·dropbear·python 없음 — 그러나 **필요 없다** (기기가 curl 로 직접 푸시).
- wlan0 `192.168.0.205/23`, 기본 라우트 정상. 리스닝 포트는 로컬 전용
  (9101 java, 20450 webreader) — 외부 노출 없음.
- **kindletool 실패 원인 확정 — noexec 아님**: `/tmp`·`/var/tmp`·
  `/var/local`·`/mnt/us` 네 위치 전부 스크립트 실행 성공. `/mnt/us` 는
  fuse.fsp (`nosuid,nodev`, **noexec 없음**). → 커뮤니티 ELF 바이너리와
  5.19.x 런타임 라이브러리의 **비호환**이 원인. noexec 가설 기각.
- `/var/local` 은 ext4 + 실행 가능 → 5.19.x 용으로 빌드된 dropbear 가
  나온다면 그쪽에 두는 우회가 유효 (지금은 불필요).
- **WiFi 푸시 구상**: Mac 에서 LAN 수신 서버 기동 → 기기에서 `;log mrpi`
  트리거 → `curl -T ksdk_annotation_v1.db http://<Mac>:PORT/`. USB·
  MacDroid 불필요. 트리거는 여전히 수동(기기 검색창).
  ⚠️ USB 비연결 상태에서는 DB 가 WAL 모드로 열려 있을 수 있으니
  `-wal`/`-shm` 파일이 존재하면 함께 전송할 것.

### 14.15 외부 반향 검증 (사용자 지적) — 전면 차단이라면 반향이 커야 한다

"My Clippings.txt 가 이 정도로 광범위하게 쓰였는데 전면 차단이면 반향이
없을 리 없다"는 지적에 따라 외부 보고를 조사했다.

- **전면 중단 보고는 없다.** 한국 커뮤니티에서 5.19 관련 "My Clippings 에
  저장 안 됨" 보고 없음. 영어권에서도 "완전히 안 써진다"는 보고는 없다.
- **단계적 악화 보고는 있다 (시간순)**:
  - 2026-03 (5.19.2): 노트가 타이핑 중 수 초마다 저장되어 **단어 단위로
    분절** — MobileRead, 3대 기기 재현. **하이라이트는 여전히 정상.**
  - 2026-05: 하이라이트가 **불완전**해지고 노트 분절 지속
    ("Annotations seem completely broken")
- 우리 기기(Scribe 06-25, Colorsoft 08-25 중단)는 공개 보고보다 **앞서
  있다** — 저널 경로가 완전히 죽는 단계에 도달한 소수 코호트로 보인다.
  로그의 `isOnDeviceMigrationEnabled is true`(웨브랩)와 합치한다.

**결론 정정**: "모든 기기의 사양 변경"이 아니라 **웨브랩 기반 단계적 전환**
이다. 대부분의 기기는 아직 영향을 받지 않아 대규모 반향이 없다.

**웨브랩 로그 직접 증거 (2026-09-19)**:

```
getTreatment('KSDKANNNOTATIONS_1379504')               -> 'T1'
getTreatment('KSDKANNOTATIONS_BOOK_DOWNLOADS_1388425') -> 'T1'
getTreatmentNoTrigger('KINDLE_FASTSYNC_MIGRATION_EINK_VOCAB_BUILDER_1371786') -> 'T1'
getTreatmentNoTrigger('KINDLE_FASTSYNC_MIGRATION_EINK_SETTINGS_1367860')      -> 'T1'
getTreatmentNoTrigger('KINDLE_FASTSYNC_MIGRATION_EINK_APPS_READER_KFT_DATA_1368783') -> 'C'
```

- 어노테이션 관련 웨브랩 4개 전부 **T1(처리군)**, KFT_DATA 만 **C(대조군)** —
  같은 기기 안에서도 웨브랩별로 갈린다. "기기 일괄 사양 변경"이 아니라
  **웨브랩 단위 부분 전환**의 직접 증거.
- 다른 사용자 판정법: 자기 기기에서 `;dm` 후
  `getTreatment('KSDKANNOTATIONS…)` 값을 확인하면 어느 코호트인지 스스로
  판정할 수 있다.

**마이그레이션 완료 상태 (새 사실)**:

```
OnDeviceMigrationController: handleMigrationAlreadyCompleted: DB state is MIGRATION_COMPLETED.
OnDeviceMigrationController: runMigration: background thread exiting (state=DONE).
```

- 진행 중·중단이 아니라 **완료**다. "임시 이상 상태라 되돌아온다"는 가능성은
  없다 — 이 기기는 KSDK 로 전환 완료됐고 레거시 경로로 복귀하지 않는다.
- 완료된 마이그레이션은 기존 어노테이션을 새 DB로 옮겼을 것 — KSDK DB 에는
  8/25 이후분뿐 아니라 **그 이전 것까지 전량** 들어 있을 가능성이 높다.

### 14.14 반박 검증 (동료 세션 요청) — 결론 유지, 세부 정정

**주장 A (사양 변경): 확정 유지, "동결"은 "고아화"로 정정.**
- 레거시 쓰기 경로는 살아 있다 (`saveSideCars()` 16회, meonjeo 93 재작성 13회).
  "동결"이 아니라 **소스만 갱신되지 않는 고아 경로**다.
- `My Clippings.txt` 정지 기전은 사이드카 루프보다 직접적이다 —
  `AnnotationJournal: skipping Kindle journal write` 로 **저널 기록이
  스킵**되면 append 대상 자체가 사라진다. 사이드카 루프는 부차적이다.
- 두 기기의 중단 2개월 차이는 **웨브랩**(`isOnDeviceMigrationEnabled is true`)
  의 단계적 적용으로 설명된다 — 펌웨어 버전 차이보다 설득력 있는 후보. 미확증.
- belkin/meonjeo 비대칭 재작성은 "캐시에 남은 것이 쓰인다"로 설명된다:
  meonjeo 는 93을 로드해 93을 쓰고, belkin 은 0을 로드해 0을 쓴다. 정책
  차이가 아니라 캐시 내용 차이.

**주장 B (격리 루프는 부수 증상): 확정 유지.**
- `.bad_file` 이 2025-02 부터 누적된 것은 반박이 아니다 — 격리 루프 기전은
  KSDK 이전부터 있었고, 그때는 캐시가 차 있어 **재작성본도 온전해 루프가
  무해**했다. KSDK 이후 캐시가 비면서 루프가 파괴적으로 변했다.

**남은 미확증 (솔직한 약점):**
1. KSDK 활성화 시각 — 로그 범위가 오늘뿐이라 직접 증거 없음
2. ~~새 하이라이트가 실제로 KSDK DB 에 INSERT 되는지~~ **해소** —
   `CRUDApi: Successfully processed operation: createAnnotation` ×5 +
   `AnnotationsChangedEvent [1] created` (§14.12)
3. ~~`SyncStateRecord` 9건의 정체~~ **해소** — 값이 9→10→10→9 로 변동,
   마이그레이션 이후 열린 책만 등록 (§14.13)
4. `whisperstore.migration.status` 와의 관계 — 여전히 불명 (저위험)
5. `Datamate invalid error section` 이 동기화 실패를 뜻하는지 — Kindle 앱
   로그인 확인이 가장 빠른 판정 (§14.13)
6. **`My Clippings.txt` 정지 기전 — "구 저널 미호출"이 가장 근접한 서술.**
   부팅 구간 75분(책 열기 8회·하이라이트 다수) 동안 `My Clippings.txt` 에
   **기록하는** 코드 경로가 단 한 번도 호출되지 않았다. 파일명 언급 91건은
   전부 이 파일을 PDOC 문서로 열고 인덱싱하는 맥락이고, writer 계열
   컴포넌트(`ClippingWriter` 등)는 로그에 존재조차 하지 않는다.
   `AnnotationJournal` 의 skip 로그(11건)는 `last_read`/LPR 전용이라
   하이라이트 정지를 직접 설명하지 못한다. 호출 지점이 제거됐는지 조건부
   비활성인지는 로그로 구분 불가다.

**실험 프로토콜 주의**: USB 연결 중에는 기기가 KSDK DB 연결을 닫는다
(`USB mode is enabled, closing DB connection`). 하이라이트 테스트는
**USB 를 뽑은 상태**에서 해야 의미가 있다.

### 14.13 새 회수 후보 — KSDK 클라우드 동기화 (실재 확인됨)

`Found 9 SyncStateRecord entries` — 동기화 클라이언트가 살아 있고 디스크에
상태 레코드가 있다. KSDK 가 어노테이션을 Amazon 클라우드(whisperstore)로
업로드한다면, **WiFi 연결 후 동기화 → Kindle 앱 / read.amazon.com 노트북에서
8/25 이후 하이라이트가 보이는지** 확인할 가치가 있다. 사이드로드(PDOC) 책의
어노테이션 동기화 여부는 미확정이지만, USB 회수 경로가 전부 닫힌 지금
유일하게 남은 무탈옥 회수 후보이다.

**후속 로그로 실재 확인 (2026-09-19 저녁)**:

```
InMemoryNotebookStateManager: Publishing sync toggle event to platform package with sync enabled
KSDKSyncCommons::error_utils:ERROR] Received a response from Datamate with invalid `error` section.  (20회)
DownloadsBridgeImpl: WiFi Connected Event Received - Connection established  (114회)
WhisperSyncV1Impl:UploadJournal:status=noJsonJournal:No json journal entries to upload, aborting
```

- 동기화 클라이언트가 **실제로 Amazon 백엔드(Datamate)와 통신 중**이다.
  다만 응답의 `error` 섹션 파싱 오류가 20회 — 동기화 실패인지 경고인지 미구분.
- 구 저널은 비어 있어 업로드할 것이 없다 (`noJsonJournal`) — 주장 A와 일관.
- `SyncStateRecord` 값은 고정이 아니라 **9→10→10→9 로 변동** — 141권 전체가
  아니라 **마이그레이션 이후 실제로 열린 책만 등록**되는 것으로 보인다
  (오늘 belkin·meonjeo 등만 열림). "전면 이전인데 왜 9개뿐인가" 반론은 무효.
- 판정 방법: ① 같은 계정으로 **Kindle 모바일/데스크톱 앱** 로그인 → 그 책의
  하이라이트가 내려오는지 (앱 로컬 DB는 기기보다 접근이 쉬움)
  ② read.amazon.com/notebook (PDOC 은 기존 상식으론 안 보임, KSDK 는 새
  시스템이라 달라졌을 수 있음)

**전망 재검토 — `NonsyncableAnnotationDAO` 해석 정정**:

```
KSDKAnnotations::NonsyncableAnnotationDAO: No datasets provided, returning all nonsyncable annotations
```

이 줄의 대상 `book id *87a998…` 은 후속 로그로 **"My Clippings 0.txt" 파일
자체**의 읽기 진행률임이 확인됐다. 사이드로드(PDOC) 하이라이트가 nonsyncable
로 분류된다는 근거는 **아직 없다** — 앞선 "전망 하향" 판단은 근거가 약해
철회한다. 클라우드 회수 전망은 **미정**으로 되돌리고, 판정은 여전히 Kindle
앱 로그인 확인에 달려 있다.

---

## 17. KSDK DB 구조 조사 — 기기 식별·ASIN 기대치 정정 (2026-09-20)

통합 설계(리딩총괄) 쪽에서 "KSDK DB 에 `book_id`(ASIN 포함)와 `device_name` 이
같이 있으니 **안정적 book_key 와 기기 식별이라는 두 난제를 동시에 해결한다**"는
기대가 있었습니다. 회수본 DB 를 전수 조회해 확인한 결과 **그 기대는 성립하지
않습니다.** 설계 우선순위에 직접 영향이 가므로 근거를 남깁니다.

조회 대상: `~/kindle_annotation_backup/ksdk-recovered-20260919-0331/.annotations/
amzn1.account.…/ksdk_annotation_v1.db` (Colorsoft 회수본, 3,353행)

### 17.1 `device_name` 은 클리핑에 붙어 있지 않습니다

`nonsyncable_annotations` 를 dataset 별로 전수 집계한 결과입니다.

| dataset | 종류 | 건수 | `device_name` 보유 |
|---|---|---|---|
| 1 | HIGHLIGHT | 2,204 | **0** |
| 2 | BOOKMARK | 668 | **0** |
| 3 | NOTE | 105 | **0** |
| 18 | POPULARHIGHLIGHT | 40 | **0** |
| 7 | waypoint | 189 | **0** |
| 8 | last_read | 147 | 147 (전부) |

**하이라이트·북마크·노트에는 기기 정보가 한 건도 없습니다.** `device_name` 이
붙는 것은 "읽던 위치"(last_read) 레코드뿐이고 그건 클리핑이 아닙니다.

→ **클리핑별 기기 식별은 KSDK 로도 불가능합니다.** 삭제 탐지 금지 사유가
유지되는 정도가 아니라, 해소 경로 하나가 막힌 것입니다.

### 17.2 `device_name` 값 자체가 불안정합니다

```
nonsyncable_annotations : "Local", "another device"
server_view             : "Jae-wook's Kindle Scribe", "Jae-wook's Kindle Voyage",
                          "Local", "another device"
```

- **사용자 지정 이름**이라 기기 이름을 바꾸면 값이 바뀝니다. 불변 식별자가 아닙니다.
- `Local`·`another device` 같은 **플레이스홀더가 섞입니다.**
- 실제 기기명이 나오는 것은 `server_view`(클라우드 동기화된 읽기 위치) 22건뿐입니다.
- **불변 식별자(시리얼 등)는 이 DB 에 없습니다** — `key_value_storage` 전수 확인,
  `ANNOTATION_RECOVERY_STATUS` 와 책별 `MigrationStatus` 뿐입니다.

기기 구분이 필요하면 추출 파이프라인이 "어느 기기에서 뽑았는지"를 **반입 시점에
스스로 기록**하는 수밖에 없습니다. 현재는 `kindle_sync*.json` 최상위 `kindle_path`
가 유일한 흔적인데 클리핑 레코드로 내려오지 않습니다 (`kindle/exporters.py` 의
`source_file` strip 문제와 같은 뿌리).

### 17.3 `book_id` 는 파싱하지 말 것 — payload 에 이미 분리돼 있습니다

`serialized_payload` 안에 구조적으로 들어 있습니다.

```json
"book_data": {
  "asin": "H7OCTJGYSM3Y3YAVEY8O8LX5QGWH19IB",
  "contentType": "PDOC",
  "guid": "CR!IXPC73LEY0KD66726K67OR8XM7TM",
  "isOwnedByCustomer": 0, "isSample": 0
}
```

`book_id` 문자열을 `-` 로 자르는 방식은 **실제로 깨집니다.** 하이픈 개수가
일정하지 않습니다:

```
하이픈 3개 : 144권   01XHTU4O…-PDOC-CR!1VNSPVQ3…-0
하이픈 7개 :   3권   01589ac8-a11f-4b48-9d19-b3f4ccc9a5c1-EBOK-Vera:83623E66-0
```

id 자체가 UUID 인 경우가 있어 첫 `-` 분할은 3건을 망가뜨립니다. **payload JSON 에서
꺼내십시오.**

### 17.4 ⚠️ `asin` 필드의 값은 ASIN 이 아닙니다

필드 이름이 `asin` 이라 오해하기 쉽지만 내용이 다릅니다.

```
PDOC (사이드로드) : 140권 / 어노테이션 3,304건  (98.5%)
EBOK (아마존 구매):   7권 / 어노테이션    49건  ( 1.5%)
```

PDOC 의 `asin` 값은 아마존 ASIN 이 아니라 **기기가 생성한 32자 내부 ID** 입니다.
EBOK 7권 중에서도 3건은 `Vera`(탈옥 도구)·`Font_Calibration` 같은 시스템 항목이라,
**진짜 ASIN 을 가진 실제 책은 4권뿐**입니다:

```
B000FCK3C8, B002ISDCKW, B004GHNIRK, B005CWUF3S
```

→ 통합 설계 P0 에서 "`asin:` 갈래가 죽어 있다"고 확정한 것이 **KSDK 에서도
그대로입니다.** 사용자 장서가 거의 전부 사이드로드라 ASIN 이 애초에 없습니다.
book_key 는 **저자 토큰 정렬** 같은 내용 기반 수단에 의존해야 합니다.

PDOC 내부 ID 가 기기 간에도 같은지는 **검증 불가** — Scribe DB 가 아직 없어
대조 대상이 없습니다.

### 17.5 소실된 `kindle_sync.json` 대조 — KSDK 가 덮습니다

`kindle_sync.json`(Colorsoft, 2026-06-28 추출, 29권 1,951건, 783KB)이 09-19 14:11
사고 실행에 **덮어써져 소실**됐습니다 (지금은 157건 32KB). `.gitignore` 에
`*.json` 이 있어 git 에도 없고 백업 디렉터리에도 사본이 없습니다. 리딩총괄이
`~/prj`·Dropbox·Documents·Time Machine 로컬 스냅샷까지 확인했고 **복구 불가**입니다.

**KSDK 회수본이 이를 덮는지 구조적으로 대조**했습니다. 소실분 추출 시각
(2026-06-28 13:32:29)을 경계로 자른 결과입니다.

| | 소실된 `kindle_sync.json` | KSDK 회수본 (같은 경계) |
|---|---|---|
| **책 수** | **29권** | **29권** ✅ |
| 클리핑 수 | 1,951건 | 1,955건 |

내역: HIGHLIGHT 1,455 / BOOKMARK 474 / NOTE 26.

**책 수가 정확히 일치**하고 건수 차이는 0.2% 로, `POPULARHIGHLIGHT` 계수 여부 등
파서 차이로 설명 가능한 범위입니다. 같은 기기의 전체 어노테이션 저장소이므로
논리적으로도 포함 관계가 성립합니다.

⚠️ **다만 본문 단위 완전성은 증명되지 않았습니다.** KSDK DB 에는 좌표
(`shortPosition`)와 색상만 있고 **하이라이트 본문이 없습니다.** 본문 기준 대조는
KFX 파일이 있어야 가능하고, KFX 는 기기에 있습니다. 기기 재연결 시 마저 할 일입니다.

### 17.6 남은 일

- 기기 재연결 후 **본문 기준 소실분 대조**
- Scribe DB 확보 후 **PDOC 내부 ID 의 기기 간 동일성 검증**
- 위 둘 다 지금은 재료가 없습니다
