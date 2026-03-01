"""Command-line interface for the Kindle clippings parser."""

import argparse
import sys
from pathlib import Path

from kindle.ebook import (
    _find_sdr_parent,
    find_paired_ebook,
    extract_kfx_info,
    extract_book_text,
    fill_clipping_text,
    fill_clipping_pages,
    fill_clipping_kindle_locations,
)
from kindle.exporters import export_json, export_csv, export_markdown, export_text
from kindle.scanner import scan_path


def main():
    parser = argparse.ArgumentParser(
        description="Parse Kindle/Mobipocket clippings and export them.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "input",
        help="Input file or directory to scan (My Clippings.txt, *.mbp, *.apnx)",
    )
    parser.add_argument(
        "-o", "--output",
        required=True,
        help="Output file path",
    )
    parser.add_argument(
        "-f", "--format",
        choices=["json", "csv", "markdown", "text"],
        default=None,
        help="Output format (default: inferred from output file extension)",
    )
    parser.add_argument(
        "--ebook",
        default=None,
        metavar="EBOOK",
        help="Ebook file (KFX, AZW3, MOBI, EPUB …) to extract highlight text and "
             "page numbers from.  If omitted, the script tries to auto-detect a "
             "sibling ebook next to every .sdr folder it finds.",
    )
    parser.add_argument(
        "--no-text",
        action="store_true",
        help="Skip highlight text extraction (useful if Calibre is slow or unavailable).",
    )
    parser.add_argument(
        "--no-pages",
        action="store_true",
        help="Skip page-number extraction (requires KFX Input Calibre plugin).",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.output)

    # Infer format from extension if not specified
    fmt = args.format
    if fmt is None:
        ext_map = {".json": "json", ".csv": "csv", ".md": "markdown", ".txt": "text"}
        fmt = ext_map.get(out_path.suffix.lower(), "json")
        print(f"Output format: {fmt} (inferred from extension)")

    print(f"Scanning: {input_path}")
    clippings, apnx_infos = scan_path(input_path)
    print(f"Found {len(clippings)} clippings, {len(apnx_infos)} APNX page indexes")

    # --- Text extraction + page number population ---
    ebook_override = Path(args.ebook) if args.ebook else None

    # Group YJR/YJF clippings by their .sdr parent so we can find the paired ebook.
    sdr_to_clippings: dict[Path, list] = {}
    for c in clippings:
        src = Path(c.source_file)
        sdr = _find_sdr_parent(src)
        if sdr:
            sdr_to_clippings.setdefault(sdr, []).append(c)

    # If no .sdr groupings (e.g. My Clippings.txt only) but --ebook was given,
    # apply text extraction to all clippings.
    if not sdr_to_clippings and ebook_override:
        sdr_to_clippings[Path(".")] = clippings

    for sdr, group in sdr_to_clippings.items():
        ebook = ebook_override or (find_paired_ebook(sdr) if sdr != Path(".") else None)
        if not ebook:
            print(f"  [info] No ebook found next to {sdr.name} — skipping text/page extraction")
            continue
        if not ebook.exists():
            print(f"  [warn] Ebook not found: {ebook}", file=sys.stderr)
            continue

        if ebook.suffix.lower() == ".kfx":
            # For KFX files: use kfxlib for text, pages, and Kindle Locations in one pass.
            # kfxlib preserves the exact KFX internal char offsets used by YJR annotations,
            # whereas Calibre's ebook-convert introduces position drift.
            print(f"  Extracting text, page map, and Kindle Locations from: {ebook.name} …")
            page_map, kl_offsets, book_text = extract_kfx_info(ebook)

            if not args.no_text and book_text:
                before = sum(1 for c in group if c.content and c.content.startswith("["))
                fill_clipping_text(group, book_text)
                after  = sum(1 for c in group if c.content and not c.content.endswith("]"))
                print(f"  Filled text for {after - (len(group) - before)} highlights")
            elif not args.no_text:
                print(f"  [warn] Text extraction failed for {ebook.name}", file=sys.stderr)

            # fill_clipping_pages must use char offsets → call before KL conversion
            if not args.no_pages and page_map:
                fill_clipping_pages(group, page_map)
                paged = sum(1 for c in group if c.page is not None)
                print(f"  Assigned page numbers to {paged} clippings "
                      f"(pp. {page_map[0][0]}–{page_map[-1][0]})")

            # Convert char offsets → Kindle Location numbers for display
            if not args.no_pages and kl_offsets:
                fill_clipping_kindle_locations(group, kl_offsets)
                print(f"  Converted locations to Kindle Location numbers "
                      f"(1–{len(kl_offsets)})")
            elif not args.no_pages:
                print(f"  [info] No location map found in {ebook.name} (plugin may be missing)")

        else:
            # For non-KFX formats: use Calibre's ebook-convert for text extraction.
            # Page numbers and Kindle Locations are not available for these formats.
            if not args.no_text:
                print(f"  Extracting highlight text from: {ebook.name} …")
                book_text = extract_book_text(ebook)
                if book_text:
                    before = sum(1 for c in group if c.content and c.content.startswith("["))
                    fill_clipping_text(group, book_text)
                    after  = sum(1 for c in group if c.content and not c.content.endswith("]"))
                    print(f"  Filled text for {after - (len(group) - before)} highlights")
                else:
                    print(f"  [warn] Text extraction failed for {ebook.name}", file=sys.stderr)

    clippings.sort(key=lambda c: c.added_date or "")

    if fmt == "json":
        export_json(clippings, apnx_infos, out_path)
    elif fmt == "csv":
        export_csv(clippings, out_path)
    elif fmt == "markdown":
        export_markdown(clippings, out_path)
    elif fmt == "text":
        export_text(clippings, out_path)
