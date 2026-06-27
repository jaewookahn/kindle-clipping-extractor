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
    Button, Checkbox, DataTable, Footer, Header, Input, Label, RichLog, Static,
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
    fill_clipping_text,
    fill_clipping_pages,
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
    base       = "#1e1e2e"
    mantle     = "#181825"
    crust      = "#11111b"
    surface0   = "#313244"
    surface1   = "#45475a"
    surface2   = "#585b70"
    overlay0   = "#6c7086"
    overlay1   = "#7f849c"
    overlay2   = "#9399b2"
    subtext0   = "#a6adc8"
    subtext1   = "#bac2de"
    text       = "#cdd6f4"
    lavender   = "#b4befe"
    blue       = "#89b4fa"
    sapphire   = "#74c7ec"
    sky        = "#89dceb"
    teal       = "#94e2d5"
    green      = "#a6e3a1"
    yellow     = "#f9e2af"
    peach      = "#fab387"
    maroon     = "#eba0ac"
    red        = "#f38ba8"
    mauve      = "#cba6f7"
    pink       = "#f5c2e7"


# ---------------------------------------------------------------------------
# 상태 바 (헤더 아래)
# ---------------------------------------------------------------------------

class AppLogo(Static):
    """좌측에 그라데이션 로고, 우측에 sub_title 안내."""

    sub = reactive[str]("")

    def render(self) -> str:
        # 글자별 그라데이션 (lavender → mauve → pink)
        word = " ✦ KINDLE  CLIPPING ✦ "
        grad = [CAT.lavender, CAT.lavender, CAT.blue, CAT.sapphire,
                CAT.sky, CAT.teal, CAT.green, CAT.yellow, CAT.peach,
                CAT.maroon, CAT.red, CAT.mauve, CAT.pink, CAT.pink]
        colored = []
        for i, ch in enumerate(word):
            c = grad[i % len(grad)]
            colored.append(f"[{c} b]{ch}[/]")
        logo = "".join(colored)
        hint = f"[{CAT.subtext0}]{self.sub}[/]" if self.sub else ""
        return f"{logo}   {hint}"


class StatusBar(Static):
    """Kindle 경로 + sync 현황. 2줄, chip 스타일."""

    kindle_path  = reactive[str]("")
    total_books  = reactive[int](0)
    with_clips   = reactive[int](0)
    notion_books = reactive[int](0)
    last_sync    = reactive[str]("never")

    @staticmethod
    def _chip(label: str, value, fg_label: str, bg_label: str,
              fg_value: str, bg_value: str) -> str:
        # 좌측 라벨 / 우측 값 두-톤 chip + 라운드 모서리
        return (
            f"[{fg_label} on {bg_label} b]  {label}  [/]"
            f"[{fg_value} on {bg_value} b] {value:>3} [/]"
        )

    def render(self) -> str:
        path = self.kindle_path or f"[{CAT.red}]미감지 — k 키로 선택[/]"
        line1 = (
            f"[{CAT.mauve} b]▎[/]"
            f"[{CAT.lavender} b] DEVICE [/]"
            f"[{CAT.subtext1}] {path}[/]"
        )
        chips = "  ".join([
            self._chip("BOOKS",  self.total_books,
                       CAT.base, CAT.blue,  CAT.blue,  CAT.surface0),
            self._chip("CLIPS",  self.with_clips,
                       CAT.base, CAT.green, CAT.green, CAT.surface0),
            self._chip("NOTION", self.notion_books,
                       CAT.base, CAT.mauve, CAT.mauve, CAT.surface0),
        ])
        line2 = (
            f"  {chips}   "
            f"[{CAT.overlay0}]│[/] "
            f"[{CAT.subtext0}]last sync[/] "
            f"[{CAT.sapphire}]{self.last_sync}[/]"
        )
        return f"{line1}\n{line2}"


# ---------------------------------------------------------------------------
# 클리핑 미리보기 모달
# ---------------------------------------------------------------------------

class ClippingPreview(ModalScreen):
    """선택한 책의 클리핑 목록 (YJR + KFX 텍스트 채워서)."""

    BINDINGS = [
        Binding("escape", "dismiss",  "닫기"),
        Binding("q",      "dismiss",  "닫기", show=False),
    ]

    CSS = """
    ClippingPreview { align: center middle; }
    #preview-box {
        width: 95%; height: 92%;
        border: thick #cba6f7;
        background: #1e1e2e;
        padding: 1 2;
    }
    #preview-title {
        dock: top;
        height: 2;
        content-align: center middle;
        background: #313244;
        color: #f5c2e7;
        text-style: bold;
        padding: 0 1;
    }
    #preview-body {
        height: 1fr;
        margin-top: 1;
    }
    #cover-panel {
        width: 32;
        border: round #45475a;
        background: #181825;
        padding: 1;
        align: center middle;
    }
    #cover-image { width: 100%; height: 1fr; }
    #cover-caption {
        dock: bottom;
        height: 1;
        content-align: center middle;
        color: #6c7086;
    }
    #preview-table {
        width: 1fr;
        margin-left: 1;
        border: round #45475a;
        background: #181825;
        scrollbar-color: #cba6f7 #11111b;
    }
    #preview-table > .datatable--header {
        background: #313244;
        color: #cba6f7;
        text-style: bold;
    }
    #preview-table > .datatable--cursor {
        background: #cba6f7 30%;
    }
    #preview-table > .datatable--odd-row { background: #1e1e2e; }
    #preview-table > .datatable--even-row { background: #181825; }
    """

    def __init__(self, book: dict) -> None:
        super().__init__()
        self.book = book

    def compose(self) -> ComposeResult:
        with Vertical(id="preview-box"):
            yield Label(
                f"[b]{self.book['title']}[/b]   {self.book['author']}    "
                f"({self.book['yjr_count']} YJR / {self.book['stem']})",
                id="preview-title",
            )
            with Horizontal(id="preview-body"):
                with Vertical(id="cover-panel"):
                    yield Static("[dim]표지 로드 중…[/dim]", id="cover-image")
                    yield Static("", id="cover-caption")
                yield DataTable(id="preview-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        table = self.query_one("#preview-table", DataTable)
        table.add_column("#",      width=4)
        table.add_column("타입",   width=14)
        table.add_column("색",     width=10)
        table.add_column("페이지", width=6)
        table.add_column("위치",   width=12)
        table.add_column("날짜",   width=16)
        table.add_column("내용",   width=80)
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
                page_map, kl_offsets, book_text = extract_kfx_info(self.book["kfx"])
                if book_text:
                    fill_clipping_text(clips, book_text)
                else:
                    errors.append("KFX 본문 추출 실패 (kfxlib 없음 또는 KFX 손상) "
                                  "→ 하이라이트 텍스트가 비어 보일 수 있음")
                if page_map:
                    fill_clipping_pages(clips, page_map)
                if kl_offsets:
                    fill_clipping_kindle_locations(clips, kl_offsets)
                else:
                    errors.append("KL 맵 없음 → 위치가 raw char offset")
            except Exception as e:
                errors.append(f"KFX 정보 추출 실패: {e}")

        # `_render` 같은 underscore-prefixed 이름은 Widget 내부 메서드와 겹쳐
        # Textual이 인자 없이 호출하는 경우가 있다. 안전한 이름 사용.
        self.app.call_from_thread(lambda: self._show_clips(clips, errors))

    def _load_cover(self) -> None:
        """Google Books 표지 URL → 임시 파일 → Image 위젯 교체."""
        try:
            url = _get_cover_url(self.book["title"], self.book["author"])
        except Exception as e:
            self.app.call_from_thread(
                lambda e=e: self._show_cover_text(f"[red]검색 실패: {e}[/red]")
            )
            return
        if not url:
            self.app.call_from_thread(
                lambda: self._show_cover_text(
                    "[dim](Google Books에서 표지 못 찾음)[/dim]"
                )
            )
            return
        try:
            import tempfile, requests
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            tmp = Path(tempfile.mkdtemp(prefix="kindle_cover_")) / "cover.jpg"
            tmp.write_bytes(r.content)
        except Exception as e:
            self.app.call_from_thread(
                lambda e=e: self._show_cover_text(f"[red]다운로드 실패: {e}[/red]")
            )
            return
        self.app.call_from_thread(lambda p=tmp, u=url: self._swap_in_image(p, u))

    def _show_cover_text(self, text: str) -> None:
        try:
            self.query_one("#cover-image", Static).update(text)
        except Exception:
            pass

    def _swap_in_image(self, path: Path, url: str) -> None:
        """기존 #cover-image 위젯 제거 후 그 자리에 textual-image Image 마운트."""
        try:
            from textual_image.widget import Image
        except Exception as e:
            self._show_cover_text(f"[red]textual-image 로드 실패: {e}[/red]")
            return
        try:
            panel   = self.query_one("#cover-panel", Vertical)
            caption = self.query_one("#cover-caption", Static)
            old     = self.query_one("#cover-image")
            old.remove()
            img = Image(str(path), id="cover-image")
            # caption은 dock:bottom 이므로 mount 순서 무관
            panel.mount(img)
            caption.update(f"[dim]{_truncate(url, 24)}[/dim]")
        except Exception as e:
            self._show_cover_text(f"[red]이미지 위젯 실패: {e}[/red]")

    # YJR이 content 앞에 "[yellow] ..." 같은 prefix를 붙임. 분리해서 색 컬럼으로.
    _COLOR_RE = __import__("re").compile(r"^\[([a-zA-Z]+)\]\s*(.*)", flags=__import__("re").DOTALL)

    _TYPE_LABEL = {
        "highlight":     "🖍️ 하이라이트",
        "bookmark":      "🔖 북마크",
        "note":          "📝 노트",
        "last_position": "📍 위치",
    }
    _COLOR_CHIP = {
        "yellow": f"[{CAT.base} on {CAT.yellow} b] yellow [/]",
        "blue":   f"[{CAT.base} on {CAT.blue}   b]  blue  [/]",
        "pink":   f"[{CAT.base} on {CAT.pink}   b]  pink  [/]",
        "orange": f"[{CAT.base} on {CAT.peach}  b] orange [/]",
    }

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
        table = self.query_one("#preview-table", DataTable)
        table.loading = False
        errors = errors or []
        if not clips:
            if errors:
                for e in errors:
                    cell, h = self._content_cell(f"[red]{e}[/red]", markup=True)
                    table.add_row("!", "-", "-", "-", "-", "-", cell, height=h)
            else:
                table.add_row("-", "-", "-", "-", "-", "-", "(클리핑 없음)")
            return
        # 클리핑 있으면 에러는 경고 노티로
        if errors:
            self.notify(" / ".join(errors), severity="warning", timeout=6)
        # added_date 기준 정렬 (없으면 가장 끝으로)
        clips_sorted = sorted(clips, key=lambda c: c.added_date or "9999")
        for i, c in enumerate(clips_sorted, 1):
            loc = f"L{c.location_start}" if c.location_start is not None else "-"
            if c.location_end and c.location_end != c.location_start:
                loc += f"-{c.location_end}"
            page = str(c.page) if c.page else "-"
            date = (c.added_date or "-")[:16]   # "YYYY-MM-DD HH:MM"

            color, raw = self._split_color(c.content or "")
            color_cell = self._COLOR_CHIP.get(color, color or "-")

            # 북마크는 위치 1글자 텍스트가 의미 없음 → 비움
            if c.clip_type == "bookmark":
                shown = "—"
            else:
                shown = (raw or "").strip() or "·"

            content_cell, height = self._content_cell(shown)
            type_cell = self._TYPE_LABEL.get(c.clip_type, c.clip_type)
            table.add_row(
                str(i),
                type_cell,
                color_cell,
                page,
                loc,
                date,
                content_cell,
                height=height,
            )


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
        border: thick #cba6f7;
        background: #1e1e2e;
        padding: 1 2;
    }
    #picker-title {
        dock: top;
        height: 2;
        content-align: center middle;
        background: #313244;
        color: #89dceb;
        text-style: bold;
        padding: 0 1;
    }
    #picker-table {
        height: 1fr;
        margin-top: 1;
        border: round #45475a;
        background: #181825;
    }
    #picker-table > .datatable--header {
        background: #313244;
        color: #89dceb;
        text-style: bold;
    }
    #picker-table > .datatable--cursor {
        background: #89dceb 30%;
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
        Binding("escape", "dismiss", "닫기"),
    ]

    CSS = """
    SyncOptions { align: center middle; }
    #sync-box {
        width: 92%; height: 92%;
        border: thick #cba6f7;
        background: #1e1e2e;
        padding: 1 2;
    }
    #sync-form {
        dock: top;
        height: 17;
        padding: 1 1;
        background: #181825;
        border-bottom: heavy #cba6f7;
    }
    #file-row { height: 3; }
    #file-row Label { width: 8; padding-top: 1; color: #cdd6f4; text-style: none; }
    #file-row Input {
        width: 1fr;
        background: #313244;
        color: #cdd6f4;
        border: round #45475a;
    }
    #file-row Input:focus { border: round #cba6f7; }
    #sync-form Label {
        color: #89dceb;
        text-style: bold;
        margin-bottom: 1;
    }
    #sync-log {
        height: 1fr;
        border: round #45475a;
        padding: 0 1;
        background: #11111b;
        color: #cdd6f4;
        scrollbar-color: #cba6f7 #11111b;
    }
    #sync-buttons {
        dock: bottom;
        height: 3;
        align: center middle;
        background: #1e1e2e;
    }
    #sync-buttons Button {
        margin: 0 1;
        background: #313244;
        color: #cdd6f4;
        border: tall #45475a;
    }
    #sync-buttons Button#run {
        background: #a6e3a1;
        color: #1e1e2e;
        text-style: bold;
        border: tall #94e2d5;
    }
    #sync-buttons Button#run:hover { background: #94e2d5; }
    Checkbox {
        width: 1fr;
        background: transparent;
        color: #cdd6f4;
    }
    Checkbox > .toggle--button {
        color: #cba6f7;
        background: #45475a;
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
        with Vertical(id="sync-box"):
            with Vertical(id="sync-form"):
                yield Label(f"[b]대상[/b]: {self.scope_label}  ({len(self.scope_books)}권)")
                yield Checkbox(
                    "dry-run (목록만, 파일·Notion·상태 모두 저장 안 함)",
                    id="opt-dry", value=False,
                )
                yield Checkbox("파일로 저장", id="opt-file", value=True)
                with Horizontal(id="file-row"):
                    yield Label("경로:")
                    yield Input(
                        value=str(Path("kindle_sync.json").resolve()),
                        placeholder="확장자로 형식 결정 (.json/.csv/.md/.txt)",
                        id="opt-file-path",
                    )
                yield Checkbox(
                    f"Notion 업로드 (NOTION_TOKEN, NOTION_DB 환경변수 필요)",
                    id="opt-notion",
                    value=bool(os.environ.get("NOTION_TOKEN") and os.environ.get("NOTION_DB")),
                )
                yield Checkbox("상태 초기화 (--reset, 전체 재동기화)", id="opt-reset")
            yield RichLog(id="sync-log", highlight=True, markup=True, wrap=False)
            with Horizontal(id="sync-buttons"):
                yield Button("실행", id="run", variant="primary")
                yield Button("닫기", id="close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close":
            self.dismiss()
            return
        if event.button.id == "run":
            self._start_run()

    def _start_run(self) -> None:
        import subprocess
        log = self.query_one("#sync-log", RichLog)
        log.clear()

        dry    = self.query_one("#opt-dry",    Checkbox).value
        file_o = self.query_one("#opt-file",   Checkbox).value
        notion = self.query_one("#opt-notion", Checkbox).value
        reset  = self.query_one("#opt-reset",  Checkbox).value

        if not (dry or file_o or notion):
            log.write("[red]실행할 동작이 없습니다. dry-run / 파일 / Notion 중 하나 선택[/red]")
            return

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

        # scope: 필터·선택된 책이 부분집합이면 --book 추가
        stems = [b["stem"] for b in self.scope_books]
        if 0 < len(stems) < len(self._all_book_stems()):
            for s in stems:
                cmd += ["--book", s]

        log.write(f"[b]$ {' '.join(self._mask(cmd))}[/b]")
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
        except Exception as e:
            log.write(f"[red]subprocess 실행 실패: {e}[/red]")
            return
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

    def _pump_output(self) -> None:
        log = self.query_one("#sync-log", RichLog)
        assert self._proc is not None
        for line in self._proc.stdout:
            # tqdm이 새어 나오는 경우 대비: ANSI 제거 + CR 뒤 마지막 segment만
            text = self._ANSI.sub("", line)
            if "\r" in text:
                text = text.rsplit("\r", 1)[-1]
            text = text.rstrip()
            if not text:
                continue
            self.app.call_from_thread(lambda t=text: log.write(t))
        rc = self._proc.wait()
        msg = (
            f"[green]완료 (exit {rc})[/green]"
            if rc == 0 else f"[red]실패 (exit {rc})[/red]"
        )
        self.app.call_from_thread(lambda m=msg: log.write(m))


# ---------------------------------------------------------------------------
# 메인 앱
# ---------------------------------------------------------------------------

class KindleTUI(App):
    # Catppuccin Mocha
    CSS = """
    /* === 전역 ================================================ */
    Screen { background: #1e1e2e; color: #cdd6f4; }

    Footer {
        background: #11111b;
        color: #cdd6f4;
        border-top: heavy #45475a;
    }
    Footer > .footer--key {
        background: #cba6f7;
        color: #1e1e2e;
        text-style: bold;
    }
    Footer > .footer--description {
        color: #a6adc8;
    }
    Toast { border: thick #cba6f7; background: #313244; color: #cdd6f4; }
    Toast.-warning { border: thick #f9e2af; }
    Toast.-error   { border: thick #f38ba8; }

    /* === Logo Row ============================================ */
    AppLogo {
        height: 1;
        padding: 0 2;
        background: #181825;
        color: #cdd6f4;
    }

    /* === Status Bar ========================================== */
    StatusBar {
        height: 4;
        padding: 1 2;
        background: #181825;
        border-bottom: heavy #cba6f7;
    }

    /* === Filter Input ======================================== */
    Input.filter {
        height: 3;
        margin: 0 1;
        border: round #45475a;
        background: #313244;
        color: #cdd6f4;
    }
    Input.filter:focus {
        border: round #cba6f7;
        background: #313244;
    }

    /* === Books Table ========================================= */
    #books {
        height: 1fr;
        margin: 0 1;
        border: round #45475a;
        background: #181825;
        scrollbar-color: #cba6f7 #11111b;
        scrollbar-color-hover: #f5c2e7;
        scrollbar-corner-color: #11111b;
        scrollbar-size: 1 1;
    }
    #books > .datatable--header {
        background: #313244;
        color: #cba6f7;
        text-style: bold;
    }
    #books > .datatable--cursor {
        background: #cba6f7 35%;
        color: #cdd6f4;
        text-style: bold;
    }
    #books > .datatable--hover { background: #313244; }
    #books > .datatable--odd-row { background: #1e1e2e; }
    #books > .datatable--even-row { background: #181825; }
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

    def on_mount(self) -> None:
        self.title    = "kindle-clipping-tui"
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
        self.push_screen(SyncOptions(self.kindle_root, scope, label))


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
