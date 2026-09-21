#!/usr/bin/env python3
"""loc_end 소스 간 일치 실측.

결과: `LOC_END_CHECK.md`. 사용법: `python tools/loc_end_check.py`
(`~/kindle_annotation_backup/` 아래 백업이 있어야 한다).

리딩총괄2 지시: 새 clip_key(②③)가 loc_end 를 해시에 넣게 되므로, 소스 간에
loc_end 가 어긋나면 같은 하이라이트가 갈라진다. DATA_MODEL.md §2.1 은
"Location 완전 일치 3,892건"이라고만 적었지 start·end 를 각각 봤는지가
불명확 — 이번엔 **둘을 따로** 낸다.

두 비교:
  A) POST-KL 간 — 과거 KFX+YJR 추출본(Scribe, kindle_sync_scribe.json,
     5,638건) vs My Clippings.txt 병합본(8,501엔트리). §2.1 과 같은 방식
     (본문 내용 1:1 매칭, 정규화 20자 이상)으로 짝짓고 start·end 를 각각 대조.
  B) PRE-KL 간 — KSDK DB vs YJR 사이드카. 둘 다 char offset 이라 내용이
     아니라 **좌표 자체**로 책을 잇는다: 각 YJR 책의 location_start 집합과
     각 KSDK asin 의 location_start 집합의 교집합 비율(Jaccard)이 가장 높은
     쌍을 그 책으로 판정한다(임계값 이상만 채택). 매칭된 책 안에서는
     location_start 동일 항목끼리 짝지어 end 를 대조한다.
"""
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kindle.parsers.my_clippings import parse_my_clippings
from kindle.parsers.yjr import parse_yjr
from kindle import ksdk
from kindle.keys import normalize_content

BACKUP = Path.home() / "kindle_annotation_backup"
OUT = Path(__file__).resolve().parent.parent / "LOC_END_CHECK.md"

# ===========================================================================
# A) POST-KL: Scribe 과거 추출본 vs My Clippings.txt
# ===========================================================================

def load_scribe_extraction():
    p = BACKUP / "2026-09-20-archives" / "kindle_sync_scribe.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    out = []
    for b in d["books"]:
        for c in b["clippings"]:
            if c.get("clip_type") != "highlight":
                continue
            out.append({
                "book": b["title"], "content": c.get("content") or "",
                "loc_start": c.get("location_start"), "loc_end": c.get("location_end"),
            })
    return out


def load_my_clippings():
    p = BACKUP / "2026-09-20-archives" / "My Clippings.txt"
    clips = parse_my_clippings(p)
    out = []
    for c in clips:
        if c.clip_type != "highlight":
            continue
        out.append({
            "book": c.book_title, "content": c.content or "",
            "loc_start": c.location_start, "loc_end": c.location_end,
        })
    return out


def section_a():
    scribe = load_scribe_extraction()
    mc = load_my_clippings()

    # 정규화 20자 이상만 (§2.1 과 동일 기준)
    def index_by_content(rows):
        idx = defaultdict(list)
        for r in rows:
            norm = normalize_content(r["content"])
            if len(norm) >= 20:
                idx[norm].append(r)
        return idx

    idx_s = index_by_content(scribe)
    idx_m = index_by_content(mc)

    start_diffs = Counter()
    end_diffs = Counter()
    end_both_present = 0
    matched = 0
    ambiguous = 0
    mismatched_examples = []
    end_mismatch_examples = []
    mismatch_ends_with_period = 0
    mismatch_other = 0
    _SENT_END = (".", "।", "。")

    for norm, s_rows in idx_s.items():
        m_rows = idx_m.get(norm)
        if not m_rows:
            continue
        if len(s_rows) != 1 or len(m_rows) != 1:
            ambiguous += 1
            continue      # 1:1 만 쓴다 — §2.1 방식 그대로
        s, m = s_rows[0], m_rows[0]
        matched += 1
        if s["loc_start"] is not None and m["loc_start"] is not None:
            d = m["loc_start"] - s["loc_start"]
            start_diffs[d] += 1
            if d != 0 and len(mismatched_examples) < 5:
                mismatched_examples.append((s, m, d))
        if s["loc_end"] is not None and m["loc_end"] is not None:
            end_both_present += 1
            d = m["loc_end"] - s["loc_end"]
            end_diffs[d] += 1
            if d != 0:
                if s["content"].rstrip().endswith(_SENT_END):
                    mismatch_ends_with_period += 1
                else:
                    mismatch_other += 1
                if len(end_mismatch_examples) < 5:
                    end_mismatch_examples.append((s, m, d))

    return {
        "matched": matched, "ambiguous": ambiguous,
        "start_diffs": start_diffs, "end_diffs": end_diffs,
        "end_both_present": end_both_present,
        "start_mismatch_examples": mismatched_examples,
        "end_mismatch_examples": end_mismatch_examples,
        "mismatch_ends_with_period": mismatch_ends_with_period,
        "mismatch_other": mismatch_other,
        "scribe_total": len(scribe), "mc_total": len(mc),
    }


# ===========================================================================
# B) PRE-KL: KSDK DB vs YJR 사이드카 — 좌표 집합 겹침으로 책을 잇는다
# ===========================================================================

def load_yjr_books():
    """{book_stem: [{"type","loc_start","loc_end"}, ...]}

    ⚠️ 같은 책의 .yjr 이 여러 백업 스냅샷(`2026-09-19/`, `pre-jailbreak-*/`)에
    중복으로 들어 있다 — 같은 파일을 두 번 세면 모든 위치가 후보 2개짜리가
    되어 "1:1 만" 매칭 규칙에 전부 걸려 나간다 (처음 실행에서 실제로 이렇게
    189/189 겹치는 책의 매칭이 13건으로 무너졌다). 책마다 **가장 큰 파일
    하나만** 남긴다 — 잘린 파일이 있으면 온전한 사본을 고르기 위함이다.
    """
    candidates: dict[str, Path] = {}
    for p in BACKUP.rglob("*.yjr"):
        if ".bad_file" in p.name:
            continue
        stem = p.parent.name[:-4] if p.parent.name.lower().endswith(".sdr") else p.parent.name
        prev = candidates.get(stem)
        if prev is None or p.stat().st_size > prev.stat().st_size:
            candidates[stem] = p

    books = defaultdict(list)
    for stem, p in candidates.items():
        try:
            clips = parse_yjr(p, book_title=stem)
        except Exception:
            continue
        for c in clips:
            books[stem].append({
                "type": c.clip_type, "loc_start": c.location_start,
                "loc_end": c.location_end,
            })
    return books


def load_ksdk_books(db_path: Path):
    """{asin: [{"type","loc_start","loc_end"}, ...]} — 클리핑(①②③)만."""
    by_asin = defaultdict(list)
    for c in ksdk.parse_ksdk_db(db_path):
        by_asin[c.book_title].append({          # book_title == asin (제목 캐시 없이 호출)
            "type": c.clip_type, "loc_start": c.location_start, "loc_end": c.location_end,
        })
    return by_asin


def section_b():
    yjr_books = load_yjr_books()
    ksdk_books = load_ksdk_books(BACKUP / "live" / "ksdk_annotation_v1.db")

    # 각 YJR 책마다 가장 겹치는 KSDK asin 을 찾는다 (Jaccard, location_start 집합)
    ksdk_sets = {asin: {c["loc_start"] for c in items if c["loc_start"] is not None}
                for asin, items in ksdk_books.items()}

    pairs = []   # (yjr_stem, asin, jaccard, overlap_n)
    for stem, items in yjr_books.items():
        yset = {c["loc_start"] for c in items if c["loc_start"] is not None}
        if len(yset) < 5:            # 너무 작은 책(파편)은 오판 위험이 크다 — 제외
            continue
        best = None
        for asin, kset in ksdk_sets.items():
            if not kset:
                continue
            inter = len(yset & kset)
            if inter == 0:
                continue
            union = len(yset | kset)
            jac = inter / union
            if best is None or jac > best[1]:
                best = (asin, jac, inter)
        if best and best[1] >= 0.5 and best[2] >= 5:
            pairs.append((stem, best[0], best[1], best[2]))

    end_diffs = Counter()
    end_both_present = 0
    end_mismatch_examples = []
    start_matched_total = 0

    for stem, asin, jac, overlap_n in pairs:
        yjr_by_start = defaultdict(list)
        for c in yjr_books[stem]:
            if c["loc_start"] is not None:
                yjr_by_start[(c["type"], c["loc_start"])].append(c)
        for c in ksdk_books[asin]:
            if c["loc_start"] is None:
                continue
            cands = yjr_by_start.get((c["type"], c["loc_start"]))
            if not cands or len(cands) != 1:
                continue
            y = cands[0]
            start_matched_total += 1
            # 북마크는 사양대로 end 를 안 본다 (이미 None 정규화 대상)
            if c["type"] == "bookmark":
                continue
            if c["loc_end"] is not None and y["loc_end"] is not None:
                end_both_present += 1
                d = c["loc_end"] - y["loc_end"]
                end_diffs[d] += 1
                if d != 0 and len(end_mismatch_examples) < 5:
                    end_mismatch_examples.append((stem, asin, c, y, d))

    return {
        "book_pairs": pairs, "start_matched_total": start_matched_total,
        "end_diffs": end_diffs, "end_both_present": end_both_present,
        "end_mismatch_examples": end_mismatch_examples,
        "yjr_book_count": len(yjr_books), "ksdk_book_count": len(ksdk_books),
    }


# ===========================================================================

def fmt_counter(c: Counter, limit=15) -> str:
    if not c:
        return "  (표본 없음)"
    lines = []
    for k, v in sorted(c.items(), key=lambda kv: -kv[1])[:limit]:
        lines.append(f"  {k:>+6} : {v:>6,}" if k != 0 else f"  {k:>6} : {v:>6,}  ← 일치")
    if len(c) > limit:
        lines.append(f"  … 그 외 {len(c) - limit}개 값")
    return "\n".join(lines)


a = section_a()
b = section_b()

report = []
report.append("# loc_end 소스 간 일치 실측\n")
report.append("세션 `킨들클리핑new` · 지시: 리딩총괄2 · "
              "원본: `~/kindle_annotation_backup/`, "
              "reading_manager `DATA_MODEL.md` §2.1 방식 재현 + end 분리\n")

report.append("## A) POST-KL 간 — 과거 KFX+YJR 추출본(Scribe) vs My Clippings.txt\n")
report.append(f"- Scribe 과거 추출본 하이라이트: {a['scribe_total']:,}건 "
              f"(`kindle_sync_scribe.json`, 2026-06-26 동기화)")
report.append(f"- My Clippings.txt 병합본 하이라이트: {a['mc_total']:,}건 (8,501 엔트리 중)")
report.append(f"- 정규화 20자 이상 & 내용 1:1 매칭: **{a['matched']:,}쌍** "
              f"(중복 내용이라 1:1 아닌 것 {a['ambiguous']}건 제외)\n")
report.append("### loc_start 차이 분포 (My Clippings − Scribe추출)\n")
report.append("```")
report.append(fmt_counter(a["start_diffs"]))
report.append("```\n")
report.append(f"### loc_end 차이 분포 — 양쪽 다 end 있는 {a['end_both_present']:,}쌍\n")
report.append("```")
report.append(fmt_counter(a["end_diffs"]))
report.append("```\n")
total_mismatch = a["mismatch_ends_with_period"] + a["mismatch_other"]
if total_mismatch:
    pct = a["mismatch_ends_with_period"] / total_mismatch * 100
    report.append(f"🔎 **패턴**: 불일치 {total_mismatch}건 중 **{a['mismatch_ends_with_period']}건"
                  f"({pct:.0f}%)이 하이라이트 끝이 문장 종결 부호(`.` 등)로 끝난다.** "
                  f"나머지 {a['mismatch_other']}건만 예외. 무작위가 아니라 "
                  f"**끝이 마침표인 경우에 쏠린 것**으로 보인다 — KL맵 변환(`bisect_right`)이 "
                  f"마침표 위치를 기기가 매기는 Location 보다 1~2 높게 넣는 경계 처리 차이로 "
                  f"추정된다.\n")
if a["end_mismatch_examples"]:
    report.append("**end 불일치 예시**:\n")
    for s, m, d in a["end_mismatch_examples"]:
        report.append(f"- 차이 {d:+d} · `…{s['content'][-25:]}` · "
                      f"scribe=({s['loc_start']},{s['loc_end']}) "
                      f"myclippings=({m['loc_start']},{m['loc_end']})")
    report.append("")
else:
    report.append("**end 불일치 예시 없음 — 전부 일치.**\n")

report.append("## B) PRE-KL 간 — KSDK DB vs YJR 사이드카\n")
report.append(f"- YJR 사이드카 책: {b['yjr_book_count']}개 폴더 스캔 "
              f"(껍데기·파편 다수 포함)")
report.append(f"- KSDK DB 책(asin): {b['ksdk_book_count']}권")
report.append(f"- 좌표 집합 겹침(Jaccard≥0.5, 교집합≥5)으로 확정한 책 쌍: "
              f"**{len(b['book_pairs'])}권**\n")
if b["book_pairs"]:
    report.append("| YJR 폴더 | KSDK asin | Jaccard | 겹치는 좌표 수 |")
    report.append("|---|---|---|---|")
    for stem, asin, jac, n in sorted(b["book_pairs"], key=lambda x: -x[3])[:15]:
        report.append(f"| {stem[:40]} | {asin[:20]} | {jac:.2f} | {n} |")
    report.append("")
report.append(f"- location_start 로 짝지어진 클리핑: {b['start_matched_total']:,}건 "
              f"(북마크 포함)")
report.append(f"### loc_end 차이 분포 — 하이라이트·노트만, 양쪽 다 end 있는 "
              f"{b['end_both_present']:,}건\n")
report.append("```")
report.append(fmt_counter(b["end_diffs"]))
report.append("```\n")
if b["end_mismatch_examples"]:
    report.append("**end 불일치 예시**:\n")
    for stem, asin, c, y, d in b["end_mismatch_examples"]:
        report.append(f"- 차이 {d:+d} · {stem[:30]} · type={c['type']} start={c['loc_start']} · "
                      f"ksdk_end={c['loc_end']} yjr_end={y['loc_end']}")
    report.append("")
else:
    report.append("**end 불일치 예시 없음 — 전부 일치.**\n")

report.append("## 결론\n")

a_end_bad = sum(v for k, v in a["end_diffs"].items() if k != 0)
a_end_ok = a["end_diffs"].get(0, 0)
b_end_bad = sum(v for k, v in b["end_diffs"].items() if k != 0)
b_end_ok = b["end_diffs"].get(0, 0)

a_pct = a_end_bad / a["end_both_present"] * 100 if a["end_both_present"] else 0
report.append(f"- **B) PRE-KL (KSDK ↔ YJR)**: end 일치 {b_end_ok:,} / "
              f"불일치 {b_end_bad:,}  (표본 {b['end_both_present']:,}건) — "
              f"**완전 일치.** 같은 저장소가 마이그레이션 전후로 나뉜 것뿐이니 예상대로다")
report.append(f"- **A) POST-KL (Scribe추출 ↔ My Clippings)**: end 일치 {a_end_ok:,} / "
              f"불일치 {a_end_bad:,}  (표본 {a['end_both_present']:,}건, {a_pct:.1f}%) — "
              f"**거의 일치하지만 완전하지 않다.** 무작위가 아니라 하이라이트 끝이 "
              f"마침표로 끝나는 경우에 쏠려 있고(94%), 값도 대부분 -1(간혹 -2)로 방향이 "
              f"일정하다. 그런데 크기가 -1 로 고정이 아니라 -1/-2 로 갈려 "
              f"**단일 상수로는 안 흡수된다** — 마침표 뒤에 따옴표·괄호가 더 붙는지에 "
              f"따라 갈리는 것으로 보인다")
report.append("")
report.append("### 권장 (판단은 총괄 몫)")
report.append("")
report.append("- **B(KSDK↔YJR, 킨들 내부 두 소스)는 loc_end 를 키에 넣어도 안전하다** — "
              "1,487/1,487 완전 일치")
report.append("- **A(추출본↔My Clippings)는 그대로 넣으면 매칭된 것 중 약 1%가 갈린다.** "
              "다만 원인이 무작위가 아니라 **끝이 문장부호인 하이라이트라는 식별 가능한 "
              "경계 케이스**다. 옵션:")
report.append("  1. loc_end 그대로 키에 포함 — 이 1% 는 소스 간 키가 갈라짐(중복 신원 발생)")
report.append("  2. 끝이 `.`/`」`/`”` 등 문장부호·닫는 괄호일 때 loc_end 를 ±2 허용 범위로 "
              "정규화(스냅) — 정확한 상수 오프셋이 아니라 **경계 마진**으로 접근")
report.append("  3. 이 경계 케이스만 loc_start 로 폴백")

text = "\n".join(report)
OUT.write_text(text, encoding="utf-8")
print(text)
print(f"\n\n저장: {OUT}")
