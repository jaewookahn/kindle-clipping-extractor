#!/usr/bin/env python3
"""tui.py — Kindle 클리핑 동기화 TUI (Textual 기반).

사용법:
    python tui.py                         # 자동 감지
    python tui.py --kindle /path/Internal Storage   # 경로 직접 지정

키바인딩:
    j/k, ↑/↓   책 이동
    /          제목·저자 필터
    enter      선택한 책의 클리핑 미리보기 토글
    s          동기화 옵션 모달 (dry-run / file / Notion)
    r          제목 캐시 새로고침
    q          종료
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

from textual.app    import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen  import ModalScreen
from textual.widgets import (
    Button, Checkbox, DataTable, Footer, Header, Input, Label, LoadingIndicator,
    RichLog, Static,
)

from datetime import datetime

from kindle.device   import find_kindle, find_kindle_candidates
from kindle.title_cache import (
    DEFAULT_PATH as DEFAULT_TITLE_CACHE,
    load_cache, save_cache, get_or_extract,
)
from kindle.notion_export import (
    DEFAULT_STATE as NOTION_DEFAULT_STATE,
    load_state as load_notion_state,
    _get_cover_url,
)
from kindle.parsers.yjr import parse_yjr
from kindle.ebook import (
    extract_kfx_info,
    extract_kfx_cover,
    fill_clipping_text,
    fill_clipping_pages,
    fill_clipping_chapters,
    fill_clipping_kindle_locations,
)

import sync_kfx as sk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def _display_width(s: str) -> int:
    """문자열의 터미널 표시 너비(셀 수). CJK 전각=2, 그 외=1."""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
               for c in s if c != "\n")


# === Catppuccin Mocha 팔레트 ==============================================
class CAT:
    base       = "#303446"
    mantle     = "#292c3c"
    crust      = "#232634"
    surface0   = "#414559"
    surface1   = "#51576d"
    surface2   = "#626880"
    overlay0   = "#737994"
    overlay1   = "#838ba7"
    overlay2   = "#949cbb"
    subtext0   = "#a5adce"
    subtext1   = "#b5bfe2"
    text       = "#c6d0f5"
    lavender   = "#babbf1"
    blue       = "#8caaee"
    sapphire   = "#85c1dc"
    sky        = "#99d1db"
    teal       = "#81c8be"
    green      = "#a6d189"
    yellow     = "#e5c890"
    peach      = "#ef9f76"
    maroon     = "#ea999c"
    red        = "#e78284"
    mauve      = "#ca9ee6"
    pink       = "#f4b8e4"


# ---------------------------------------------------------------------------
# 상태 바 (헤더 아래)
# ---------------------------------------------------------------------------

class AppLogo(Static):
    """좌측에 미니멀 워드마크, 우측에 sub_title 안내."""

    sub = reactive[str]("")

    def render(self) -> str:
        logo = (
            f"[{CAT.mauve} b]◆[/] "
            f"[{CAT.lavender} b]Kindle[/] "
            f"[{CAT.subtext1} b]Clipping[/]"
        )
        hint = f"[{CAT.overlay1}]{self.sub}[/]" if self.sub else ""
        return f"{logo}    {hint}"


class StatusBar(Static):
    """Kindle 경로 + sync 현황. 2줄, 미니멀 스타일."""

    kindle_path  = reactive[str]("")
    total_books  = reactive[int](0)
    with_clips   = reactive[int](0)
    notion_books = reactive[int](0)
    last_sync    = reactive[str]("never")

    @staticmethod
    def _stat(label: str, value, color: str) -> str:
        # 라벨은 흐리게, 값은 컬러 — 군더더기 없는 인라인 통계
        return f"[{CAT.overlay1}]{label}[/] [{color} b]{value}[/]"

    def render(self) -> str:
        path = self.kindle_path or f"[{CAT.red}]미감지 — [b]k[/b] 키로 선택[/]"
        line1 = (
            f"[{CAT.lavender} b]DEVICE[/]   "
            f"[{CAT.subtext1}]{path}[/]"
        )
        line2 = "    ".join([
            self._stat("BOOKS",  self.total_books,  CAT.blue),
            self._stat("CLIPS",  self.with_clips,   CAT.green),
            self._stat("NOTION", self.notion_books, CAT.mauve),
            f"[{CAT.overlay0}]│[/]  "
            f"[{CAT.overlay1}]last sync[/] [{CAT.sapphire}]{self.last_sync}[/]",
        ])
        return f"{line1}\n{line2}"


# ---------------------------------------------------------------------------
# 클리핑 미리보기 모달
# ---------------------------------------------------------------------------

class ClippingPreview(ModalScreen):
    """선택한 책의 클리핑 목록 (YJR + KFX 텍스트 채워서)."""

    BINDINGS = [
        Binding("escape", "close_or_clear", "닫기"),
        Binding("q",      "dismiss",        "닫기", show=False),
        Binding("slash",  "focus_search",   "검색"),
        Binding("w",      "toggle_wrap",    "워드랩"),
        Binding("1",      "sort_clip('idx')",    "#",     show=False),
        Binding("2",      "sort_clip('type')",   "타입",  show=False),
        Binding("3",      "sort_clip('color')",  "색",    show=False),
        Binding("4",      "sort_clip('page')",   "페이지",show=False),
        Binding("5",      "sort_clip('loc')",    "위치",  show=False),
        Binding("6",      "sort_clip('date')",   "날짜",  show=False),
        Binding("7",      "sort_clip('chapter')","챕터",  show=False),
    ]

    SORT_KEYS = {
        "idx":   lambda c: 0,                                       # 원래 순서 유지
        "type":  lambda c: c.clip_type,
        "color": lambda c: (c.content or "").startswith("[") and (c.content or "")[:10] or "",
        "page":  lambda c: c.page if c.page else 0,
        "loc":   lambda c: c.location_start if c.location_start is not None else 0,
        "date":  lambda c: c.added_date or "",
        "chapter": lambda c: c.chapter or "",
    }

    CSS = """
    ClippingPreview { align: center middle; }
    #preview-box {
        width: 95%; height: 92%;
        border: round #626880;
        background: #303446;
        padding: 1 2;
    }
    #preview-header {
        dock: top;
        height: 3;
        background: #292c3c;
    }
    #preview-title {
        width: 1fr;
        height: 3;
        content-align: center middle;
        color: #f4b8e4;
        text-style: bold;
    }
    #toolbar {
        width: 18;
        height: 3;
        padding: 0 1;
        align: right middle;
    }
    Button#wrap-toggle {
        width: 14;
        height: 1;
        border: none;
        background: #51576d;
        color: #c6d0f5;
        margin-top: 1;
    }
    Button#wrap-toggle:hover { background: #626880; }
    Button#wrap-toggle.-wrap-on  { background: #a6d189; color: #303446; text-style: bold; }
    Button#wrap-toggle.-wrap-off { background: #51576d; color: #c6d0f5; }
    #preview-body {
        height: 1fr;
        margin-top: 1;
    }
    #cover-panel {
        width: 40;
        border: round #51576d;
        background: #292c3c;
        padding: 1;
        align: center top;
    }
    /* 표지 자리(플레이스홀더 Static·실제 Image 공통) — id 가 아니라 class 로
       크기를 줘서, 위젯 교체 시 id 충돌(DuplicateIds) 없이 같은 레이아웃 유지.
       height 를 1fr 로 두면 책 표지(2:3)가 세로로 늘어나므로 고정 높이 사용.
       tmux half-block 렌더는 셀 수 = 해상도라, 패널을 키워 선명도를 올린다. */
    #cover-panel > .cover { width: 100%; height: 28; }
    #cover-caption {
        dock: bottom;
        height: 1;
        content-align: center middle;
        color: #737994;
    }
    #preview-table {
        width: 1fr;
        margin-left: 1;
        border: round #51576d;
        background: #292c3c;
        scrollbar-background: #414559;
        scrollbar-color: #737994;
        scrollbar-color-hover: #ca9ee6;
        scrollbar-background-hover: #51576d;
    }
    #preview-table > .datatable--header {
        background: #414559;
        color: #ca9ee6;
        text-style: bold;
    }
    #preview-table > .datatable--cursor {
        background: #ca9ee6 30%;
    }
    #preview-table > .datatable--odd-row { background: #303446; }
    #preview-table > .datatable--even-row { background: #292c3c; }
    .clip-search {
        display: none;
        height: 3;
        margin: 1 0 0 0;
        border: round #51576d;
        background: #292c3c;
        color: #c6d0f5;
    }
    .clip-search:focus { border: round #ca9ee6; }
    """

    def __init__(self, book: dict) -> None:
        super().__init__()
        self.book = book
        self.clips: list = []          # 로드된 클리핑 캐시
        self.errors: list[str] = []
        self.wrap_on: bool = True
        self.sort_key: str = "date"
        self.sort_reverse: bool = False
        self.search_text: str = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="preview-box"):
            with Horizontal(id="preview-header"):
                yield Label(
                    f"[b]{self.book['title']}[/b]   {self.book['author']}    "
                    f"({self.book['yjr_count']} YJR / {self.book['stem']})",
                    id="preview-title",
                )
                with Horizontal(id="toolbar"):
                    yield Button("⏎ wrap: ON", id="wrap-toggle", classes="-wrap-on")
            yield Input(placeholder="🔍  검색 — 내용·챕터·색·타입 (Esc 로 해제)",
                        id="clip-search", classes="clip-search")
            with Horizontal(id="preview-body"):
                with Vertical(id="cover-panel"):
                    yield Static("[dim]표지 로드 중…[/dim]", id="cover-image", classes="cover")
                    yield Static("", id="cover-caption")
                yield DataTable(id="preview-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        table = self.query_one("#preview-table", DataTable)
        table.add_column("#",     width=4)
        table.add_column("",      width=4)    # 타입 아이콘
        table.add_column("색",    width=6)
        table.add_column("페이지", width=6)
        table.add_column("위치",  width=12)
        table.add_column("날짜",  width=16)
        table.add_column("챕터",  width=22)
        table.add_column("내용",  width=80)
        table.loading = True
        self.run_worker(self._load_clippings, thread=True, exclusive=True)
        self.run_worker(self._load_cover,     thread=True, exclusive=False)

    def _load_clippings(self) -> None:
        """파이프라인(parse_yjr → fill_text → fill_pages → fill_kl)을 thread에서 실행."""
        clips: list = []
        errors: list[str] = []

        sdr = self.book.get("sdr")
        if sdr is None:
            errors.append("SDR 폴더 없음 (책에 어노테이션이 한 번도 저장되지 않음)")
        else:
            yjr_files = list(sdr.glob("*.yjr"))
            if not yjr_files:
                errors.append("SDR 폴더에 YJR 파일 없음 (하이라이트·북마크 없음)")
            for yjr in yjr_files:
                try:
                    clips.extend(parse_yjr(yjr, book_title=self.book["title"]))
                except Exception as e:
                    errors.append(f"YJR 파싱 실패 ({yjr.name}): {e}")

        if clips:
            try:
                page_map, kl_offsets, book_text, toc = extract_kfx_info(self.book["kfx"])
                if book_text:
                    fill_clipping_text(clips, book_text)
                else:
                    errors.append("KFX 본문 추출 실패 (kfxlib 없음 또는 KFX 손상) "
                                  "→ 하이라이트 텍스트가 비어 보일 수 있음")
                if page_map:
                    fill_clipping_pages(clips, page_map)
                if toc:
                    fill_clipping_chapters(clips, toc)
                if kl_offsets:
                    fill_clipping_kindle_locations(clips, kl_offsets)
                else:
                    errors.append("KL 맵 없음 → 위치가 raw char offset")
            except Exception as e:
                errors.append(f"KFX 정보 추출 실패: {e}")

        # `_render` 같은 underscore-prefixed 이름은 Widget 내부 메서드와 겹쳐
        # Textual이 인자 없이 호출하는 경우가 있다. 안전한 이름 사용.
        self.app.call_from_thread(lambda: self._show_clips(clips, errors))

    # 표지 이미지 디스크 캐시 — 한 번 받은 파일을 재사용해 두 번째 열기부터 즉시 표시
    _COVER_DIR = Path.home() / ".cache" / "kindle_covers"

    @classmethod
    def _hi_res(cls, url: str) -> str:
        """알라딘 cover200(200px) → cover500(500px) 으로 승격해 선명도 향상."""
        return url.replace("/cover200/", "/cover500/") if "/cover200/" in url else url

    def _cached_download(self, url: str) -> Optional[Path]:
        """url 을 디스크 캐시에 받아 경로 반환. 캐시 hit 시 네트워크 생략."""
        import hashlib, requests
        self._COVER_DIR.mkdir(parents=True, exist_ok=True)
        ext = (Path(url).suffix or ".jpg").split("?")[0]
        dest = self._COVER_DIR / (hashlib.sha1(url.encode()).hexdigest() + ext)
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        dest.write_bytes(r.content)
        return dest

    def _embedded_cover_path(self, kfx: Path) -> Optional[Path]:
        """KFX 임베디드 표지를 디스크 캐시에 추출해 경로 반환 (mtime·size 키)."""
        import hashlib
        self._COVER_DIR.mkdir(parents=True, exist_ok=True)
        try:
            st = kfx.stat()
        except OSError:
            return None
        key = hashlib.sha1(
            f"{kfx.resolve()}|{st.st_mtime_ns}|{st.st_size}".encode()
        ).hexdigest()
        for ext in ("jpg", "jpeg", "png", "webp", "gif"):
            p = self._COVER_DIR / f"kfx_{key}.{ext}"
            if p.exists() and p.stat().st_size > 0:
                return p
        res = extract_kfx_cover(kfx)
        if not res:
            return None
        ext, raw = res
        ext = {"jpeg": "jpg"}.get(ext, ext) or "jpg"
        p = self._COVER_DIR / f"kfx_{key}.{ext}"
        p.write_bytes(raw)
        return p

    def _load_cover(self) -> None:
        """표지: ① KFX 임베디드(오프라인·정확·고해상도) 우선 → ② 외부 검색 폴백."""
        # ① KFX 파일에 내장된 정품 표지
        kfx = self.book.get("kfx")
        if kfx and Path(kfx).exists():
            try:
                p = self._embedded_cover_path(Path(kfx))
                if p:
                    self.app.call_from_thread(
                        lambda p=p: self._swap_in_image(p, "KFX 내장 표지")
                    )
                    return
            except Exception:
                pass
        # ② 외부 검색(알라딘 → Yes24 → Google Books)
        try:
            url = _get_cover_url(self.book["title"], self.book["author"])
        except Exception as e:
            self.app.call_from_thread(
                lambda e=e: self._show_cover_text(f"[red]검색 실패: {e}[/red]")
            )
            return
        if not url:
            self.app.call_from_thread(
                lambda: self._show_cover_text("[dim](표지를 찾지 못함)[/dim]")
            )
            return
        # 고해상도(cover500) 우선, 실패 시 원본(cover200)로 폴백
        path = None
        for candidate in (self._hi_res(url), url):
            try:
                path = self._cached_download(candidate)
                if path:
                    url = candidate
                    break
            except Exception:
                continue
        if path is None:
            self.app.call_from_thread(
                lambda: self._show_cover_text("[red]표지 다운로드 실패[/red]")
            )
            return
        self.app.call_from_thread(lambda p=path, u=url: self._swap_in_image(p, u))

    def _show_cover_text(self, text: str) -> None:
        try:
            self.query_one("#cover-image", Static).update(text)
        except Exception:
            pass

    @staticmethod
    def _kitty_capable() -> bool:
        """Kitty 그래픽 프로토콜(TGP)을 쓸 수 있는 터미널인가.

        tmux 안이라도 GHOSTTY_*/KITTY_*/WEZTERM_* 환경변수는 살아있다.
        TERM 은 tmux 안에서 'tmux-256color' 로 가려지므로 env 우선.
        """
        import os
        term = os.environ.get("TERM", "").lower()
        prog = os.environ.get("TERM_PROGRAM", "").lower()
        return (
            bool(os.environ.get("KITTY_WINDOW_ID"))
            or bool(os.environ.get("GHOSTTY_RESOURCES_DIR"))
            or bool(os.environ.get("GHOSTTY_BIN_DIR"))
            or bool(os.environ.get("WEZTERM_EXECUTABLE"))
            or "kitty" in term or "ghostty" in term
            or prog in ("ghostty", "wezterm", "kitty")
        )

    @staticmethod
    def _image_widget_cls():
        """터미널 환경에 맞는 textual-image 위젯 클래스 선택.

        Kitty 그래픽 지원 터미널(Ghostty/Kitty/WezTerm)이면 tmux 안이든 밖이든
        TGPImage 사용 — textual-image 가 tmux passthrough(\\033Ptmux;…) 봉투로
        그래픽 escape 를 감싸 멀티플렉서를 통과시킨다. 단 tmux 측
        `allow-passthrough on` 이 필요하며, 앱 시작 시 자동으로 켠다
        (KindleTUI._enable_tmux_passthrough).

        그래픽 불가 터미널이 tmux 안이면 컬러 문자 half-block 로 폴백.

        KINDLE_TUI_IMAGE=tgp|sixel|halfcell|unicode|auto 로 강제 지정 가능.
        """
        import os
        from textual_image.widget import (
            AutoImage, HalfcellImage, UnicodeImage, TGPImage, SixelImage,
        )
        override = os.environ.get("KINDLE_TUI_IMAGE", "").lower()
        forced = {
            "tgp": TGPImage, "kitty": TGPImage, "sixel": SixelImage,
            "halfcell": HalfcellImage, "half": HalfcellImage,
            "unicode": UnicodeImage, "auto": AutoImage,
        }.get(override)
        if forced:
            return forced
        if ClippingPreview._kitty_capable():
            return TGPImage   # tmux 안이면 passthrough 로 통과
        term = os.environ.get("TERM", "").lower()
        in_mux = bool(os.environ.get("TMUX")) or "tmux" in term or "screen" in term
        return HalfcellImage if in_mux else AutoImage

    def _swap_in_image(self, path: Path, url: str) -> None:
        """기존 #cover-image 위젯 제거 후 그 자리에 textual-image Image 마운트."""
        try:
            ImageCls = self._image_widget_cls()
        except Exception as e:
            self._show_cover_text(f"[red]textual-image 로드 실패: {e}[/red]")
            return
        try:
            panel   = self.query_one("#cover-panel", Vertical)
            caption = self.query_one("#cover-caption", Static)
            # 새 이미지를 먼저 마운트(class 로 크기 지정, id 없음 → 충돌 없음)한 뒤
            # 플레이스홀더를 제거. remove() 가 async 라 같은 id 재사용 시
            # DuplicateIds 로 mount 가 실패하던 버그를 피한다.
            img = ImageCls(str(path), classes="cover")
            panel.mount(img)
            for old in panel.query("#cover-image"):
                old.remove()
            caption.update(f"[dim]{_truncate(url, 24)}[/dim]")
        except Exception as e:
            self._show_cover_text(f"[red]이미지 위젯 실패: {e}[/red]")

    # YJR이 content 앞에 "[yellow] ..." 같은 prefix를 붙임. 분리해서 색 컬럼으로.
    _COLOR_RE = __import__("re").compile(r"^\[([a-zA-Z]+)\]\s*(.*)", flags=__import__("re").DOTALL)

    # 이모지는 마크업으로 색을 못 바꾸므로(터미널 고정 색) 색 제어가 되는
    # 텍스트 글리프 사용. 타입별로 색을 달리해 한눈에 구분.
    _TYPE_ICON = {
        "highlight":     f"[{CAT.yellow} b]✎[/]",   # 노란 펜
        "bookmark":      "🔖",                        # 원래 이모지 유지
        "note":          f"[{CAT.green} b]✐[/]",     # 초록 노트
        "last_position": f"[{CAT.mauve} b]➤[/]",     # 보라 위치
    }
    # 색 이름은 글자로 보여주되 배경색을 칠해서 식별
    _COLOR_BG = {
        "yellow": CAT.yellow,
        "blue":   CAT.blue,
        "pink":   CAT.pink,
        "orange": CAT.peach,
        "red":    CAT.red,
        "green":  CAT.green,
        "purple": CAT.mauve,
    }
    # 한글 2자 라벨 — 폭 절약 (영문 yellow/orange 등은 길어서 컬럼 넓어짐)
    _COLOR_KO = {
        "yellow": "노랑",
        "blue":   "파랑",
        "pink":   "분홍",
        "orange": "주황",
        "red":    "빨강",
        "green":  "초록",
        "purple": "보라",
    }

    @classmethod
    def _color_cell(cls, name: str) -> str:
        if not name:
            return f"[{CAT.overlay0}]-[/]"
        bg = cls._COLOR_BG.get(name, CAT.surface1)
        ko = cls._COLOR_KO.get(name, name[:2])   # 미등록 색은 영문 앞 2자
        return f"[{CAT.base} on {bg} b] {ko} [/]"

    @classmethod
    def _split_color(cls, content: str) -> tuple[str, str]:
        if not content:
            return "", ""
        m = cls._COLOR_RE.match(content)
        if m:
            return m.group(1).lower(), m.group(2)
        return "", content

    _CONTENT_COL_CELLS = 80

    @classmethod
    def _content_cell(cls, text: str, markup: bool = False):
        """Rich Text(overflow=fold) + 셀-너비 기준으로 행 높이 계산.
        반환: (Text, height)"""
        import math
        from rich.text import Text
        col = max(10, cls._CONTENT_COL_CELLS - 2)   # 컬럼 좌우 패딩 보정
        # 명시적 줄바꿈도 행으로 카운트
        lines = (text or "·").split("\n")
        total_h = 0
        for ln in lines:
            w = _display_width(ln)
            total_h += max(1, math.ceil(w / col))
        height = max(1, total_h)
        t = Text.from_markup(text) if markup else Text(text or "·")
        t.no_wrap = False
        t.overflow = "fold"
        return t, height

    def _show_clips(self, clips: list, errors: list = None) -> None:
        # 워커 → 메인 호출 진입점. 데이터 보관 후 redraw.
        self.clips  = clips
        self.errors = errors or []
        self._redraw_table()

    def _filtered_clips(self) -> list:
        """검색어로 클리핑 필터 — 내용·챕터·타입·페이지 대상 (대소문자 무시)."""
        ft = self.search_text.strip().lower()
        if not ft:
            return self.clips
        out = []
        for c in self.clips:
            hay = " ".join([
                c.content or "", c.chapter or "",
                c.clip_type or "", str(c.page or ""),
            ]).lower()
            if ft in hay:
                out.append(c)
        return out

    def _redraw_table(self) -> None:
        table = self.query_one("#preview-table", DataTable)
        table.loading = False
        table.clear()
        if not self.clips:
            if self.errors:
                for e in self.errors:
                    cell, h = self._content_cell(f"[red]{e}[/red]", markup=True)
                    table.add_row("!", "-", "-", "-", "-", "-", "-", cell, height=h)
            else:
                table.add_row("-", "-", "-", "-", "-", "-", "-", "(클리핑 없음)")
            return
        if self.errors:
            self.notify(" / ".join(self.errors), severity="warning", timeout=6)
        clips = self._filtered_clips()
        if not clips:
            table.add_row("-", "-", "-", "-", "-", "-", "-",
                          f"(검색 결과 없음: '{self.search_text}')")
            return
        key_fn = self.SORT_KEYS.get(self.sort_key, self.SORT_KEYS["date"])
        clips_sorted = sorted(clips, key=key_fn, reverse=self.sort_reverse)
        for i, c in enumerate(clips_sorted, 1):
            loc = f"L{c.location_start}" if c.location_start is not None else "-"
            if c.location_end and c.location_end != c.location_start:
                loc += f"-{c.location_end}"
            page = str(c.page) if c.page else "-"
            date = (c.added_date or "-")[:16]   # "YYYY-MM-DD HH:MM"

            color, raw = self._split_color(c.content or "")
            color_cell = self._color_cell(color)

            # 북마크는 위치 1글자 텍스트가 의미 없음 → 비움
            if c.clip_type == "bookmark":
                shown = "—"
            else:
                shown = (raw or "").strip() or "·"

            if self.wrap_on:
                content_cell, height = self._content_cell(shown)
            else:
                # 워드랩 off: 한 줄로 표시
                from rich.text import Text
                content_cell = Text(shown.replace("\n", " "), no_wrap=True, overflow="ellipsis")
                height = 1

            type_cell = self._TYPE_ICON.get(c.clip_type, c.clip_type)

            from rich.text import Text
            chapter_cell = Text(
                c.chapter or "-",
                no_wrap=True, overflow="ellipsis",
                style=CAT.subtext0 if c.chapter else CAT.overlay0,
            )
            table.add_row(
                str(i),
                type_cell,
                color_cell,
                page,
                loc,
                date,
                chapter_cell,
                content_cell,
                height=height,
            )

    # -- actions ----------------------------------------------------------

    def action_focus_search(self) -> None:
        inp = self.query_one("#clip-search", Input)
        inp.display = True
        inp.focus()

    def action_close_or_clear(self) -> None:
        """Esc: 검색 중이면 검색 해제, 아니면 모달 닫기."""
        inp = self.query_one("#clip-search", Input)
        if inp.display and (inp.value or inp.has_focus):
            inp.value = ""
            inp.display = False
            self.search_text = ""
            self._redraw_table()
            self.query_one("#preview-table", DataTable).focus()
            return
        self.dismiss()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "clip-search":
            self.search_text = event.value
            self._redraw_table()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "clip-search":
            self.query_one("#preview-table", DataTable).focus()

    def action_toggle_wrap(self) -> None:
        self.wrap_on = not self.wrap_on
        self._redraw_table()
        btn = self.query_one("#wrap-toggle", Button)
        if self.wrap_on:
            btn.label = "⏎ wrap: ON"
            btn.set_classes("-wrap-on")
        else:
            btn.label = "→ wrap: OFF"
            btn.set_classes("-wrap-off")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "wrap-toggle":
            event.stop()
            self.action_toggle_wrap()

    def action_sort_clip(self, key: str) -> None:
        if self.sort_key == key:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_key = key
            self.sort_reverse = False
        self._redraw_table()

    def on_data_table_header_selected(self, event) -> None:
        if event.data_table.id != "preview-table":
            return
        idx_to_key = {0: "idx", 1: "type", 2: "color",
                      3: "page", 4: "loc", 5: "date", 6: "chapter"}
        key = idx_to_key.get(event.column_index)
        if key:
            self.action_sort_clip(key)


# ---------------------------------------------------------------------------
# Kindle picker 모달
# ---------------------------------------------------------------------------

class KindlePicker(ModalScreen):
    """여러 Kindle 후보(MacDroid stale + 새 기기 등) 중 선택."""

    BINDINGS = [
        Binding("escape", "dismiss", "취소", show=False),
    ]

    CSS = """
    KindlePicker { align: center middle; }
    #picker-box {
        width: 90%; height: 60%;
        border: round #626880;
        background: #303446;
        padding: 1 2;
    }
    #picker-title {
        dock: top;
        height: 2;
        content-align: center middle;
        background: #292c3c;
        color: #99d1db;
        text-style: bold;
        padding: 0 1;
    }
    #picker-table {
        height: 1fr;
        margin-top: 1;
        border: round #51576d;
        background: #292c3c;
    }
    #picker-table > .datatable--header {
        background: #414559;
        color: #99d1db;
        text-style: bold;
    }
    #picker-table > .datatable--cursor {
        background: #99d1db 30%;
    }
    """

    def __init__(self, candidates: list[dict]) -> None:
        super().__init__()
        self.candidates = candidates

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-box"):
            yield Label(
                f"[b]Kindle 후보 {len(self.candidates)}개[/b] — Enter로 선택, Esc로 취소",
                id="picker-title",
            )
            yield DataTable(id="picker-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        table = self.query_one("#picker-table", DataTable)
        table.add_columns("#", "라벨", "Source", "최근 활동", "파일", "경로")
        for i, c in enumerate(self.candidates, 1):
            mt = (datetime.fromtimestamp(c["latest_mtime"]).strftime("%Y-%m-%d %H:%M")
                  if c["latest_mtime"] else "—")
            table.add_row(
                str(i),
                c["label"],
                c["source"],
                mt,
                str(c["file_count"]),
                _truncate(str(c["path"]), 60),
                key=str(i),
            )
        table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # Enter 가 dismiss 후 부모 화면의 books DataTable RowSelected 까지 흘러
        # 첫 항목이 자동 선택되는 현상 차단
        event.stop()
        idx = int(event.row_key.value) - 1
        self.dismiss(self.candidates[idx]["path"])


# ---------------------------------------------------------------------------
# 동기화 옵션 모달
# ---------------------------------------------------------------------------

class SyncOptions(ModalScreen):
    """sync_kfx 호출 옵션 선택 → subprocess로 실행, 출력 스트리밍."""

    BINDINGS = [
        Binding("escape", "try_close", "닫기"),
    ]

    running = reactive(False)

    CSS = """
    SyncOptions { align: center middle; }
    #sync-box {
        width: 88%; height: 88%;
        max-width: 120;
        border: round #626880;
        background: #303446;
        padding: 1 3;
    }
    #sync-heading {
        dock: top;
        height: 1;
        color: #ca9ee6;
        text-style: bold;
        margin-bottom: 1;
    }
    #sync-form {
        dock: top;
        height: auto;
        max-height: 60%;
        overflow-y: auto;
        padding: 0 1;
    }
    #scope-label {
        color: #b5bfe2;
        margin-bottom: 1;
        padding: 0 1;
        background: #292c3c;
    }
    /* 섹션 헤더 — 옵션을 목적별로 묶어 한눈에 구분 */
    #sync-form .section {
        color: #99d1db;
        text-style: bold;
        margin-top: 1;
    }
    #sync-form .section-danger {
        color: #e78284;
        text-style: bold;
        margin-top: 1;
    }
    #sync-form .hint {
        color: #737994;
        margin-left: 3;
        margin-bottom: 1;
    }
    /* 체크박스 1줄로 압축 — 기본 border 가 3줄을 먹어 옵션이 잘리던 문제 해결.
       내부 .toggle--* 컴포넌트는 건드리지 않음(렌더 깨짐 방지). */
    #sync-form Checkbox {
        height: 1;
        border: none;
        background: transparent;
        padding: 0;
        margin: 0 0 0 2;
    }
    #file-row { height: 1; margin: 0 0 0 5; }
    #file-row > Label { width: 6; color: #a5adce; }
    #file-row Input {
        width: 1fr;
        height: 1;
        background: #292c3c;
        color: #c6d0f5;
        border: none;
        padding: 0 1;
    }
    #file-row Input:focus { background: #3a3f52; }
    #sync-log {
        height: 1fr;
        margin-top: 1;
        border: round #414559;
        padding: 0 1;
        background: #232634;
        color: #c6d0f5;
        scrollbar-background: #414559;
        scrollbar-color: #737994;
        scrollbar-color-hover: #ca9ee6;
        scrollbar-background-hover: #51576d;
    }
    #sync-buttons {
        dock: bottom;
        height: 3;
        align: center middle;
        padding-top: 1;
    }
    /* 실행 중에만 보이는 스피너 + 상태 텍스트 */
    #sync-spin {
        display: none;
        width: 6;
        height: 1;
        color: #ca9ee6;
    }
    #sync-status {
        display: none;
        width: auto;
        height: 1;
        margin: 0 2 0 0;
        color: #99d1db;
        content-align: left middle;
    }
    #sync-buttons.-running #sync-spin   { display: block; }
    #sync-buttons.-running #sync-status { display: block; }
    #sync-buttons Button {
        margin: 0 1;
        min-width: 14;
        background: #414559;
        color: #c6d0f5;
        border: none;
    }
    #sync-buttons Button:hover { background: #51576d; }
    #sync-buttons Button#run {
        background: #a6d189;
        color: #303446;
        text-style: bold;
    }
    #sync-buttons Button#run:hover { background: #81c8be; }
    #sync-buttons Button#run:disabled {
        background: #414559;
        color: #737994;
        text-style: none;
    }
    #sync-buttons Button#close.-cancel {
        background: #e78284;
        color: #303446;
        text-style: bold;
    }
    """

    def __init__(
        self,
        kindle_root: Path,
        scope_books: list[dict],
        scope_label: str,
    ) -> None:
        super().__init__()
        self.kindle_root = kindle_root
        self.scope_books = scope_books
        self.scope_label = scope_label
        self._proc = None

    def compose(self) -> ComposeResult:
        has_env = bool(os.environ.get("NOTION_TOKEN") and os.environ.get("NOTION_DB"))
        with Vertical(id="sync-box"):
            yield Label("동기화", id="sync-heading")
            with Vertical(id="sync-form"):
                yield Label(
                    f"대상  [b]{self.scope_label}[/b]   ·   {len(self.scope_books)}권",
                    id="scope-label",
                )

                yield Label("출력 대상", classes="section")
                yield Checkbox("파일로 저장", id="opt-file", value=True)
                with Horizontal(id="file-row"):
                    yield Label("경로")
                    yield Input(
                        value=str(Path("kindle_sync.json").resolve()),
                        placeholder=".json / .csv / .md / .txt — 확장자로 형식 결정",
                        id="opt-file-path",
                    )
                yield Checkbox("Notion 데이터베이스에 업로드", id="opt-notion", value=has_env)
                if not has_env:
                    yield Label("NOTION_TOKEN·NOTION_DB 환경변수 필요", classes="hint")

                yield Label("실행 모드", classes="section")
                yield Checkbox("미리보기만 — 아무것도 저장 안 함 (dry-run)", id="opt-dry")
                yield Checkbox("챕터 정보 다시 쓰기 — 기존 페이지 본문 재작성", id="opt-rewrite")

                yield Label("상태 초기화  ⚠ 주의", classes="section-danger")
                yield Checkbox("로컬 동기화 상태 비우기 (--reset)", id="opt-reset")
                yield Checkbox("Notion 동기화 상태 비우기 (--reset-notion)", id="opt-reset-notion")

            yield RichLog(id="sync-log", highlight=True, markup=True, wrap=False)
            with Horizontal(id="sync-buttons"):
                yield LoadingIndicator(id="sync-spin")
                yield Static("", id="sync-status")
                yield Button("▶  실행", id="run")
                yield Button("닫기", id="close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run":
            if not self.running:
                self._start_run()
            return
        if event.button.id == "close":
            if self.running:
                self._cancel()      # 실행 중 → 중단
            else:
                self.dismiss()

    # -- 실행 상태 관리 ---------------------------------------------------
    def watch_running(self, running: bool) -> None:
        """running 토글 시 버튼 외형/동작 전환 (실행 그레이아웃 + 중단 노출)."""
        try:
            run_btn   = self.query_one("#run",   Button)
            close_btn = self.query_one("#close", Button)
        except Exception:
            return
        run_btn.disabled = running
        if running:
            close_btn.label = "■  중단"
            close_btn.add_class("-cancel")
        else:
            close_btn.label = "닫기"
            close_btn.remove_class("-cancel")
        # 스피너 + "동기화 중…" 표시 토글 (클리핑 뷰 로딩과 동일한 느낌)
        try:
            row = self.query_one("#sync-buttons")
            row.set_class(running, "-running")
            self.query_one("#sync-status", Static).update(
                "[b]동기화 중…[/b]" if running else ""
            )
        except Exception:
            pass

    def action_try_close(self) -> None:
        if self.running:
            self.notify("실행 중입니다 — '중단' 버튼으로 멈춘 뒤 닫으세요.",
                        severity="warning", timeout=4)
            return
        self.dismiss()

    def _cancel(self) -> None:
        log = self.query_one("#sync-log", RichLog)
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                log.write("[yellow]⊘ 중단 요청 — 프로세스 종료 중…[/yellow]")
            except Exception as e:
                log.write(f"[red]중단 실패: {e}[/red]")
        # 종료는 _pump_output 가 감지해 running=False 로 되돌린다

    def on_unmount(self) -> None:
        # 모달이 어떤 경로로든 닫히면 orphan subprocess 방지
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def _start_run(self) -> None:
        import subprocess
        log = self.query_one("#sync-log", RichLog)
        log.clear()

        dry          = self.query_one("#opt-dry",          Checkbox).value
        file_o       = self.query_one("#opt-file",         Checkbox).value
        notion       = self.query_one("#opt-notion",       Checkbox).value
        reset        = self.query_one("#opt-reset",        Checkbox).value
        reset_notion = self.query_one("#opt-reset-notion", Checkbox).value
        rewrite      = self.query_one("#opt-rewrite",      Checkbox).value

        if not (dry or file_o or notion):
            log.write("[red]실행할 동작이 없습니다. dry-run / 파일 / Notion 중 하나 선택[/red]")
            return

        # 실행 모드 요약 (사용자가 옵션 잘못 선택한 케이스 즉시 인지)
        active = []
        if dry:          active.append("[yellow]dry-run[/]")
        if file_o:       active.append("[green]파일저장[/]")
        if notion:       active.append("[magenta]Notion업로드[/]")
        if reset:        active.append("[cyan]로컬reset[/]")
        if reset_notion: active.append("[cyan]Notion-reset[/]")
        if rewrite:      active.append("[magenta]챕터백필[/]")
        log.write(f"[b]모드:[/] {' · '.join(active)}")

        if rewrite and not notion:
            log.write("[yellow]챕터 백필은 Notion 업로드와 함께 써야 효과가 있습니다.[/yellow]")

        cmd = [
            sys.executable, "sync_kfx.py",
            "--kindle", str(self.kindle_root),
            "--no-progress",   # tqdm 비활성, 책당 1줄 print
        ]
        if dry:
            cmd.append("--dry-run")
        if file_o:
            path_str = self.query_one("#opt-file-path", Input).value.strip()
            if not path_str:
                log.write("[red]파일 경로가 비어 있습니다.[/red]")
                return
            out = Path(path_str).expanduser().resolve()
            try:
                out.parent.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                log.write(f"[red]상위 디렉터리 생성 실패: {e}[/red]")
                return
            cmd += ["-o", str(out)]
            log.write(f"[dim]→ 파일 출력: {out}[/dim]")
        if notion:
            tok = os.environ.get("NOTION_TOKEN", "")
            db  = os.environ.get("NOTION_DB", "")
            if not tok or not db:
                log.write("[red]NOTION_TOKEN / NOTION_DB 환경변수가 필요합니다.[/red]")
                return
            cmd += ["--notion-token", tok, "--notion-db", db]
        if reset:
            cmd.append("--reset")
        if reset_notion:
            cmd.append("--reset-notion")
        if rewrite:
            cmd.append("--rewrite-bodies")

        # scope: 필터·선택된 책이 부분집합이면 --book 추가
        stems = [b["stem"] for b in self.scope_books]
        if 0 < len(stems) < len(self._all_book_stems()):
            for s in stems:
                cmd += ["--book", s]

        log.write(f"[b]$ {' '.join(self._mask(cmd))}[/b]")
        try:
            # PYTHONUNBUFFERED=1 — sync_kfx 의 print() 가 라인 단위로 즉시
            # PIPE 로 흘러나오게 한다. 안 주면 child stdio 가 fully-buffered 라
            # 책 처리 끝나야 한꺼번에 쏟아져 진행 상황 안 보임.
            env = {**os.environ, "PYTHONUNBUFFERED": "1"}
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=env,
            )
        except Exception as e:
            log.write(f"[red]subprocess 실행 실패: {e}[/red]")
            return
        self.running = True
        self.run_worker(self._pump_output, thread=True, exclusive=True)

    def _all_book_stems(self) -> list[str]:
        # KindleTUI에 모든 책 목록이 있으므로 거기서 가져옴
        app = self.app
        if hasattr(app, "books"):
            return [b["stem"] for b in app.books]
        return []

    @staticmethod
    def _mask(cmd: list[str]) -> list[str]:
        out = []
        skip = False
        for i, a in enumerate(cmd):
            if skip:
                out.append("***")
                skip = False
                continue
            if a == "--notion-token":
                skip = True
            out.append(a)
        return out

    _ANSI = __import__("re").compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

    _PROG_RE = __import__("re").compile(r"^\[\s*\d+\s*/\s*\d+\]")

    def _pump_output(self) -> None:
        from rich.text import Text
        log = self.query_one("#sync-log", RichLog)
        assert self._proc is not None
        prev_blank = True   # 선두 빈 줄 억제
        for line in self._proc.stdout:
            # tqdm이 새어 나오는 경우 대비: ANSI 제거 + CR 뒤 마지막 segment만
            text = self._ANSI.sub("", line)
            if "\r" in text:
                text = text.rsplit("\r", 1)[-1]
            text = text.rstrip()
            if not text:
                # 빈 줄을 살려 책 사이를 구분하되, 연속 빈 줄은 하나로 합침
                if not prev_blank:
                    self.app.call_from_thread(lambda: log.write(Text("")))
                    prev_blank = True
                continue
            prev_blank = False
            # 책 진행 줄·결과 줄을 색으로 강조 → 이전 엔트리와 또렷이 구분
            s = text.lstrip()
            style = None
            if self._PROG_RE.match(text):
                style = f"{CAT.sky} bold"
            elif s.startswith(("→", "↻")):
                style = CAT.green
            elif s.startswith("✓"):
                style = CAT.teal
            elif s.startswith(("×", "✗", "오류", "[경고]", "[warn]", "Traceback")):
                style = CAT.red
            self.app.call_from_thread(
                lambda t=text, st=style: log.write(Text(t, style=st) if st else Text(t))
            )
        rc = self._proc.wait()
        if rc == 0:
            msg = "[green b]✓ 완료[/green b]"
        elif rc in (-15, -2, 143, 130):     # SIGTERM/SIGINT — 사용자 중단
            msg = "[yellow b]⊘ 중단됨[/yellow b]"
        else:
            msg = f"[red b]✗ 실패 (exit {rc})[/red b]"
        self.app.call_from_thread(lambda m=msg: log.write(m))
        self.app.call_from_thread(lambda: setattr(self, "running", False))


# ---------------------------------------------------------------------------
# 메인 앱
# ---------------------------------------------------------------------------

class KindleTUI(App):
    # Catppuccin Frappé — 밝은 다크
    CSS = """
    /* === 전역 ================================================ */
    Screen { background: #303446; color: #c6d0f5; }

    Footer {
        background: #232634;
        color: #c6d0f5;
        border-top: heavy #51576d;
    }
    Footer > .footer--key {
        background: #ca9ee6;
        color: #303446;
        text-style: bold;
    }
    Footer > .footer--description {
        color: #a5adce;
    }
    Toast { border: thick #ca9ee6; background: #414559; color: #c6d0f5; }
    Toast.-warning { border: thick #e5c890; }
    Toast.-error   { border: thick #e78284; }

    /* === Logo Row ============================================ */
    AppLogo {
        height: 2;
        padding: 1 3 0 3;
        background: #303446;
        color: #c6d0f5;
    }

    /* === Status Bar ========================================== */
    StatusBar {
        height: 5;
        padding: 1 3;
        background: #303446;
        border-bottom: solid #626880;
    }

    /* === Filter Input ======================================== */
    Input.filter {
        height: 3;
        margin: 1 3 0 3;
        border: round #51576d;
        background: #292c3c;
        color: #c6d0f5;
    }
    Input.filter:focus {
        border: round #ca9ee6;
        background: #292c3c;
    }

    /* === Books Table ========================================= */
    #books {
        height: 1fr;
        margin: 1 3 1 3;
        border: round #737994;
        background: #303446;
        scrollbar-background: #414559;
        scrollbar-color: #737994;
        scrollbar-color-hover: #ca9ee6;
        scrollbar-background-hover: #51576d;
        scrollbar-color-hover: #f4b8e4;
        scrollbar-corner-color: #232634;
        scrollbar-size: 1 2;
    }
    #books > .datatable--header {
        background: #414559;
        color: #ca9ee6;
        text-style: bold;
    }
    #books > .datatable--cursor {
        background: #ca9ee6 40%;
        color: #c6d0f5;
        text-style: bold;
    }
    #books > .datatable--hover { background: #414559; }
    #books > .datatable--odd-row { background: #303446; }
    #books > .datatable--even-row { background: #292c3c; }
    """

    BINDINGS = [
        Binding("q",      "quit",            "종료"),
        Binding("slash",  "focus_filter",    "필터"),
        Binding("p",      "preview",         "클리핑 보기"),
        Binding("r",      "refresh_titles",  "제목 새로고침"),
        Binding("s",      "sync",            "동기화"),
        Binding("k",      "pick_kindle",     "기기 변경"),
        Binding("1",      "sort('title')",    "제목 정렬", show=False),
        Binding("2",      "sort('author')",   "저자 정렬", show=False),
        Binding("3",      "sort('format')",   "포맷 정렬", show=False),
        Binding("4",      "sort('yjr')",      "YJR 정렬", show=False),
        Binding("5",      "sort('notion')",   "Notion 정렬", show=False),
        Binding("6",      "sort('modified')", "최종수정 정렬", show=False),
        Binding("0",      "sort('stem')",     "원래 순서", show=False),
        Binding("escape", "clear_filter",    "필터 해제", show=False),
    ]

    SORT_KEYS = {
        "stem":     lambda b: b["stem"].lower(),
        "title":    lambda b: b["title"].lower(),
        "author":   lambda b: b["author"].lower(),
        "format":   lambda b: b["kfx"].suffix,
        "yjr":      lambda b: -b["yjr_count"],            # 많은 순
        "notion":   lambda b: -b["notion_count"],         # 많은 순
        "modified": lambda b: -b.get("last_mtime", 0.0),  # 최근 순
    }

    kindle_root: Optional[Path] = None

    def __init__(self, kindle_root: Optional[Path] = None) -> None:
        super().__init__()
        self.kindle_root = kindle_root
        self.title_cache: dict = load_cache(DEFAULT_TITLE_CACHE)
        self.books: list[dict] = []      # list_kfx_books 출력 + title/author
        self.filter_text: str = ""
        self.sort_key: str = "modified"   # 기본: 최근 수정순
        self.sort_reverse: bool = False

    # -- composition --------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield AppLogo(id="logo")
        yield StatusBar(id="status")
        yield Input(placeholder="🔍  필터 — 제목·저자·파일명", classes="filter", id="filter")
        yield DataTable(id="books", cursor_type="row", zebra_stripes=True)
        yield Footer()

    # -- lifecycle ----------------------------------------------------------

    @staticmethod
    def _enable_tmux_passthrough() -> None:
        """tmux 안 + Kitty 그래픽 터미널이면 allow-passthrough 를 켠다.

        textual-image 가 그래픽 escape 를 tmux 봉투로 감싸도, tmux 측에서
        passthrough 가 꺼져 있으면 통째로 버려진다. 현재 패널에 한해(-p) 켜
        TGP 표지가 tmux 안에서도 선명하게 뜨도록 한다. 실패해도 무시(폴백).
        """
        import os, subprocess
        if not os.environ.get("TMUX") or not ClippingPreview._kitty_capable():
            return
        try:
            subprocess.run(
                ["tmux", "set", "-p", "allow-passthrough", "on"],
                check=False, capture_output=True, timeout=2,
            )
        except Exception:
            pass

    def on_mount(self) -> None:
        self.title    = "kindle-clipping-tui"
        self._enable_tmux_passthrough()
        self.query_one(AppLogo).sub = (
            "↑↓ 이동   ⏎ 미리보기   / 필터   s 동기화   k 기기   1-6 정렬   q 종료"
        )
        self.query_one("#filter", Input).display = False

        table = self.query_one("#books", DataTable)
        table.add_columns("#", "제목", "저자", "포맷", "YJR", "Notion", "최종수정")
        table.focus()

        # 1) Kindle 감지
        bar = self.query_one(StatusBar)
        if self.kindle_root is None:
            cands = find_kindle_candidates()
            if not cands:
                bar.kindle_path = "감지 실패 — --kindle 옵션으로 경로 지정 필요"
                return
            if len(cands) == 1:
                self.kindle_root = cands[0]["path"]
            else:
                # 여러 후보 → picker 띄움
                self._pick_then_load(cands)
                return
        bar.kindle_path = str(self.kindle_root)
        self.reload_books()

    def _pick_then_load(self, cands: list[dict]) -> None:
        def _on_pick(result):
            if result is None:
                return
            self.kindle_root = result
            self.query_one(StatusBar).kindle_path = str(result)
            self.reload_books()
        self.push_screen(KindlePicker(cands), _on_pick)

    def action_pick_kindle(self) -> None:
        cands = find_kindle_candidates()
        if not cands:
            self.notify("Kindle 후보를 찾지 못했습니다.", severity="error")
            return
        self._pick_then_load(cands)

    # -- data load ----------------------------------------------------------

    def reload_books(self) -> None:
        """documents/ 스캔 + 제목 캐시 + Notion 상태 합쳐 self.books 채움."""
        if self.kindle_root is None:
            return
        documents = self.kindle_root / "documents"
        raw = sk.list_kfx_books(documents)

        # 제목 캐시 hit만 (느린 추출은 'r' 키로 명시적으로)
        for b in raw:
            hit = self.title_cache.get("books", {}).get(str(b["kfx"].resolve()))
            if hit:
                b["title"]  = hit["title"]
                b["author"] = hit["author"]
            else:
                b["title"]  = b["stem"]
                b["author"] = ""

        # Notion 동기화 현황
        notion_state = load_notion_state(NOTION_DEFAULT_STATE)
        synced_books = set(notion_state.get("books", {}).keys())
        for b in raw:
            b["notion_count"] = len(
                notion_state.get("books", {}).get(b["title"], {})
                                                .get("synced_fingerprints", [])
            )

        self.books = raw
        self.populate_table()
        self.refresh_status()

    def refresh_status(self) -> None:
        bar = self.query_one(StatusBar)
        bar.total_books = len(self.books)
        bar.with_clips  = sum(1 for b in self.books if b["yjr_count"] > 0)
        bar.notion_books = sum(1 for b in self.books if b["notion_count"] > 0)
        ns = load_notion_state(NOTION_DEFAULT_STATE)
        if ns.get("last_sync"):
            bar.last_sync = ns["last_sync"]

    def populate_table(self) -> None:
        table = self.query_one("#books", DataTable)
        table.clear()
        rows = self._filtered_books()
        key_fn = self.SORT_KEYS.get(self.sort_key, self.SORT_KEYS["stem"])
        rows = sorted(rows, key=key_fn, reverse=self.sort_reverse)
        for i, b in enumerate(rows, 1):
            yjr = f"{b['yjr_count']}" if b["yjr_count"] else ("·" if b["sdr"] else "-")
            notion = f"{b['notion_count']}" if b["notion_count"] else "-"
            mtime = b.get("last_mtime", 0.0)
            mod = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d") if mtime else "-"
            table.add_row(
                str(i),
                _truncate(b["title"],  44),
                _truncate(b["author"], 18),
                b["kfx"].suffix,
                yjr,
                notion,
                mod,
                key=b["stem"],
            )

    def action_sort(self, key: str) -> None:
        """같은 키 두 번 = 방향 토글, 다른 키 = 그 키로 오름차순."""
        if self.sort_key == key:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_key = key
            self.sort_reverse = False
        self.populate_table()
        arrow = "▼" if self.sort_reverse else "▲"
        self.query_one(AppLogo).sub = (
            f"정렬 {key} {arrow}   ↑↓ 이동   ⏎ 미리보기   / 필터   s 동기화"
        )

    def _filtered_books(self) -> list[dict]:
        ft = self.filter_text.lower()
        if not ft:
            return self.books
        return [
            b for b in self.books
            if ft in b["title"].lower()
            or ft in b["author"].lower()
            or ft in b["stem"].lower()
        ]

    # -- actions ------------------------------------------------------------

    def action_focus_filter(self) -> None:
        inp = self.query_one("#filter", Input)
        inp.display = True
        inp.focus()

    def action_clear_filter(self) -> None:
        inp = self.query_one("#filter", Input)
        inp.value = ""
        inp.display = False
        self.filter_text = ""
        self.populate_table()
        self.query_one("#books", DataTable).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter":
            self.filter_text = event.value
            self.populate_table()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter":
            self.query_one("#books", DataTable).focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # DataTable에서 Enter 눌렀을 때도 클리핑 보기 열기
        self.action_preview()

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        """헤더 클릭 → 해당 컬럼 정렬."""
        if event.data_table.id != "books":
            return
        # 컬럼 인덱스 → sort key 매핑 (열 순서: #/제목/저자/포맷/YJR/Notion/최종수정)
        idx_to_key = {1: "title", 2: "author", 3: "format",
                      4: "yjr", 5: "notion", 6: "modified"}
        key = idx_to_key.get(event.column_index)
        if key:
            self.action_sort(key)

    def action_refresh_titles(self) -> None:
        """선택된(필터된) 책들 제목을 kfxlib로 재추출."""
        from kindle.ebook import extract_kfx_metadata
        books = self._filtered_books()
        if not books:
            return
        self.notify(f"제목 추출: {len(books)}권 …", timeout=2)
        for b in books:
            meta = get_or_extract(self.title_cache, b["kfx"],
                                  extract_kfx_metadata, refresh=True)
            b["title"]  = meta["title"]
            b["author"] = meta["author"]
        save_cache(DEFAULT_TITLE_CACHE, self.title_cache)
        self.populate_table()
        self.notify(f"제목 추출 완료", timeout=2)

    def _current_book(self) -> Optional[dict]:
        """현재 커서 위치의 책을 row key(=stem) 기준으로 조회.
        cursor_row 인덱스는 정렬 후 위치라 self.books 인덱스와 어긋날 수 있다."""
        table = self.query_one("#books", DataTable)
        if not self.books:
            return None
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        except Exception:
            return None
        stem = row_key.value
        for b in self.books:
            if b["stem"] == stem:
                return b
        return None

    def action_preview(self) -> None:
        b = self._current_book()
        if b is None:
            return
        # YJR 없는 책도 모달은 열되, 안에서 상태를 보여준다 (사용자 경험 일관)
        self.push_screen(ClippingPreview(b))

    def action_sync(self) -> None:
        if self.kindle_root is None:
            self.notify("Kindle 경로가 없습니다.", severity="error")
            return
        # 필터가 적용 중이면 그 부분집합을, 아니면 전체를 대상으로
        if self.filter_text:
            scope = self._filtered_books()
            label = f"필터된 {len(scope)}권 (필터: '{self.filter_text}')"
        else:
            scope = self.books
            label = "전체"
        def _after_sync(_):
            # SyncOptions 가 닫힐 때 상태 reload → status bar / Notion 카운트 갱신
            self.title_cache = load_cache(DEFAULT_TITLE_CACHE)
            self.reload_books()
        self.push_screen(SyncOptions(self.kindle_root, scope, label), _after_sync)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Kindle 클리핑 동기화 TUI")
    parser.add_argument("--kindle", default=None, metavar="PATH",
                        help="킨들 마운트 경로 (생략 시 자동 감지)")
    args = parser.parse_args()

    root = Path(args.kindle) if args.kindle else None
    if root is not None and not root.exists():
        print(f"오류: {root} 가 존재하지 않습니다.", file=sys.stderr)
        sys.exit(1)

    KindleTUI(kindle_root=root).run()


if __name__ == "__main__":
    main()
