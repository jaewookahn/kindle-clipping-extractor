"""Directory scanner: recursively finds and parses all supported clipping files."""

import sys
from pathlib import Path
from typing import List

from kindle.models import Clipping, APNXInfo
from kindle.parsers import parse_my_clippings, parse_apnx, parse_mbp, parse_yjr, parse_yjf


def _sdr_book_title(sdr_path: Path) -> str:
    """
    Derive a human-readable book title from the .sdr folder name.
    E.g. "My Book - Author.sdr" → "My Book - Author"
    The long hash suffix in the filenames inside is stripped automatically
    since we use the folder name, not the file name.
    """
    name = sdr_path.name
    if name.endswith(".sdr"):
        name = name[:-4]
    return name


def scan_path(input_path: Path) -> tuple[List[Clipping], List[APNXInfo]]:
    """Recursively find and parse all supported files under input_path."""
    clippings: List[Clipping] = []
    apnx_infos: List[APNXInfo] = []

    # Collect files to process; if a .sdr directory is given, scan inside it
    if input_path.is_file():
        files = [input_path]
    else:
        files = list(input_path.rglob("*"))

    # Track which .sdr directories we've seen so we can derive the book title once
    seen_sdr: dict[Path, str] = {}
    for f in files:
        if f.is_file() and f.suffix.lower() in (".yjr", ".yjf"):
            # Walk up to find the nearest .sdr ancestor
            for parent in f.parents:
                if parent.suffix.lower() == ".sdr":
                    if parent not in seen_sdr:
                        seen_sdr[parent] = _sdr_book_title(parent)
                    break

    def _book_title_for(f: Path) -> str:
        for parent in f.parents:
            if parent in seen_sdr:
                return seen_sdr[parent]
        return f.stem

    for f in files:
        if not f.is_file():
            continue
        suffix = f.suffix.lower()
        name = f.name.lower()

        try:
            if name == "my clippings.txt" or (suffix == ".txt" and "clipping" in name):
                print(f"  Parsing My Clippings.txt: {f}")
                clippings.extend(parse_my_clippings(f))

            elif suffix == ".apnx":
                print(f"  Parsing APNX: {f}")
                apnx_infos.append(parse_apnx(f))

            elif suffix == ".mbp":
                print(f"  Parsing MBP: {f}")
                clippings.extend(parse_mbp(f, book_title=_book_title_for(f)))

            elif suffix == ".yjr" and not name.endswith(".bad_file"):
                print(f"  Parsing YJR: {f}")
                clippings.extend(parse_yjr(f, book_title=_book_title_for(f)))

            elif suffix == ".yjf":
                print(f"  Parsing YJF: {f}")
                clippings.extend(parse_yjf(f, book_title=_book_title_for(f)))

        except Exception as exc:
            print(f"  [warn] Failed to parse {f}: {exc}", file=sys.stderr)

    return clippings, apnx_infos
