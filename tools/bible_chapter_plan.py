#!/usr/bin/env python3
"""성경 KFX 전용 목차 계획 생성기 — kfx_toc.py 의 plan 을 대신한다.

kfx_toc.py 의 plan 은 '스타일이 같은 짧은 요소'를 소제목으로 본다. 성경은 그 방식이
안 통한다 — 장 시작이 스타일 하나로 통일돼 있지 않고, 절 번호·상호참조가 잔뜩 섞인다.
대신 이 책은 권 첫 페이지에 장 링크가 있어서 그걸 읽는 편이 정확하다.
여기서 낸 JSON 을 `kfx_toc.py apply` 에 그대로 먹이면 된다 — plan 과 apply 를
나눠둔 이유가 이것이다.

성경 KFX: 각 권 첫 페이지의 '장 링크' 줄을 읽어 kfx_toc.py 용 목차 계획을 만든다.

권 첫 페이지에 `Genesis 1 • Genesis 2 • …` 형태의 줄이 있고, 각 링크가 $142 span
({$143 오프셋, $144 길이, $179 앵커이름})으로 걸려 있다. 앵커는 $266 프래그먼트가
{$180: 이름, $183: {$155: eid, $143: offset}} 로 실제 위치를 준다.
본문 정규식으로 장 표지(GENESIS 1)를 긁으면 945개밖에 안 잡혔는데, 링크는 전수다.
"""
import json, logging, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kindle.ebook import _kfxlib_context
logging.basicConfig(level=logging.CRITICAL)
INV = "⁠⁣​﻿‌‍"
SRC, OUT = sys.argv[1], sys.argv[2]

with _kfxlib_context():
    from kfxlib import YJ_Book, set_logger
    from kfxlib.ion import unannotated as U
    set_logger(logging.getLogger("bp"))
    b = YJ_Book(SRC); b.decode_book(set_metadata=None)
    pos = b.collect_position_map_info()
    chunks = sorted([c for c in b.collect_content_position_info() if c.text], key=lambda c: c.pid)
    first = {}
    for c in chunks:
        if c.eid not in first: first[c.eid] = (c.pid, c.text)

    anchors = {}
    for f in b.fragments:
        if f.ftype == "$266":
            v = U(f.value); t = U(v.get("$183", {}))
            anchors[str(v.get("$180"))] = (t.get("$155"), t.get("$143", 0))

    raw = {}
    def walk(n):
        n = U(n)
        if isinstance(n, dict):
            if "$155" in n and "$142" in n: raw.setdefault(n["$155"], n)
            for v in n.values(): walk(v)
        elif isinstance(n, list):
            for v in n: walk(v)
    for f in b.fragments:
        if f.ftype in ("$259", "$260"): walk(f.value)

    # 장 링크 줄: 링크 span 이 3개 이상이고 링크 텍스트가 '이름 숫자' 꼴
    LINK = re.compile(r"^(.+?)\s+(\d+)$")
    # 본문 장 표지: 'GENESIS 1', '2 CHRONICLES 1', 'SONG OF SOLOMON 3'
    # 숫자 뒤에 한글이 바로 붙는 경우가 있다('2 CHRONICLES 1다윗의'). \b 는 한글도
    # 단어 문자로 보므로 경계를 못 잡는다 — 뒤에 숫자만 안 오면 되는 것으로 바꾼다.
    MARK = re.compile(r"^(\d?\s?[A-Z][A-Z]*(?: [A-Z]+)*) (\d+)(?![0-9])")
    marks = []
    for eid, (pid, txt) in first.items():
        m = MARK.match(txt.strip(INV).strip())
        if m: marks.append((pid, eid, m.group(1).strip(), int(m.group(2))))
    marks.sort()
    print(f"본문 장 표지 {len(marks)}개")
    link_lines = []
    for eid, n in raw.items():
        if eid not in first: continue
        txt = first[eid][1]
        spans = [U(s) for s in U(n["$142"])]
        links = [s for s in spans if s.get("$179") and s.get("$144")]
        if not links: continue
        items = []
        for s in links:
            label = txt[s["$143"]: s["$143"] + s["$144"]].strip(INV).strip()
            m = LINK.match(label)
            tgt = anchors.get(str(s["$179"]))
            if m and tgt and tgt[0] is not None:
                items.append((int(m.group(2)), tgt[0], tgt[1]))
        # 장 번호가 1부터 이어지는 묶음만 장 목차로 인정 (본문 상호참조 링크 배제)
        nums = sorted(n for n, _, _ in items)
        if items and nums[0] == 1 and nums == list(range(1, len(nums) + 1)):
            link_lines.append((first[eid][0], items))
    link_lines.sort()
    print(f"장 링크 줄 {len(link_lines)}개, 링크 총 {sum(len(i) for _, i in link_lines)}개")

    # 현재 목차(권) 읽기
    frag = b.fragments.get("$389")
    books = []
    for bn in U(frag.value):
        bn = U(bn)
        for nc in U(bn.get("$392", [])):
            nc = U(nc)
            if str(nc.get("$235")) != "$212": continue
            for u in U(nc.get("$247", [])):
                u = U(u)
                lbl = U(u.get("$241")); p = U(u.get("$246"))
                eid, off = p.get("$155"), p.get("$143", 0)
                try: pid = b.pid_for_eid(eid, off, pos)
                except Exception: pid = None
                books.append({"title": lbl.get("$244"), "eid": eid, "offset": off,
                              "pid": pid, "children": []})
    print(f"현재 목차 권 {len(books)}개")

    # 링크 줄을 pid 로 권에 귀속
    known = sorted([x for x in books if x["pid"] is not None], key=lambda x: x["pid"])
    used = 0
    for pid, items in link_lines:
        owner = None
        for bk in known:
            if bk["pid"] <= pid: owner = bk
            else: break
        if owner is None: continue
        for num, teid, toff in items:
            if teid not in first and b.pid_for_eid(teid, toff, pos) is None: continue
            owner["children"].append({"title": f"{num}장", "eid": teid, "offset": toff,
                                      "children": []})
            used += 1

    # 링크가 유실·누락된 권(역대하는 1개, 미가는 4개뿐이었다)은 본문 장 표지로 메운다.
    # 둘 중 장 수가 많은 쪽을 택한다 — 링크는 오프셋까지 있어 같으면 링크 우선.
    bounds = [(bk, known[i + 1]["pid"] if i + 1 < len(known) else 10 ** 12)
              for i, bk in enumerate(known)]
    swapped = []
    for bk, end in bounds:
        got = [(n, e) for pid, e, _, n in marks if bk["pid"] <= pid < end]
        seen, uniq = set(), []
        for n, e in got:
            if n in seen: continue
            seen.add(n); uniq.append((n, e))
        if len(uniq) > len(bk["children"]):
            swapped.append((bk["title"], len(bk["children"]), len(uniq)))
            bk["children"] = [{"title": f"{n}장", "eid": e, "offset": 0, "children": []}
                              for n, e in uniq]
    if swapped:
        print("본문 표지로 교체한 권:", ", ".join(f"{t}({a}→{b})" for t, a, b in swapped))
    for bk in books:                       # 장 번호 순 정렬 + 중복 제거
        seen, out = set(), []
        for ch in sorted(bk["children"], key=lambda c: int(c["title"][:-1])):
            if ch["title"] in seen: continue
            seen.add(ch["title"]); out.append(ch)
        bk["children"] = out

    total = sum(len(bk["children"]) for bk in books)
    print(f"붙인 장 {total}개\n")
    for bk in books[:6]:
        print(f"  {bk['title']}  ({len(bk['children'])}장)")
    print("   ...")
    for bk in books[-6:]:
        print(f"  {bk['title']}  ({len(bk['children'])}장)")
    empty = [bk["title"] for bk in books if not bk["children"]]
    print(f"\n장이 안 붙은 권 {len(empty)}개: {empty}")
    json.dump({"kfx": SRC, "chapters": books}, open(OUT, "w"), ensure_ascii=False, indent=1)
