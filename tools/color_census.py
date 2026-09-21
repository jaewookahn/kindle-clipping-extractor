#!/usr/bin/env python3
"""실제 색상 값 전수 조사 — KSDK DB + 로컬 아카이브(YJR·JSON 산출물·My Clippings).

결과: `COLOR_CENSUS.md` (설명) / `COLOR_CENSUS.json` (원본 데이터).

리딩총괄2 지시: "표본을 센 쪽이 항상 옳았다." 4색 화이트리스트가 틀렸던
근본 원인은 파서가 고정 어휘를 쓰지 않고 mchl_color 값을 그대로 통과시키기
때문 — 실제로 몇 종류가 도는지 세어야 다음 판단(패턴 정규식이 맞는지, 알려진
색이 몇 개인지)의 근거가 된다. `kindle/keys.py` 가 결국 채택한 답은 화이트
리스트를 늘리는 것이 아니라 패턴(`^\[[A-Za-z][A-Za-z_]*\]\s?`)으로 바꾸는
것이었다.

사용법:
    python tools/color_census.py

`~/kindle_annotation_backup/` 아래 백업이 있어야 한다 (KSDK DB, YJR 사이드카,
JSON 산출물). 없는 소스는 건너뛴다.
"""
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BACKUP = Path.home() / "kindle_annotation_backup"
counts: dict[str, Counter] = {}


def bump(source: str, value: str):
    counts.setdefault(source, Counter())[value] += 1


# ── 1) KSDK DB: nonsyncable_annotations.serialized_payload 의 mchl_color ──
def census_ksdk_db(path: Path, label: str):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    for (pl,) in con.execute("SELECT serialized_payload FROM nonsyncable_annotations"):
        try:
            d = json.loads(pl)
            meta = json.loads(d.get("json_metadata") or "{}")
        except Exception:
            continue
        c = meta.get("mchl_color")
        if c:
            bump(f"ksdk_db:{label}:nonsyncable_annotations", c)
    # server_view 도 같은 스키마를 쓸 수 있으니 함께 본다
    try:
        for (pl,) in con.execute("SELECT serialized_payload FROM server_view"):
            try:
                d = json.loads(pl)
                meta = json.loads(d.get("json_metadata") or "{}")
            except Exception:
                continue
            c = meta.get("mchl_color")
            if c:
                bump(f"ksdk_db:{label}:server_view", c)
    except sqlite3.OperationalError:
        pass
    con.close()


for p, label in [
    (BACKUP / "live" / "ksdk_annotation_v1.db", "live"),
    (BACKUP / "ksdk-recovered-20260919-0331" / ".annotations"
     / "amzn1.account.AG4IK4ZSHJ4AVFVDXU4ZYLIGSAKA" / "ksdk_annotation_v1.db", "recovered"),
]:
    if p.exists():
        census_ksdk_db(p, label)


# ── 2) YJR 사이드카: parse_yjr() 이 content 앞에 "[색상] " 을 붙인다 ──
from kindle.parsers.yjr import parse_yjr

_TAG = re.compile(r"^\[([^\]]+)\]\s?")
yjr_files = [p for p in BACKUP.rglob("*.yjr") if ".bad_file" not in p.name]
for p in yjr_files:
    try:
        clips = parse_yjr(p, book_title=p.parent.name)
    except Exception:
        continue
    for c in clips:
        m = _TAG.match(c.content or "")
        if m:
            bump("yjr_sidecars", m.group(1))

print(f"YJR 파일 {len(yjr_files)}개 스캔")


# ── 3) sync_kfx / sync JSON 산출물의 content 필드 ──
def census_json_output(path: Path, label: str):
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  [skip] {label}: {e}")
        return
    books = d.get("books", [])
    n = 0
    for b in books:
        for c in b.get("clippings", []):
            content = c.get("content", "")
            m = _TAG.match(content or "")
            if m:
                bump(f"json_output:{label}", m.group(1))
                n += 1
    print(f"  {label}: {len(books)}권, 색상 태그 {n}건")


for p, label in [
    (BACKUP / "2026-09-20-archives" / "kindle_sync.json", "kindle_sync.json(Colorsoft,소실전백업)"),
    (BACKUP / "2026-09-20-archives" / "kindle_sync_scribe.json", "kindle_sync_scribe.json(Scribe)"),
]:
    if p.exists():
        census_json_output(p, label)

# 현재 저장소 산출물도 (있으면)
cur = Path("/Users/codex/prj/kindle-clipping-extractor/kindle_sync.json")
if cur.exists():
    census_json_output(cur, "kindle_sync.json(현재 저장소)")


# ── 4) My Clippings.txt — 보통 색상 정보가 없다. 확인만 한다 ──
mc = BACKUP / "2026-09-20-archives" / "My Clippings.txt"
if mc.exists():
    raw = mc.read_bytes().decode("utf-8-sig", errors="replace")
    has_bracket = "[" in raw and re.search(r"\[[A-Za-z_]+\]", raw)
    print(f"\nMy Clippings.txt: 대괄호 색상 표기 {'있음(!)' if has_bracket else '없음(예상대로)'}")


# ── 요약 ──
print("\n" + "=" * 70)
print("색상 값 전수 (소스별)")
print("=" * 70)
all_colors: Counter = Counter()
for source, c in counts.items():
    print(f"\n[{source}]  총 {sum(c.values())}건, distinct {len(c)}")
    for color, n in c.most_common():
        print(f"    {color:<20} {n:>6,}")
        all_colors[color] += n

print("\n" + "=" * 70)
print(f"전체 distinct 색상: {len(all_colors)}")
print("=" * 70)
for color, n in all_colors.most_common():
    print(f"  {color:<20} {n:>6,}")

out = {
    "generated_by": "킨들클리핑new 세션",
    "purpose": "실제 색상 값 전수 조사 — clip_key 색상태그 제거 패턴 검증용",
    "by_source": {s: dict(c.most_common()) for s, c in counts.items()},
    "all_colors": dict(all_colors.most_common()),
}
outpath = Path(__file__).resolve().parent.parent / "COLOR_CENSUS.json"
outpath.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n저장: {outpath}")
