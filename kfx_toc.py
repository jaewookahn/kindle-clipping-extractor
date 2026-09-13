#!/usr/bin/env python3
"""kfx_toc.py — KFX 목차($389/$212)만 수술적으로 고쳐 쓴다.

목차가 너무 성긴 책(장 5개인데 소제목 수십 개)을 쓸 만하게 만드는 도구다.
**본문 요소를 하나도 추가·삭제하지 않는다** — 이미 존재하는 eid를 가리키기만 한다.
따라서 eid·문자 오프셋·추정 페이지·킨들 로케이션이 전부 그대로고,
`.sdr/*.yjr` 하이라이트가 살아남는다.

목차를 xhtml 요소로 끼워넣거나 EPUB로 변환했다가 재생성하면 **안 된다** —
전자는 본문이 늘어나 추정 페이지가 밀리고, 후자는 eid가 전부 새로 매겨져
하이라이트가 통째로 깨진다.

  plan   : 본문에서 소제목 스타일 요소를 찾아 목차 초안 JSON을 낸다 (사람이 검토·수정)
  apply  : JSON대로 $212 nav 컨테이너를 다시 만들어 KFX를 쓴다
  verify : 원본 대비 $389 외 프래그먼트·페이지맵·로케이션맵·본문이 동일한지 확인

사용법:
    python kfx_toc.py plan book.kfx --list-styles           # 스타일 id 분포부터 본다
    python kfx_toc.py plan book.kfx --heading-styles s59,s6A -o toc_plan.json
    # toc_plan.json 을 눈으로 검토·수정한 뒤
    python kfx_toc.py apply book.kfx toc_plan.json -o new.kfx
    python kfx_toc.py verify book.kfx new.kfx               # 기기에 넣기 전 필수

소제목은 휴리스틱이 아니라 **스타일 id**로 고른다 — 길이나 문장부호로 짐작하면
대사와 그림 캡션이 섞여 들어온다. 스타일 id는 책마다 다르므로 `--list-styles`로
먼저 확인할 것 (그리스 신화: s4P 장 제목 / s6A 장 첫머리 소제목 / s59 나머지
소제목 / s51 본문).

기기 전송은 이 스크립트가 하지 않는다. calibre "장치로 보내기"를 쓸 것 —
MacDroid 무료 버전은 읽기 전용인데 `cp`가 성공을 반환해 안 들어간 걸 모른다.
직접 MTP로 넣어야 하면 tools/mtp_put.c 참고.
"""
import argparse, copy, json, logging, re, sys
from collections import Counter, defaultdict
from pathlib import Path

from kindle.ebook import _kfxlib_context

INVISIBLE = "⁠⁣​﻿‌‍"
TOC_NAV, PAGE_NAV, SECTION_NAV = "$212", "$237", "$798"


def clean_title(s):
    s = s.strip(INVISIBLE)
    s = re.sub(r"[%s]+" % INVISIBLE, "", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"(?<=[^\s\d])\d{1,3}$", "", s).strip()   # 꼬리 각주 번호
    return s


def load(kfx_path, U, YJ_Book):
    b = YJ_Book(str(kfx_path))
    b.decode_book(set_metadata=None)
    return b


def pid_resolver(b):
    """eid+offset -> 절대 문자 오프셋. 목차 타겟은 텍스트 요소가 아니라 컨테이너를
    가리키는 책이 있어서(『권력과 진보』의 장 항목이 그랬다) 본문 청크만으로는
    위치를 못 찾는다. 위치맵을 쓰면 그런 eid도 풀린다."""
    pos_info = b.collect_position_map_info()

    def resolve(eid, offset=0):
        try:
            return b.pid_for_eid(eid, offset, pos_info)
        except Exception:
            return None
    return resolve


def index_book(b, U):
    """eid -> (pid, section, text, style) 및 pid 정렬 목록."""
    chunks = sorted([c for c in b.collect_content_position_info() if c.text], key=lambda c: c.pid)
    first = {}
    for c in chunks:
        if c.eid not in first:
            first[c.eid] = (c.pid, str(c.section_name), c.text)
    style = {}
    def walk(n):
        n = U(n)
        if isinstance(n, dict):
            if "$155" in n and "$157" in n:
                style[n["$155"]] = str(n["$157"])
            for v in n.values(): walk(v)
        elif isinstance(n, list):
            for v in n: walk(v)
    for f in b.fragments:
        if f.ftype in ("$259", "$260"): walk(f.value)
    return first, style


def nav_containers(b, U):
    frag = b.fragments.get("$389")
    out = []
    for bn in U(frag.value):
        bn = U(bn)
        for nc in U(bn.get("$392", [])):
            out.append((bn, nc, U(nc)))
    return frag, out


def existing_toc(ncs, U):
    for bn, raw, nc in ncs:
        if str(nc.get("$235")) == TOC_NAV:
            return bn, raw, nc
    return None, None, None


def unit_info(u, U):
    u = U(u)
    lbl = U(u.get("$241")) if "$241" in u else None
    pos = U(u.get("$246")) if "$246" in u else None
    return (lbl.get("$244") if lbl else None,
            pos.get("$155") if pos else None,
            pos.get("$143", 0) if pos else 0)


# ---------------------------------------------------------------- plan
def cmd_plan(args):
    with _kfxlib_context():
        from kfxlib import YJ_Book, set_logger
        from kfxlib.ion import unannotated as U
        set_logger(logging.getLogger("kfx_toc"))
        b = load(args.kfx, U, YJ_Book)
        first, style = index_book(b, U)
        pid_of = pid_resolver(b)
        frag, ncs = nav_containers(b, U)
        _, _, toc = existing_toc(ncs, U)
        if toc is None:
            sys.exit("이 책에는 $212 목차 nav 컨테이너가 없다.")

        def read_tree(units):
            out = []
            for u in units:
                t, eid, off = unit_info(u, U)
                pid = pid_of(eid, off)
                if pid is None:
                    pid = first.get(eid, (None,))[0]
                node = {"title": t, "eid": eid, "offset": off,
                        "pid": pid, "children": []}
                if "$247" in U(u):
                    node["children"] = read_tree(U(U(u)["$247"]))
                out.append(node)
            return out

        chapters = read_tree(U(toc.get("$247", [])))

        if args.list_styles:
            cnt = Counter(style.values())
            print(f"{'style':>8} {'n':>6}  예시")
            for s, n in cnt.most_common(25):
                ex = next((clean_title(first[e][2])[:40] for e, st in style.items()
                           if st == s and e in first and clean_title(first[e][2])), "")
                print(f"{s:>8} {n:>6}  {ex!r}")
            return

        wanted = set(args.heading_styles.split(","))
        heads = sorted(
            [{"title": clean_title(first[e][2]), "eid": e, "pid": first[e][0], "style": st}
             for e, st in style.items() if st in wanted and e in first and clean_title(first[e][2])],
            key=lambda h: h["pid"])

        # 소제목을 pid 범위로 장에 귀속
        known = sorted([c for c in chapters if c["pid"] is not None], key=lambda c: c["pid"])
        seen = set()
        def collect(ns):
            for n in ns:
                seen.add(n["eid"]); collect(n["children"])
        collect(chapters)
        heads = [h for h in heads if h["eid"] not in seen]   # 이미 목차에 있는 건 건드리지 않는다
        for h in heads:
            owner = None
            for c in known:
                if c["pid"] <= h["pid"]:
                    owner = c
                else:
                    break
            if owner is None:
                print(f"  [경고] 귀속할 장 없음: {h['title']!r} (pid {h['pid']})", file=sys.stderr)
                continue
            owner["children"].append({"title": h["title"], "eid": h["eid"],
                                      "offset": 0, "pid": h["pid"], "children": []})
            owner["children"].sort(key=lambda x: (x["pid"] is None, x["pid"] or 0))

        plan = {"kfx": str(args.kfx), "heading_styles": sorted(wanted), "chapters": chapters}
        Path(args.out).write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        def cnt(ns): return sum(1 + cnt(n["children"]) for n in ns)
        n = cnt(chapters) - len(chapters)
        print(f"목차 초안: 장 {len(chapters)}개 + 소제목 {n}개 → {args.out}")
        def show(ns, d=0):
            for n in ns:
                mark = f"  ({len(n['children'])})" if n["children"] else ""
                print("  " + "   " * d + ("- " if d else "") + str(n["title"]) + mark)
                show(n["children"], d + 1)
        show(chapters)


# ---------------------------------------------------------------- apply
def cmd_apply(args):
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    with _kfxlib_context():
        from kfxlib import YJ_Book, set_logger
        from kfxlib.ion import unannotated as U, IonSymbol as IS
        from kfxlib.kfx_container import KfxContainer
        set_logger(logging.getLogger("kfx_toc"))
        b = load(args.kfx, U, YJ_Book)
        frag, ncs = nav_containers(b, U)
        _, _, toc = existing_toc(ncs, U)
        old_units = list(U(toc.get("$247", [])))
        if not old_units:
            sys.exit("기존 목차 항목이 없어 본을 뜰 수 없다.")

        template = old_units[0]           # Ion 주석·타입을 그대로 물려받기 위한 본
        def make_unit(title, eid, offset, children):
            u = copy.deepcopy(template)
            raw = U(u)
            U(raw["$241"])[IS("$244")] = title
            pos = U(raw["$246"]); pos[IS("$155")] = eid; pos[IS("$143")] = offset
            raw.pop("$238", None)         # nav unit id는 새로 안 만든다
            raw.pop("$247", None)
            if children:
                raw[IS("$247")] = children
            return u

        def build(nodes):
            return [make_unit(n["title"], n["eid"], n.get("offset", 0),
                              build(n.get("children", [])) or None) for n in nodes]
        new_units = build(plan["chapters"])

        U(toc)[IS("$247")] = new_units
        data = KfxContainer(b.symtab, fragments=b.fragments).serialize()
        Path(args.out).write_bytes(data)
        def cnt(ns): return sum(1 + cnt(n.get("children", [])) for n in ns)
        total = cnt(plan["chapters"])
        print(f"목차 {total}항목 기록 → {args.out}  ({len(data):,} bytes)")


# ---------------------------------------------------------------- verify
def cmd_verify(args):
    with _kfxlib_context():
        from kfxlib import YJ_Book, set_logger
        from kfxlib.ion import unannotated as U
        set_logger(logging.getLogger("kfx_toc"))

        def snapshot(path):
            b = load(path, U, YJ_Book)
            pos_info = b.collect_position_map_info()
            loc = b.collect_location_map_info(pos_info)
            pages, toc_dump = [], []
            frag, ncs = nav_containers(b, U)
            for bn, raw, nc in ncs:
                kind = str(nc.get("$235"))
                if kind == PAGE_NAV:
                    for e in U(nc.get("$247", [])):
                        t, eid, off = unit_info(e, U)
                        pages.append((t, b.pid_for_eid(eid, off, pos_info)))
                elif kind == TOC_NAV:
                    def w(us, d=0):
                        for e in us:
                            t, eid, off = unit_info(e, U)
                            toc_dump.append(("  " * d) + f"{t} -> {eid}+{off}")
                            if "$247" in U(e): w(U(U(e)["$247"]), d + 1)
                    w(U(nc.get("$247", [])))
            chunks = sorted([(c.pid, c.eid, c.eid_offset, c.text)
                             for c in b.collect_content_position_info() if c.text])
            frags = {}
            for f in b.fragments:
                frags.setdefault((f.ftype, str(f.fid)), []).append(repr(f.value))
            return {"frags": frags, "pages": pages,
                    "locs": [e.pid for e in loc] if loc else None,
                    "chunks": chunks, "toc": toc_dump}

        a, c = snapshot(args.kfx), snapshot(args.new)
        ok = True
        diff = sorted(k for k in set(a["frags"]) | set(c["frags"])
                      if a["frags"].get(k) != c["frags"].get(k))
        print(f"  {'달라진 프래그먼트':<20}: {diff or '없음'}")
        expect = set(args.expect.split(",")) if args.expect else {"$389"}
        ok &= all(ftype in expect for ftype, _ in diff)
        for label, key in (("추정 페이지 맵", "pages"),
                           ("킨들 로케이션 경계", "locs"),
                           ("본문 텍스트·오프셋", "chunks")):
            same = a[key] == c[key]
            n = len(a[key]) if a[key] else 0
            print(f"  {label:<20}: {'동일' if same else '★ 달라짐 ★'} ({n:,}건)")
            ok &= same
        print(f"  {'목차 항목 수':<20}: {len(a['toc'])} → {len(c['toc'])}")
        print("\n" + ("✅ 하이라이트 앵커 전부 보존됨" if ok else "❌ 검증 실패 — 기기에 넣지 말 것"))
        return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan"); p.add_argument("kfx", type=Path)
    p.add_argument("-o", "--out", default="toc_plan.json")
    p.add_argument("--heading-styles", default="s59,s6A")
    p.add_argument("--list-styles", action="store_true"); p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("apply"); p.add_argument("kfx", type=Path)
    p.add_argument("plan"); p.add_argument("-o", "--out", required=True); p.set_defaults(fn=cmd_apply)
    p = sub.add_parser("verify"); p.add_argument("kfx", type=Path)
    p.add_argument("new", type=Path)
    p.add_argument("--expect", help="달라져도 되는 프래그먼트 타입 (쉼표 구분, 기본 $389). "
                                    "표지를 바꿨으면 '$164,$417'")
    p.set_defaults(fn=cmd_verify)
    args = ap.parse_args()
    logging.basicConfig(level=logging.ERROR)
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
