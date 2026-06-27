"""Command-line interface for the Kindle clippings parser."""

import argparse
import platform
import sys
from pathlib import Path

from kindle.ebook import (
    _find_sdr_parent,
    find_paired_ebook,
    extract_kfx_info,
    extract_book_text,
    fill_clipping_text,
    fill_clipping_pages,
    fill_clipping_chapters,
    fill_clipping_kindle_locations,
)
from kindle.exporters import export_json, export_csv, export_markdown, export_text
from kindle.scanner import scan_path, list_books
from kindle.device import (
    find_kindle, print_device_books, print_mtp_books,
    list_device_clippings_file, mtp_mount, mtp_direct_session,
    _find_fuse_mtp_tool, _load_libmtp,
)


def _export_from_path(input_path: Path, args) -> None:
    """Parse clippings at *input_path* and write them to args.output."""
    out_path = Path(args.output)

    fmt = args.format
    if fmt is None:
        ext_map = {".json": "json", ".csv": "csv", ".md": "markdown", ".txt": "text"}
        fmt = ext_map.get(out_path.suffix.lower(), "json")
        print(f"Output format: {fmt} (inferred from extension)")

    print(f"Scanning: {input_path}")
    clippings, apnx_infos = scan_path(input_path)
    print(f"Found {len(clippings)} clippings, {len(apnx_infos)} APNX page indexes")

    ebook_override = Path(args.ebook) if args.ebook else None
    sdr_to_clippings: dict[Path, list] = {}
    for c in clippings:
        src = Path(c.source_file)
        sdr = _find_sdr_parent(src)
        if sdr:
            sdr_to_clippings.setdefault(sdr, []).append(c)

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
            print(f"  Extracting text, page map, and Kindle Locations from: {ebook.name} …")
            page_map, kl_offsets, book_text, toc = extract_kfx_info(ebook)

            if not args.no_text and book_text:
                before = sum(1 for c in group if c.content and c.content.startswith("["))
                fill_clipping_text(group, book_text)
                after  = sum(1 for c in group if c.content and not c.content.endswith("]"))
                print(f"  Filled text for {after - (len(group) - before)} highlights")
            elif not args.no_text:
                print(f"  [warn] Text extraction failed for {ebook.name}", file=sys.stderr)

            if not args.no_pages and page_map:
                fill_clipping_pages(group, page_map)
                paged = sum(1 for c in group if c.page is not None)
                print(f"  Assigned page numbers to {paged} clippings "
                      f"(pp. {page_map[0][0]}–{page_map[-1][0]})")

            if not args.no_pages and toc:
                fill_clipping_chapters(group, toc)
                chaptered = sum(1 for c in group if c.chapter)
                print(f"  Assigned chapters to {chaptered} clippings "
                      f"({len(toc)} TOC entries)")

            if not args.no_pages and kl_offsets:
                fill_clipping_kindle_locations(group, kl_offsets)
                print(f"  Converted locations to Kindle Location numbers "
                      f"(1–{len(kl_offsets)})")
            elif not args.no_pages:
                print(f"  [info] No location map found in {ebook.name} (plugin may be missing)")

        else:
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


def main():
    parser = argparse.ArgumentParser(
        description="Parse Kindle/Mobipocket clippings and export them.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=None,
        help="Input file or directory to scan (My Clippings.txt, *.mbp, *.apnx). "
             "Optional when --device is used.",
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Output file path (required unless --list-books or --device is used)",
    )
    parser.add_argument(
        "--device",
        action="store_true",
        help="Auto-detect a connected Kindle device and list its books and clippings.",
    )
    parser.add_argument(
        "--mtp",
        action="store_true",
        help="Access Kindle via MTP. "
             "Linux: uses jmtpfs FUSE mount (apt install jmtpfs). "
             "macOS: falls back to libmtp CLI (brew install libmtp). "
             "On macOS, Kindle usually auto-mounts — try --device first.",
    )
    parser.add_argument(
        "--mtp-mountpoint",
        default=None,
        metavar="DIR",
        help="Directory to use as the MTP mount point (default: auto temp dir).",
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
        "--export-all",
        action="store_true",
        help="Export ALL clippings from every book on the device (.yjr/.yjf/.mbp + "
             "My Clippings.txt). Use with --device or --mtp and -o.",
    )
    parser.add_argument(
        "--list-books",
        action="store_true",
        help="Print a list of books found in the input and exit (no output file written).",
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

    # --mtp: access Kindle via MTP.
    # Priority:
    #   1. Device already OS-mounted → filesystem directly (no MTP, no eject risk)
    #   2. FUSE tool available (Linux) → FUSE mount
    #   3. libmtp available → single ctypes session (one open, one close, no repeated eject)
    if args.mtp:
        mountpoint = Path(args.mtp_mountpoint) if args.mtp_mountpoint else None
        try:
            already_mounted = find_kindle()
            if already_mounted:
                print(f"  Kindle already mounted at {already_mounted} — using filesystem.")
                print_device_books(already_mounted)
                if args.output:
                    if args.export_all:
                        print(f"\nExporting all clippings …")
                        _export_from_path(already_mounted / "documents", args)
                    else:
                        clippings_file = list_device_clippings_file(already_mounted)
                        if clippings_file:
                            print(f"\nExporting clippings from {clippings_file} …")
                            _export_from_path(clippings_file, args)
                        else:
                            print("[warn] My Clippings.txt not found.", file=sys.stderr)

            elif _find_fuse_mtp_tool():
                with mtp_mount(mountpoint) as kindle_root:
                    print_device_books(kindle_root)
                    if args.output:
                        if args.export_all:
                            _export_from_path(kindle_root / "documents", args)
                        else:
                            clippings_file = list_device_clippings_file(kindle_root)
                            if clippings_file:
                                _export_from_path(clippings_file, args)

            elif _load_libmtp():
                # Single ctypes session: device opened once, all ops inside, closed once.
                # No repeated detach/attach → no eject.
                with mtp_direct_session() as session:
                    print_mtp_books(session)
                    if args.output:
                        if args.export_all:
                            print("\nDownloading all annotation files …")
                            tmpdir = session.fetch_all_annotations()
                            print("Exporting …")
                            _export_from_path(tmpdir, args)
                        else:
                            clippings_file = session.fetch_clippings_txt()
                            if clippings_file:
                                print("\nExporting clippings …")
                                _export_from_path(clippings_file, args)
                            else:
                                print("[warn] My Clippings.txt not found.", file=sys.stderr)

            else:
                if platform.system() == "Linux":
                    msg = "sudo apt install libmtp-dev  or  sudo apt install jmtpfs"
                else:
                    msg = "brew install libmtp"
                print(f"Error: no MTP library found. Install it:  {msg}", file=sys.stderr)
                sys.exit(1)

        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    # --device: use OS auto-mount, list books/clippings, then optionally export
    if args.device:
        kindle_root = find_kindle()
        if not kindle_root:
            print("Error: no connected Kindle device found.", file=sys.stderr)
            sys.exit(1)
        print_device_books(kindle_root)

        if args.output:
            if args.export_all:
                print(f"\nExporting all clippings from {kindle_root / 'documents'} …")
                _export_from_path(kindle_root / "documents", args)
                return
            clippings_file = list_device_clippings_file(kindle_root)
            if clippings_file:
                print(f"\nExporting clippings from {clippings_file} …")
                args.input = str(clippings_file)
            else:
                return
        else:
            return

    if not args.input:
        parser.error("input is required unless --device is used")

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    if args.list_books:
        clippings, _ = scan_path(input_path)
        books = list_books(clippings)
        if not books:
            print("No books found.")
        else:
            print(f"{'#':<5} {'Title':<60} {'Author':<30} Clippings")
            print("-" * 100)
            for i, b in enumerate(books, 1):
                print(f"{i:<5} {b['title']:<60} {b['author']:<30} {b['count']}")
        return

    if not args.output:
        parser.error("--output is required unless --list-books or --device is used")

    _export_from_path(input_path, args)
