"""Calibre/KFX-based ebook text and page-map extraction."""

import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, List

from kindle.models import Clipping

# ---------------------------------------------------------------------------
# KFX text extraction (for resolving highlight locations to actual text)
# ---------------------------------------------------------------------------
#
# Kindle location numbers in YJR annotation files are Unicode character offsets
# into the full book text.  We use Calibre's ebook-convert to produce a plain
# text rendition, then slice [loc_start:loc_end] to get the highlighted text.

_CALIBRE_PATHS = [
    "/Applications/calibre.app/Contents/MacOS/ebook-convert",
    "/usr/bin/ebook-convert",
    "/usr/local/bin/ebook-convert",
]

_KFX_PLUGIN_PATHS = [
    "~/Library/Preferences/calibre/plugins/KFX Input.zip",
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

        snippet = book_text[loc_s:min(loc_e, len(book_text))].strip()
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


def extract_kfx_info(kfx_path: Path) -> tuple[Optional[List[tuple]], Optional[List[int]], Optional[str]]:
    """
    Extract page map, Kindle Location boundaries, and book text from a KFX file in one pass.

    Returns:
        (page_map, kindle_loc_offsets, book_text)
        page_map: sorted [(page_label, char_offset), …]  or None
        kindle_loc_offsets: sorted [char_offset_for_kl1, char_offset_for_kl2, …]
            Index i (0-based) holds the char offset where Kindle Location (i+1) starts.
            Use bisect_right(kindle_loc_offsets, char_offset) to get the KL number.
            None if extraction fails.
        book_text: full Unicode text of the book with KFX-internal char offsets preserved,
            so book_text[char_offset_start:char_offset_end] gives the exact highlighted text.
            None if extraction fails.

    Requires the Calibre "KFX Input" plugin to be installed.
    """
    plugin_zip = _find_kfx_plugin()
    if not plugin_zip:
        return None, None, None

    import zipfile

    tmpdir: Optional[Path] = None
    try:
        tmpdir = Path(tempfile.mkdtemp())
        with zipfile.ZipFile(plugin_zip) as z:
            for name in z.namelist():
                if name.startswith("kfxlib/") and not name.endswith("/"):
                    dest = tmpdir / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(z.read(name))

        kfxlib_dir = str(tmpdir)
        if kfxlib_dir not in sys.path:
            sys.path.insert(0, kfxlib_dir)

        from kfxlib import yj_book                              # type: ignore
        from kfxlib.ion import unannotated, ion_type, IonSymbol # type: ignore

        book = yj_book.YJ_Book(str(kfx_path))
        book.decode_book(set_metadata=None)
        pos_info = book.collect_position_map_info()

        # --- Kindle Location boundaries ---
        loc_info = book.collect_location_map_info(pos_info)
        kindle_loc_offsets: Optional[List[int]] = (
            [entry.pid for entry in loc_info] if loc_info else None
        )

        # --- Publisher page map ---
        page_map: List[tuple] = []
        nav_fragment = book.fragments.get("$389")
        if nav_fragment is not None:
            for book_navigation in nav_fragment.value:
                for nav_container in book_navigation.get("$392", []):
                    if ion_type(nav_container) is IonSymbol:
                        nav_container = book.fragments.get(ftype="$391", fid=nav_container)
                    if nav_container is None:
                        continue
                    nav_container = unannotated(nav_container)
                    if nav_container.get("$235", None) != "$237":  # $237 = page list
                        continue
                    for entry in nav_container.get("$247", []):
                        ep = unannotated(entry)
                        label = ep.get("$241", {}).get("$244", "")
                        pos   = ep.get("$246", {})
                        eid   = pos.get("$155")
                        eid_offset = pos.get("$143", 0)
                        pid = book.pid_for_eid(eid, eid_offset, pos_info)
                        if pid is not None and label:
                            page_map.append((label, pid))
            page_map.sort(key=lambda x: x[1])

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

        return page_map if page_map else None, kindle_loc_offsets, book_text

    except Exception as exc:
        print(f"  [warn] extract_kfx_info failed: {exc}", file=sys.stderr)
        return None, None, None
    finally:
        import shutil
        shutil.rmtree(str(tmpdir), ignore_errors=True)


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
