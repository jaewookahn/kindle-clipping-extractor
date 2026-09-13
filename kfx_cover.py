#!/usr/bin/env python3
"""kfx_cover.py — KFX 표지 이미지만 갈아끼운다.

kfx_toc.py 와 같은 원칙이다. 본문 요소를 건드리지 않고 표지 리소스의 바이트와
가로·세로 값만 바꾸므로 eid·문자 오프셋·추정 페이지·킨들 로케이션이 그대로고,
`.sdr/*.yjr` 하이라이트가 살아남는다.

캘리버로 표지를 바꾸면 **안 된다** — KFX 를 다시 만들면서 eid 가 전부 새로
매겨져 하이라이트가 깨진다.

표지는 두 프래그먼트에 나뉘어 있다:
    $164 <eid>   {$165: 'resource/rsrcN', $422: 가로, $423: 세로, ...}   메타
    $417 resource/rsrcN   IonBLOB                                       실제 바이트

  show    : 현재 표지 정보와 이미지를 꺼낸다
  replace : 새 이미지로 갈아끼운다

사용법:
    python kfx_cover.py show book.kfx -o current_cover.jpg
    python kfx_cover.py replace book.kfx new.jpg -o out.kfx
    python kfx_toc.py verify book.kfx out.kfx --expect '$164,$417'
"""
import argparse, logging, sys
from pathlib import Path

from kindle.ebook import _kfxlib_context


def find_cover(b, U):
    """표지 메타($164)와 리소스($417)를 찾는다. kfxlib 가 표지로 인정한 바이트와
    같은 것을 고른다 — $164 가 여럿이라 크기만 보고 고르면 본문 삽화를 집는다."""
    data = b.get_cover_image_data()
    if not data:
        return None, None, None
    raw = bytes(data[1])
    for f in b.fragments:
        if f.ftype != "$417":
            continue
        if bytes(f.value) == raw:
            res_name = str(f.fid)
            for m in b.fragments:
                if m.ftype == "$164" and str(U(m.value).get("$165")) == res_name:
                    return m, f, data[0]
            return None, f, data[0]
    return None, None, data[0]


def cmd_show(args):
    with _kfxlib_context():
        from kfxlib import YJ_Book, set_logger
        from kfxlib.ion import unannotated as U
        set_logger(logging.getLogger("kfx_cover"))
        b = YJ_Book(str(args.kfx)); b.decode_book(set_metadata=None)
        meta, res, fmt = find_cover(b, U)
        if res is None:
            sys.exit("표지를 찾지 못했다.")
        v = U(meta.value) if meta is not None else {}
        print(f"표지 리소스 : {res.fid}  {len(bytes(res.value)):,} bytes  ({fmt})")
        print(f"표지 메타   : {meta.ftype if meta is not None else '없음'} {meta.fid if meta is not None else ''}"
              f"  {v.get('$422')} x {v.get('$423')}")
        if args.out:
            Path(args.out).write_bytes(bytes(res.value))
            print(f"→ {args.out}")


def cmd_replace(args):
    from PIL import Image
    img = Image.open(args.image)
    w, h = img.size
    new = Path(args.image).read_bytes()
    with _kfxlib_context():
        from kfxlib import YJ_Book, set_logger
        from kfxlib.ion import unannotated as U, IonBLOB, IonSymbol as IS
        from kfxlib.kfx_container import KfxContainer
        set_logger(logging.getLogger("kfx_cover"))
        b = YJ_Book(str(args.kfx)); b.decode_book(set_metadata=None)
        meta, res, _ = find_cover(b, U)
        if res is None:
            sys.exit("표지를 찾지 못했다.")
        old_n = len(bytes(res.value))
        v = U(meta.value) if meta is not None else {}
        print(f"기존: {res.fid}  {old_n:,} bytes  {v.get('$422')} x {v.get('$423')}")
        res.value = IonBLOB(new)
        if meta is not None:
            v[IS("$422")] = w
            v[IS("$423")] = h
        print(f"신규: {len(new):,} bytes  {w} x {h}")
        data = KfxContainer(b.symtab, fragments=b.fragments).serialize()
        Path(args.out).write_bytes(data)
        print(f"→ {args.out}  ({len(data):,} bytes)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("show"); p.add_argument("kfx", type=Path)
    p.add_argument("-o", "--out"); p.set_defaults(fn=cmd_show)
    p = sub.add_parser("replace"); p.add_argument("kfx", type=Path)
    p.add_argument("image", type=Path); p.add_argument("-o", "--out", required=True)
    p.set_defaults(fn=cmd_replace)
    args = ap.parse_args()
    logging.basicConfig(level=logging.ERROR)
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
