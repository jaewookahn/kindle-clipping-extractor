#!/usr/bin/env python3
"""notion_create_db.py — sync_kfx 가 기대하는 스키마로 Notion 데이터베이스 생성.

준비:
  1) https://www.notion.so/profile/integrations 에서 integration 생성 → NOTION_TOKEN
  2) 데이터베이스를 둘 **부모 페이지** 를 Notion 에 만들기 (예: 빈 페이지 "Kindle")
  3) 그 부모 페이지의 우상단 ⋯ → "+ Add connections" 로 integration 권한 부여
  4) 부모 페이지 URL 에서 32자 hex 페이지 ID 추출

사용:
  export NOTION_TOKEN=ntn_xxx
  python notion_create_db.py --parent <PARENT_PAGE_ID> [--title "Kindle Clippings"]

성공하면 DB ID 가 stdout 에 한 줄 출력되어 그대로 export 가능:

  export NOTION_DB=$(python notion_create_db.py --parent <PARENT_PAGE_ID> --quiet)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

NOTION_API     = "https://api.notion.com/v1/databases"
NOTION_VERSION = "2022-06-28"

# sync_kfx / notion_export 가 의존하는 5개 속성
SCHEMA = {
    "Title":            {"title":     {}},
    "Author":           {"rich_text": {}},
    "Highlights":       {"number":    {}},
    "Last Highlighted": {"date":      {}},
    "Last Synced":      {"date":      {}},
}


_ID_RE = re.compile(r"[0-9a-fA-F]{32}")


def _normalize_id(raw: str) -> Optional[str]:
    """URL/하이픈 포함/순수 32자 hex 어떤 입력이든 32자 hex 로 정규화."""
    raw = raw.strip()
    cleaned = raw.replace("-", "")
    m = _ID_RE.search(cleaned)
    return m.group(0) if m else None


def create_database(token: str, parent_page_id: str, title: str) -> dict:
    import requests
    payload = {
        "parent":     {"type": "page_id", "page_id": parent_page_id},
        "title":      [{"type": "text", "text": {"content": title}}],
        "properties": SCHEMA,
    }
    headers = {
        "Authorization":  f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type":   "application/json",
    }
    r = requests.post(NOTION_API, json=payload, headers=headers, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(
            f"Notion API {r.status_code}: {r.text}\n"
            f"(부모 페이지에 integration 권한이 추가됐는지, "
            f"PARENT_PAGE_ID 가 페이지가 맞는지 확인)"
        )
    return r.json()


def main() -> None:
    p = argparse.ArgumentParser(
        description="Notion 에 sync_kfx 용 데이터베이스 생성",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--parent", required=True, metavar="PAGE_ID_OR_URL",
                   help="DB 를 둘 부모 페이지 ID (URL 통째로 붙여넣어도 됨)")
    p.add_argument("--title", default="Kindle Clippings",
                   help='데이터베이스 제목 (기본 "Kindle Clippings")')
    p.add_argument("--token", default=None,
                   help="Notion 통합 토큰 (NOTION_TOKEN 환경변수도 인식)")
    p.add_argument("--quiet", action="store_true",
                   help="DB ID 만 출력 (export 명령에 그대로 쓸 수 있게)")
    args = p.parse_args()

    token = args.token or os.environ.get("NOTION_TOKEN")
    if not token:
        p.error("NOTION_TOKEN 환경변수 또는 --token 필요")

    parent_id = _normalize_id(args.parent)
    if not parent_id:
        p.error(f"PARENT_PAGE_ID 를 못 찾음: {args.parent!r}\n"
                "URL 이라면 32자 hex 부분이 들어 있어야 합니다.")

    if not args.quiet:
        print(f"부모 페이지: {parent_id}", file=sys.stderr)
        print(f"제목:       {args.title}", file=sys.stderr)
        print("Notion API 호출 …", file=sys.stderr)

    try:
        data = create_database(token, parent_id, args.title)
    except Exception as e:
        print(f"실패: {e}", file=sys.stderr)
        sys.exit(1)

    db_id = data["id"].replace("-", "")
    url   = data.get("url", "")

    if args.quiet:
        print(db_id)
    else:
        print(file=sys.stderr)
        print(f"✓ 생성 완료", file=sys.stderr)
        print(f"  DB ID: {db_id}", file=sys.stderr)
        if url:
            print(f"  URL:   {url}", file=sys.stderr)
        print(file=sys.stderr)
        print(f"  export NOTION_DB={db_id}")


if __name__ == "__main__":
    main()
