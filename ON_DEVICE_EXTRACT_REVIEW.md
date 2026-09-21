# 온디바이스 부분 추출 검토 — DB 통째 반출 대신 필요한 것만

작성: 2026-09-20 · 세션 `킨들클리핑new` · 지시: 리딩총괄2 (방향 전환 — 하드
링크/bind mount 조사는 중단, 파일로 남기지 않음) · **기기 미연결·코드 변경 없음**

---

## 결론 먼저

**`sqlite3`(또는 대체 수단) 부재가 확정된 적은 없다 — "네트워크 도구만 확인했다"가 정확한 현재 상태다.** 다만 없을 가능성이 높고, **없어도 막다른 길이 아니다** — 정적 링크 ARM 바이너리는 kindletool 을 죽인 것과 **다른 실패 모드**라 시도해 볼 가치가 있다. 산출물 크기는 실측했다: **전체 2,977건을 좌표+색상/노트까지 담아도 355KB(gzip 46.8KB)** — DB 원본 3.68MB 의 6.4%(gzip 대비 1.3%). 하루치 증분은 **약 6KB** 로 추정된다.

---

## ① `sqlite3` 바이너리 — 미확인 (관찰과 추론을 가른다)

**관찰(§16.6, `01_tools.txt` 실측)**: `which` 로 확인한 목록에 `ssh`·`dropbear`·
`dropbearmulti`·`python`·`python3`·`perl` 이 없다. **`sqlite3` 는 애초에
질의 대상이 아니었다** — "없다"고 확정된 적이 없다.

**추론(미검증)**: 최소 도구셋(인터프리터 전무)인 정황상 `sqlite3` CLI 도
없을 가능성이 높다. Amazon 리더 앱 자체는 SQLite 를 쓰므로 `libsqlite3.so`
가 시스템 어딘가에 있을 개연성은 있지만, **CLI 없이 그 라이브러리를 호출할
수단(interpreter·dlopen 가능한 셸 내장 기능)이 이 기기엔 없다** — 이것도
셸만으로는 불가능하다는 뜻이지, 라이브러리 유무 자체를 확인한 것은 아니다.

### 🔴 정정 — `tools/mtp_put.c` 는 이 저장소에 없다

말씀하신 "`tools/mtp_put.c` 로 이미 파일을 올릴 수 있다"는 파일이 실제로
없다(`find` 로 확인). 다만 **같은 기능을 하는 실물이 이미 있고 실전
검증됐다** — `kindle/device.py::mtp_direct_session()` 의
`LIBMTP_Delete_Object` + `LIBMTP_Send_File_From_File` 조합으로
`mrinstaller.sh` 를 여러 번 교체·재배포하고 sha1 로 검증했다
(`KINDLE_ANNOTATION_OUTAGE.md` §16.8). **전달 경로 자체는 있다** — 파일
이름만 다르다.

### 정적 링크가 kindletool 과 왜 다른 실패인가

kindletool 이 죽은 원인은 **동적 링크 시 `libz.so.1` 버전 비호환**이었다
(§16.6, "noexec 아님·라이브러리 비호환 확정"). 정적 링크 바이너리는 실행
시점에 그런 공유 라이브러리를 전혀 참조하지 않으므로 **이 특정 실패 모드
자체가 원천적으로 없다.** 남는 위험은 커널 syscall ABI·ELF 아키텍처
호환뿐이고, 이건 리눅스 ARM 에서 거의 항상 하위 호환된다.

**관찰**: `uname -a` (00_basic.txt) → `Linux kindle 5.15.41-lab126 ... armv7l
GNU/Linux`. armv7(32비트) 커널인 것은 확인됐다.

**추론(미검증)**: 문자열 끝의 "GNU/Linux" 는 관례상 glibc 계열임을 시사하지만
커널 빌드 시 고정된 문자열이라 **userspace libc 를 확정하지 않는다.**
hard-float 인지(VFP 유닛 탑재 여부)도 `/proc/cpuinfo` 의 `Features` 줄을
봐야 확정된다 — 미확인. 다만 **정적 링크면 이 확인 자체가 덜 중요하다** —
바이너리 안에 필요한 게 다 있으니 커널 syscall ABI 와 EABI 콜링 컨벤션
(soft/hard float)만 맞으면 된다.

**빌드 경로 (관찰 — 지금 이 맥에 있는 것)**: 전용 크로스컴파일러
(`arm-linux-musleabihf-gcc` 등)는 이 맥에 없다(`which` 확인). 다만
**`docker` 는 있다** — `arm32v7/alpine`(musl 기반) 같은 이미지를 받아 그
안에서 정적 바이너리를 빌드하면 새 툴체인을 맥에 영구 설치하지 않고도 가능
하다. musl 기반 정적 링크는 이런 임베디드 타깃에 흔히 쓰이는 표준적인
방법이다.

**계획(미실행)**: SQLite 전체 CLI 를 옮길 필요 없이, **딱 필요한 질의
하나만 하는 전용 바이너리**(가칭 `ksdk_extract`)를 SQLite 앰알거메이션
(단일 소스 파일, 퍼블릭 도메인)을 정적으로 링크해 만든다. 커스텀 CLI 개발
보다 훨씬 작고 검증하기 쉽다. **기기 없이 로컬 사본(`~/kindle_annotation_
backup/live/ksdk_annotation_v1.db`)으로 전수 테스트한 뒤에만** 기기에
올린다.

---

## ② 증분 추출 — 컬럼은 이미 있다 (관찰, 우리 코드로 확인됨)

`kindle/ksdk.py` 가 이미 질의하는 스키마에 시각 컬럼이 있다:

```sql
SELECT annotation_id, book_id, dataset, start_position, end_position,
       created_time, modified_time, serialized_payload
  FROM nonsyncable_annotations
```

`created_time`(epoch-ms)로 `WHERE created_time > ?` 필터가 그대로 된다.
전체 3,353행 규모에서는 인덱스 없이도 순식간이다(실측: 로컬에서 전체 파싱이
1초 미만).

**마지막 추출 시점을 기기에 남길 곳**: 이미 쓰고 있는
`/mnt/us/documents/ksdk_export/` 에 `last_extract_cursor.txt` 같은 작은
파일 하나면 된다 — 그 디렉터리 자체는 이미 존재하고 매 실행 다시 쓰이므로
새 인프라가 필요 없다.

---

## ③ 산출물 크기 — 실측 (로컬 사본으로 직접 계산, 추정 아님)

```
전체 클리핑                 2,977건
좌표+타입만 (TSV, 색상 생략)  228,721 B   gzip  38,899 B
+ 색상/노트 포함 (실사용)     355,049 B   gzip  46,779 B   (2,977행, 평균 119.3 B/행)
JSON(축약 키, 색상 미포함)    286,119 B   gzip  41,427 B

참고: DB 원본                3,682,304 B
```

**전체를 다 담아도 DB 원본의 6.4%(gzip 대비 1.3%)** 다. **증분(하루 ~50건
가정)은 약 6KB** — DB 통째 반출과는 성격이 다른 크기다.

**포맷 제안**: **TSV.** 기기 쪽에서 `printf`/`awk` 로 한 줄씩 찍기 가장
쉽고, 맥 쪽 파서(`csv` 모듈)도 표준 라이브러리로 바로 읽는다. JSON 은 더
장황하고(위 실측: 25% 더 큼) 기기 쪽에서 이스케이프 처리가 필요해 셸
스크립트로 만들기 더 번거롭다.

---

## ④ `;log` 스크립트에 얹는 방법 — 구조안 (미구현)

```
현재  tools/ksdk_push.sh:
        DB(+wal/shm) 를 WiFi push 하거나 documents/ 로 복사

제안  같은 트리거(;log mrpi) 안에서:
        1) ksdk_extract 바이너리로 DB 를 읽어(읽기 전용 오픈)
           마지막 커서 이후 행만 TSV 로 뽑는다
        2) TSV 를 documents/ksdk_export/ 에 쓰고 커서 파일을 갱신한다
        3) 그 TSV 를 WiFi push 하거나(기존 로직 그대로 재사용) documents/ 에
           남긴다
```

**"DB 덤프"라는 사용자 우려의 핵심을 정면으로 해소한다** — `documents/` 에
남는 것이 수 MB 짜리 전체 DB 사본이 아니라 수 KB 짜리 **증분 조각**이 된다.
이 조각이 실수로 남아 있어도 위험이 다르다 — `kindle/keys.py` 의 좌표+내용
기반 dedup 이 겹치는 행을 자연히 흡수하므로, 옛 조각을 실수로 다시 반입해도
"조용히 옛 데이터로 덮어쓰는" 사고(2026-09-19 유형)가 구조적으로 덜 난다
— 전체 스냅샷을 교체하는 게 아니라 조각을 더할 뿐이기 때문이다. (그래도
`kindle/ksdk_staleness.py` 의 시각 역행 경고는 그대로 유효하게 쓸 수 있다.)

---

## ⑤ 위험 평가 🔴

**새로 생기는 위험**: 기기에서 **새 바이너리를 root 로 실행**하는 것 자체가
지금까지의 "이미 검증된 셸 스크립트 교체"보다 새로운 종류의 위험이다 — C
바이너리의 버그(메모리 오류 등)는 셸 스크립트보다 예측하기 어렵다.

**완화**:
- **DB 를 항상 읽기 전용으로 연다** (`SQLITE_OPEN_READONLY`) — 지금
  `kindle/ksdk.py` 가 이미 하는 것과 같은 방식(`mode=ro` URI). 읽기 전용
  핸들은 원본을 손상시킬 경로가 없다
- **기기에 올리기 전 로컬 사본으로 전수 테스트** — 이미 여러 벌의 실제 DB
  사본이 있다(`live/`, `ksdk-recovered-20260919-0331/`). 기기 없이 정확성·
  안정성을 검증할 수 있다
- **배포·회수가 이미 검증된 가역 절차다** — MTP 로 올린 파일은 MTP 로
  삭제하면 그만이고(§16.8 에서 실제로 스크립트를 교체·검증했다), 이 바이너리는
  `mrinstaller.sh` 자리를 차지하지 않는 **별도 파일**이라 그 자리 원상복구
  (`device-original/mrinstaller.sh.orig`)와는 무관하다 — 지우면 끝이다

**권고**: Scribe 회수가 아직인 지금, Colorsoft 에 새 실행 경로를 추가하는
건 시급하지 않다. **오프라인 빌드·테스트까지는 지금 진행하고, 기기에 실제로
올리는 건 Scribe 회수 완료 후로 미루는 편**을 권한다(그래도 그 판단은 총괄
몫이다).

---

## 기기 연결 시 확인 목록

1. `which sqlite3` / `ls /usr/bin | grep -i sqlite` — CLI 존재 여부 확정
2. `cat /proc/cpuinfo | grep Features` — VFP/NEON(hard-float) 여부 확정
3. (오프라인 빌드가 끝난 뒤) 만든 정적 바이너리를 MTP 로 올려 `;log` 로
   실행 — 종료 코드·출력 TSV 를 로컬 DB 파싱 결과와 대조
4. 문제 없으면 커서 파일 갱신·증분 결과가 실제로 줄어드는지 두 번째 실행으로 확인
