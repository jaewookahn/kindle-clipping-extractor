#!/usr/bin/env python3
"""
Kindle / Mobipocket clippings parser.

Supports:
  My Clippings.txt  — plain-text highlights/notes written by every Kindle device
  .apnx             — Amazon Page Number Index (page-map metadata, no text content)
  .mbp              — Mobipocket annotation binary (bookmarks, highlights, notes)
  .yjr              — Kindle sidecar annotation records (highlights, bookmarks, notes)
  .yjf              — Kindle sidecar fast-data (last reading position, timer stats)
  .sdr/             — Kindle sidecar directory (scanned recursively for yjr/yjf)

Export formats: json, csv, markdown, text

Usage examples:
  python parse_clippings.py "My Clippings.txt" -o clippings.json
  python parse_clippings.py book.mbp -o book_notes.md -f markdown
  python parse_clippings.py book.apnx -o pages.json
  python parse_clippings.py "book.sdr/" -o clippings.md -f markdown
  python parse_clippings.py clippings/ -o all.csv -f csv   # scan a directory
"""

from kindle.cli import main

if __name__ == "__main__":
    main()
