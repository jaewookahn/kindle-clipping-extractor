#!/usr/bin/env python3
"""notion_refresh_covers.py — Notion DB 의 모든 페이지 표지를 알라딘으로 새로 교체.

기존에 Google Books 실패로 플레이스홀더가 들어간 페이지가 있을 때 사용.
클리핑·페이지·속성은 건드리지 않고 cover 만 업데이트.

사용:
  export NOTION_TOKEN=ntn_xxx
  export NOTION_DB=<DB_ID>
  python notion_refresh_covers.py             # 전체 페이지
  python notion_refresh_covers.py --force     # 이미 cover 있어도 알라딘으로 덮어쓰기
  python notion_refresh_covers.py --dry-run   # 검색만, 업데이트 안 함
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional

import requests

from kindle.notion_export import _get_cover_url


NOTION_VERSION = "2022-06-28"
PLACEHOLDER_HOSTS = ("via.placeholder.com",)


def _headers(token: str) -> dict:
    return {
        "Authorization":  f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type":   "application/json",
    }


def list_pages(token: str, db_id: str) -> list[dict]:
    out, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            json=body, headers=_headers(token), timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        out.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return out


def _extract_text(rich: list) -> str:
    return "".join(t.get("plain_text", "") for t in rich or [])


def page_title_author(page: dict) -> tuple[str, str]:
    props = page.get("properties", {})
    title  = _extract_text(props.get("Title",  {}).get("title",     []))
    author = _extract_text(props.get("Author", {}).get("rich_text", []))
    return title, author


def page_cover_url(page: dict) -> Optional[str]:
    cover = page.get("cover")
    if not cover:
        return None
    return (cover.get("external") or cover.get("file") or {}).get("url")


def is_placeholder(url: Optional[str]) -> bool:
    if not url:
        return True
    return any(h in url for h in PLACEHOLDER_HOSTS)


def update_cover(token: str, page_id: str, url: str) -> None:
    r = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        json={"cover": {"type": "external", "external": {"url": url}}},
        headers=_headers(token), timeout=30,
    )
    r.raise_for_status()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--token", default=None)
    p.add_argument("--db",    default=None)
    p.add_argument("--force", action="store_true",
                   help="placeholder 가 아니어도 알라딘으로 덮어쓰기")
    p.add_argument("--dry-run", action="store_true",
                   help="검색·매칭만 출력, 실제 업데이트 안 함")
    p.add_argument("--sleep", type=float, default=0.3,
                   help="페이지 간 대기(초) — API rate-limit 회피용")
    args = p.parse_args()

    token = args.token or os.environ.get("NOTION_TOKEN")
    db_id = args.db    or os.environ.get("NOTION_DB")
    if not token or not db_id:
        p.error("NOTION_TOKEN, NOTION_DB 환경변수 또는 --token/--db 필요")

    print("페이지 목록 가져오는 중 …", flush=True)
    pages = list_pages(token, db_id)
    print(f"총 {len(pages)} 페이지\n", flush=True)

    stats = {"updated": 0, "skipped": 0, "no_match": 0, "kept": 0}

    for i, page in enumerate(pages, 1):
        title, author = page_title_author(page)
        cur = page_cover_url(page)
        ph  = is_placeholder(cur)

        if not args.force and not ph:
            print(f"[{i:>3}/{len(pages)}] {title[:35]:<35}  skip (이미 표지 있음)", flush=True)
            stats["kept"] += 1
            continue

        url = _get_cover_url(title, author)
        if not url:
            print(f"[{i:>3}/{len(pages)}] {title[:35]:<35}  ✗ 알라딘에서 못 찾음", flush=True)
            stats["no_match"] += 1
            continue

        if args.dry_run:
            print(f"[{i:>3}/{len(pages)}] {title[:35]:<35}  → {url}", flush=True)
            stats["updated"] += 1
            continue

        try:
            update_cover(token, page["id"], url)
            print(f"[{i:>3}/{len(pages)}] {title[:35]:<35}  ✓ 갱신", flush=True)
            stats["updated"] += 1
        except Exception as e:
            print(f"[{i:>3}/{len(pages)}] {title[:35]:<35}  ✗ API 실패: {e}", flush=True)
            stats["skipped"] += 1

        time.sleep(args.sleep)

    print(f"\n갱신 {stats['updated']} · 유지 {stats['kept']} · "
          f"미매칭 {stats['no_match']} · 실패 {stats['skipped']}",
          flush=True)


if __name__ == "__main__":
    main()
