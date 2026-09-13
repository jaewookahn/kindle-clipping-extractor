#!/usr/bin/env python3
"""라이브러리를 훑어 '목차가 성긴' KFX 를 찾는다.

성기다 = 목차 항목 사이 본문 간격이 큰데, 목차에 안 올라온 소제목이 본문에 여럿 있다.

**목차에 이미 있는지는 제목 문자열로 대조한다.** eid 나 위치로 비교하면 안 된다 —
목차 타겟은 장을 감싸는 컨테이너를 가리키고 본문 제목은 별개 요소라 eid 가 다르고,
위치로 재면 나니아는 110개가 전부 1자 거리인데 그리스 신화의 '영웅 헤라클레스의 등장'
(진짜 누락)도 장 제목에서 6자 거리라 둘을 못 가른다. 제목 문자열은 이 둘을 정확히
가른다 — 나니아는 목차 레이블과 글자까지 같고, 그리스 신화 쪽은 목차에 아예 없다.

소제목 스타일 판정은 다음을 모두 만족해야 한다. 느슨하게 잡으면 소설 대사와
장식 기호, 책 뒤 색인이 쏟아져 들어온다:
  - 문장 종결부호·따옴표로 끝나는 것이 5% 이하   (대사 배제)
  - 최대 60자, 중앙값 40자 이하
  - 뒤따르는 요소의 스타일이 50% 이상 일정        ('제목 다음 첫 문단' 패턴)
  - 글자가 든 서로 다른 제목이 대부분             ('1 2 3', '◆', '* * *' 배제)

사용법:
    python tools/kfx_toc_survey.py "~/Calibre Library" -o survey.json
    # 중단돼도 같은 -o 로 다시 돌리면 이어서 한다
"""
import argparse, json, logging, re, sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kindle.ebook import _kfxlib_context

INV = "⁠⁣​﻿‌‍"
ENDERS = '.。?!…"\'’”)]」』”’'
WORD = re.compile(r"[가-힣A-Za-z]{2,}")


def norm(t):
    return re.sub(r"\s+", "", t.strip(INV)).strip()


def meaningful(texts):
    if not texts:
        return 0.0
    ok = sum(1 for t in texts if WORD.search(t) and len(t) >= 4) / len(texts)
    return ok * (len(set(texts)) / len(texts))


def analyze(path, YJ_Book, U):
    b = YJ_Book(str(path))
    b.decode_book(set_metadata=None)
    pos = b.collect_position_map_info()
    chunks = sorted([c for c in b.collect_content_position_info() if c.text], key=lambda c: c.pid)
    if not chunks:
        return None
    total = chunks[-1].pid + len(chunks[-1].text)
    first, order = {}, []
    for c in chunks:
        if c.eid not in first:
            first[c.eid] = (c.pid, c.text.strip(INV).strip())
            order.append(c.eid)

    style = {}

    def walk(n):
        n = U(n)
        if isinstance(n, dict):
            if "$155" in n and "$157" in n:
                style[n["$155"]] = str(n["$157"])
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    for f in b.fragments:
        if f.ftype in ("$259", "$260"):
            walk(f.value)

    frag = b.fragments.get("$389")
    if frag is None:
        return None
    toc_eids, toc_pids, toc_labels, toc_n = set(), [], set(), 0
    for bn in U(frag.value):
        bn = U(bn)
        for nc in U(bn.get("$392", [])):
            nc = U(nc)
            if str(nc.get("$235")) != "$212":
                continue

            def w(us):
                nonlocal toc_n
                for u in us:
                    u = U(u)
                    toc_n += 1
                    lbl = U(u.get("$241")) if "$241" in u else None
                    if lbl and lbl.get("$244"):
                        toc_labels.add(norm(str(lbl["$244"])))
                    p = U(u.get("$246")) if "$246" in u else None
                    if p and p.get("$155") is not None:
                        eid = p["$155"]
                        toc_eids.add(eid)
                        try:
                            pid = b.pid_for_eid(eid, p.get("$143", 0), pos)
                        except Exception:
                            pid = None
                        if pid is None:
                            pid = first.get(eid, (None,))[0]
                        if pid is not None:
                            toc_pids.append(pid)
                    if "$247" in u:
                        w(U(u["$247"]))

            w(U(nc.get("$247", [])))
    if toc_n == 0:
        return None

    nxt = {order[i]: order[i + 1] for i in range(len(order) - 1)}
    by_style = defaultdict(list)
    for eid in order:
        st = style.get(eid)
        if st and first[eid][1]:
            by_style[st].append(eid)

    best = None
    for st, eids in by_style.items():
        texts = [first[e][1] for e in eids]
        if not (5 <= len(eids) <= 500):
            continue
        if max(len(t) for t in texts) > 60 or median(len(t) for t in texts) > 40:
            continue
        ends = sum(1 for t in texts if t[-1] in ENDERS) / len(texts)
        if ends > 0.05:
            continue
        follow = Counter(style.get(nxt.get(e)) for e in eids if nxt.get(e) in style)
        cons = follow.most_common(1)[0][1] / sum(follow.values()) if follow else 0
        if cons < 0.5:
            continue
        new = [e for e in eids
               if e not in toc_eids and norm(first[e][1]) not in toc_labels]
        if len(new) < 5:
            continue
        m = meaningful([first[e][1] for e in new])
        if m < 0.8:
            continue
        score = len(new) * cons * m
        if best is None or score > best["score"]:
            best = {"style": st, "n": len(eids), "new": len(new), "ends": round(ends, 3),
                    "consistency": round(cons, 2), "meaningful": round(m, 2), "score": score,
                    "samples": [first[e][1][:44] for e in new[:5]]}
    if best is None:
        return None

    bounds = sorted(set(toc_pids)) + [total]
    gaps = [bounds[i + 1] - bounds[i] for i in range(len(bounds) - 1)] or [total]
    return {"total": total, "toc_n": toc_n, "max_gap": max(gaps),
            "ratio": round(max(gaps) / total, 3) if total else 0, **best}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("library", type=Path, help="캘리버 라이브러리 폴더")
    ap.add_argument("-o", "--out", type=Path, required=True)
    args = ap.parse_args()
    logging.basicConfig(level=logging.CRITICAL)

    files = sorted(Path(args.library).expanduser().rglob("*.kfx"))
    rows, skipped = [], []
    if args.out.exists():
        prev = json.loads(args.out.read_text())
        rows, skipped = prev.get("rows", []), prev.get("skipped", [])
    done = {r["file"] for r in rows} | {s[0] for s in skipped}
    todo = [f for f in files if str(f) not in done]
    print(f"{len(todo)}권 조사 (완료 {len(done)} / 전체 {len(files)})", flush=True)

    with _kfxlib_context():
        from kfxlib import YJ_Book, set_logger
        from kfxlib.ion import unannotated as U
        set_logger(logging.getLogger("survey"))
        for i, f in enumerate(todo, 1):
            try:
                r = analyze(f, YJ_Book, U)
                if r:
                    r["file"], r["name"] = str(f), f.stem
                    rows.append(r)
                else:
                    skipped.append((str(f), "no-heading-style"))
            except Exception as e:
                skipped.append((str(f), f"{type(e).__name__}: {e}"))
            if i % 10 == 0:
                print(f"  {i}/{len(todo)}  후보 {len(rows)}", flush=True)
                args.out.write_text(json.dumps({"rows": rows, "skipped": skipped},
                                               ensure_ascii=False, indent=1))
    args.out.write_text(json.dumps({"rows": rows, "skipped": skipped},
                                   ensure_ascii=False, indent=1))
    print(f"완료: 후보 {len(rows)}권 / 제외 {len(skipped)}권 → {args.out}", flush=True)


main()
