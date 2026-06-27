"""Calibre/KFX-based ebook text and page-map extraction."""

import contextlib
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Iterator, Optional, List

from kindle.models import Clipping

_CALIBRE_PATHS = [
    "/Applications/calibre.app/Contents/MacOS/ebook-convert",
    "/usr/bin/ebook-convert",
    "/usr/local/bin/ebook-convert",
]

_KFX_PLUGIN_PATHS = [
    "~/Library/Preferences/calibre/plugins/KFX Input.zip",
    "~/.config/calibre/plugins/KFX Input.zip",
]


def _find_ebook_convert() -> Optional[str]:
    for p in _CALIBRE_PATHS:
        if Path(p).exists():
            return p
    return None


def _find_kfx_plugin() -> Optional[str]:
    for p in _KFX_PLUGIN_PATHS:
        expanded = Path(p).expanduser()
        if expanded.exists():
            return str(expanded)
    return None


@contextlib.contextmanager
def _kfxlib_context() -> Iterator[None]:
    """Extract kfxlib from the Calibre KFX Input plugin zip and add it to sys.path.

    Raises RuntimeError if the plugin is not found.
    Cleans up the temp directory on exit.
    """
    plugin_zip = _find_kfx_plugin()
    if not plugin_zip:
        raise RuntimeError("Calibre KFX Input plugin not found")

    tmpdir = Path(tempfile.mkdtemp())
    try:
        with zipfile.ZipFile(plugin_zip) as z:
            for name in z.namelist():
                if name.startswith("kfxlib/") and not name.endswith("/"):
                    dest = tmpdir / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(z.read(name))

        kfxlib_dir = str(tmpdir)
        added = kfxlib_dir not in sys.path
        if added:
            sys.path.insert(0, kfxlib_dir)
        try:
            yield
        finally:
            if added:
                sys.path.remove(kfxlib_dir)
    finally:
        import shutil
        shutil.rmtree(str(tmpdir), ignore_errors=True)


def extract_book_text(ebook_path: Path) -> Optional[str]:
    """
    Convert an ebook (KFX, MOBI, AZW3, EPUB …) to plain text using Calibre
    and return the full Unicode string.  Returns None if Calibre is not found
    or conversion fails.
    """
    converter = _find_ebook_convert()
    if not converter:
        return None

    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        result = subprocess.run(
            [converter, str(ebook_path), str(tmp_path)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            print(f"  [warn] ebook-convert failed: {result.stderr[-200:]}", file=sys.stderr)
            return None
        return tmp_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        print(f"  [warn] ebook-convert error: {exc}", file=sys.stderr)
        return None
    finally:
        tmp_path.unlink(missing_ok=True)


def find_paired_ebook(sdr_path: Path) -> Optional[Path]:
    """
    Look for an ebook file next to a .sdr folder that shares the same stem.
    E.g.  "My Book.sdr/"  →  "My Book.kfx" / "My Book.azw3" / etc.
    """
    stem = sdr_path.stem          # strip .sdr
    parent = sdr_path.parent
    for ext in (".kfx", ".azw3", ".mobi", ".epub", ".azw", ".prc"):
        candidate = parent / (stem + ext)
        if candidate.exists():
            return candidate
    return None


def _find_sdr_parent(source_file: Path) -> Optional[Path]:
    """Walk up the directory tree to find the nearest .sdr ancestor."""
    for parent in source_file.parents:
        if parent.suffix.lower() == ".sdr":
            return parent
    return None


def fill_clipping_text(clippings: List[Clipping], book_text: str) -> None:
    """
    For YJR annotations that have location numbers but no content text,
    fill in the actual highlighted / bookmarked text from *book_text*.

    Kindle location numbers are Unicode character offsets into the book text.
    """
    for c in clippings:
        if c.content and not c.content.startswith("["):
            continue          # already has real text (e.g. notes)
        if c.location_start is None:
            continue

        loc_s = c.location_start
        loc_e = c.location_end if c.location_end else loc_s

        # Guard against out-of-range positions
        if loc_s >= len(book_text):
            continue

        # loc_end in YJR is inclusive (points to the last character of the highlight).
        # Add 1 to convert to Python's exclusive end index.
        snippet = book_text[loc_s:min(loc_e + 1, len(book_text))].strip()
        # Collapse internal newlines/whitespace runs to single spaces
        snippet = re.sub(r"\s+", " ", snippet)

        if snippet:
            # Preserve color tag that was already in content, if any
            color_tag = ""
            if c.content and c.content.startswith("["):
                m = re.match(r"(\[[^\]]+\])", c.content)
                if m:
                    color_tag = m.group(1) + " "
            c.content = color_tag + snippet


def extract_kfx_cover(kfx_path: Path) -> Optional[tuple[str, bytes]]:
    """Extract the embedded cover image from a KFX file.

    KFX 파일에는 이미 정품 표지(고해상도)가 들어있으므로 외부 검색보다
    정확하고 빠르며 오프라인이다.

    Returns (ext, raw_bytes) e.g. ("jpeg", b"\\xff\\xd8...") or None.
    Requires the Calibre "KFX Input" plugin.
    """
    if not _find_kfx_plugin():
        return None
    try:
        with _kfxlib_context():
            from kfxlib import yj_book  # type: ignore

            book = yj_book.YJ_Book(str(kfx_path))
            book.decode_book(set_metadata=None)
            if not book.has_cover_data():
                return None
            data = book.get_cover_image_data()   # (ext, bytes)
            if not data:
                return None
            ext, raw = data
            if not raw:
                return None
            return (str(ext).lower(), bytes(raw))
    except Exception as exc:
        print(f"  [warn] extract_kfx_cover failed ({kfx_path.name}): {exc}",
              file=sys.stderr)
        return None


def extract_kfx_metadata(kfx_path: Path) -> dict[str, str]:
    """Extract title and author from a KFX file. Falls back to filename on failure."""
    fallback: dict[str, str] = {"title": kfx_path.stem, "author": ""}
    if not _find_kfx_plugin():
        return fallback
    try:
        with _kfxlib_context():
            from kfxlib import yj_book  # type: ignore

            book = yj_book.YJ_Book(str(kfx_path))
            try:
                mi = book.get_metadata()
                title = (getattr(mi, "title", None) or "").strip() or kfx_path.stem
                authors = getattr(mi, "authors", None) or []
                author = ", ".join(a for a in authors if a and a.lower() != "unknown")
                return {"title": title, "author": author}
            except Exception:
                pass

            book.decode_book(set_metadata=None)
            title = kfx_path.stem
            author = ""
            try:
                from kfxlib.ion import unannotated  # type: ignore
                meta_frag = book.fragments.get("$490")
                if meta_frag is not None:
                    for item in (meta_frag.value or []):
                        try:
                            d = unannotated(item)
                            key_sym = str(d.get("$492", ""))
                            val = d.get("$307", "") or d.get("$171", "")
                            if key_sym == "$524" and val:
                                title = str(val).strip()
                            elif key_sym == "$522" and val:
                                author = str(val).strip()
                        except Exception:
                            continue
            except Exception:
                pass
            return {"title": title, "author": author}
    except Exception as exc:
        print(f"  [warn] extract_kfx_metadata failed ({kfx_path.name}): {exc}", file=sys.stderr)
        return fallback


def extract_kfx_info(
    kfx_path: Path,
) -> tuple[Optional[List[tuple]], Optional[List[int]], Optional[str], Optional[List[tuple]]]:
    """
    Extract page map, Kindle Location boundaries, book text, and table of
    contents from a KFX file in one pass.

    Returns:
        (page_map, kindle_loc_offsets, book_text, toc)
        page_map: sorted [(page_label, char_offset), …]  or None
        kindle_loc_offsets: sorted [char_offset_for_kl1, char_offset_for_kl2, …]
            Index i (0-based) holds the char offset where Kindle Location (i+1) starts.
            Use bisect_right(kindle_loc_offsets, char_offset) to get the KL number.
            None if extraction fails.
        book_text: full Unicode text of the book with KFX-internal char offsets preserved,
            so book_text[char_offset_start:char_offset_end] gives the exact highlighted text.
            None if extraction fails.
        toc: sorted [(char_offset, breadcrumb_title), …]  or None
            breadcrumb_title joins nested chapter titles with " › ".
            char_offset is in the same coordinate space as a clipping's raw
            location_start, so map BEFORE fill_clipping_kindle_locations.

    Requires the Calibre "KFX Input" plugin to be installed.
    """
    if not _find_kfx_plugin():
        return None, None, None, None

    try:
        with _kfxlib_context():
            from kfxlib import yj_book                               # type: ignore
            from kfxlib.ion import unannotated, ion_type, IonSymbol  # type: ignore

            book = yj_book.YJ_Book(str(kfx_path))
            book.decode_book(set_metadata=None)
            pos_info = book.collect_position_map_info()

            # --- Kindle Location boundaries ---
            loc_info = book.collect_location_map_info(pos_info)
            kindle_loc_offsets: Optional[List[int]] = (
                [entry.pid for entry in loc_info] if loc_info else None
            )

            # --- Navigation fragment: page map ($237) + table of contents ($212) ---
            page_map: List[tuple] = []
            toc: List[tuple] = []
            nav_fragment = book.fragments.get("$389")
            if nav_fragment is not None:
                for book_navigation in nav_fragment.value:
                    book_navigation = unannotated(book_navigation)
                    for nav_container in book_navigation.get("$392", []):
                        if ion_type(nav_container) is IonSymbol:
                            nav_container = book.fragments.get(ftype="$391", fid=nav_container)
                        if nav_container is None:
                            continue
                        nav_container = unannotated(nav_container)
                        nav_type = nav_container.get("$235", None)

                        if nav_type == "$237":  # page list
                            for entry in nav_container.get("$247", []):
                                ep = unannotated(entry)
                                label = ep.get("$241", {}).get("$244", "")
                                pos   = ep.get("$246", {})
                                eid   = pos.get("$155")
                                eid_offset = pos.get("$143", 0)
                                pid = book.pid_for_eid(eid, eid_offset, pos_info)
                                if pid is not None and label:
                                    page_map.append((label, pid))

                        elif nav_type == "$212":  # table of contents
                            _walk_toc(
                                nav_container.get("$247", []),
                                book, pos_info, unannotated, toc, parents=[],
                            )

                page_map.sort(key=lambda x: x[1])
                toc.sort(key=lambda x: x[0])

            # --- Book text with correct KFX char offsets ---
            # collect_content_position_info() returns ContentChunk objects where
            # chunk.pid is the absolute char offset and chunk.text is the actual text.
            # Building the text this way preserves the exact positions stored in YJR annotations.
            book_text: Optional[str] = None
            try:
                content_chunks = book.collect_content_position_info()
                chunks_with_text = sorted(
                    [c for c in content_chunks if c.text],
                    key=lambda c: c.pid,
                )
                if chunks_with_text:
                    parts: List[str] = []
                    pos = 0
                    for c in chunks_with_text:
                        if c.pid > pos:
                            parts.append(" " * (c.pid - pos))   # fill gap
                        parts.append(c.text)
                        pos = c.pid + c.length
                    book_text = "".join(parts)
            except Exception as exc:
                print(f"  [warn] KFX text extraction failed: {exc}", file=sys.stderr)

            return (
                page_map if page_map else None,
                kindle_loc_offsets,
                book_text,
                toc if toc else None,
            )

    except Exception as exc:
        print(f"  [warn] extract_kfx_info failed: {exc}", file=sys.stderr)
        return None, None, None, None


def _walk_toc(entries, book, pos_info, unannotated, out: List[tuple],
              parents: List[str]) -> None:
    """Recursively flatten a KFX TOC ($212) into [(char_offset, breadcrumb)].

    Nested chapter titles are joined with ' › ' so each clipping can show its
    full path. char_offset comes from pid_for_eid — same space as page map.
    """
    for entry in entries:
        ep = unannotated(entry)
        label = ep.get("$241", {})
        if isinstance(label, dict):
            label = label.get("$244", "")
        label = (label or "").strip()

        path = parents + [label] if label else parents

        pos = ep.get("$246", {})
        eid = pos.get("$155")
        if eid is not None and label:
            pid = book.pid_for_eid(eid, pos.get("$143", 0), pos_info)
            if pid is not None:
                out.append((pid, " › ".join(path)))

        children = ep.get("$247", [])
        if children:
            _walk_toc(children, book, pos_info, unannotated, out, parents=path)


# Keep backward-compatible alias
def extract_page_map(kfx_path: Path) -> Optional[List[tuple]]:
    page_map, *_ = extract_kfx_info(kfx_path)
    return page_map


def fill_clipping_pages(clippings: List[Clipping], page_map: List[tuple]) -> None:
    """
    Assign page numbers to clippings using the KFX page map.

    page_map: sorted list of (page_label, char_offset) from extract_page_map().
    For each clipping with a location_start, finds the largest page whose
    char_offset ≤ location_start.
    NOTE: location_start must still be a char_offset here (call this BEFORE
    fill_clipping_kindle_locations).
    """
    import bisect
    offsets = [offset for _, offset in page_map]
    labels  = [label  for label, _ in page_map]

    for c in clippings:
        if c.location_start is None or c.page is not None:
            continue
        idx = bisect.bisect_right(offsets, c.location_start) - 1
        if idx >= 0:
            label = labels[idx]
            try:
                c.page = int(label)
            except ValueError:
                pass   # non-numeric page labels (e.g. "ix") — skip


def fill_clipping_chapters(clippings: List[Clipping], toc: List[tuple]) -> None:
    """
    Assign chapter breadcrumbs to clippings using the KFX table of contents.

    toc: sorted list of (char_offset, breadcrumb_title) from extract_kfx_info().
    For each clipping with a location_start, finds the last TOC entry whose
    char_offset ≤ location_start.
    NOTE: location_start must still be a char_offset here (call this BEFORE
    fill_clipping_kindle_locations, like fill_clipping_pages).
    """
    import bisect
    if not toc:
        return
    offsets = [off for off, _ in toc]
    titles  = [title for _, title in toc]

    for c in clippings:
        if c.location_start is None or c.chapter is not None:
            continue
        idx = bisect.bisect_right(offsets, c.location_start) - 1
        if idx >= 0:
            c.chapter = titles[idx]


def fill_clipping_kindle_locations(
    clippings: List[Clipping], kindle_loc_offsets: List[int]
) -> None:
    """
    Convert location_start / location_end from raw KFX char offsets to
    Kindle Location numbers (the small integers shown in the Kindle reader UI).

    kindle_loc_offsets: sorted list where index i holds the char offset at
        which Kindle Location (i+1) begins.  Obtained from extract_kfx_info().

    IMPORTANT: call this AFTER fill_clipping_text() and fill_clipping_pages(),
    because those functions expect char offsets in location_start/end.
    After this call, location_start/end hold Kindle Location numbers.
    """
    import bisect
    for c in clippings:
        if c.location_start is not None:
            kl = bisect.bisect_right(kindle_loc_offsets, c.location_start)
            c.location_start = kl if kl > 0 else 1
        if c.location_end is not None:
            kl = bisect.bisect_right(kindle_loc_offsets, c.location_end)
            c.location_end = kl if kl > 0 else 1
            if c.location_end == c.location_start:
                c.location_end = None  # collapse identical start/end
