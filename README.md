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

### 출력 특성
- **타임스탬프 순 정렬**: 모든 클리핑이 하이라이트를 만든 시각 오름차순으로 저장됩니다.
- **로컬 시간 표시**: 타임스탬프는 시스템 타임존 기준 로컬 시각으로 출력됩니다 (`2026-02-08 13:46:58` 형식).

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

## 예제

`examples/` 폴더에 실제 KFX 책(`공산당선언`)의 `.kfx` 원본, `.sdr` 사이드카, 파싱 결과 마크다운이 포함되어 있습니다.

```bash
python parse_clippings.py \
    "examples/gongsandangseoneon - kareul mareukeuseu _ peurideurihi enggelseu.sdr/" \
    -o out.md -f markdown
```

`examples/sample_output.md`에서 실제 출력 결과를 확인할 수 있습니다.

---

## 개발 노트

역공학 과정에서 시도한 방법들과 발견한 것들은 [DEVLOG.md](DEVLOG.md)에 정리되어 있습니다.
