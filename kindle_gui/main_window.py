"""메인 창 — 책 목록·필터·정렬·상태바. tui.py 의 KindleTUI 대응."""

from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHeaderView,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QTableWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from kindle.device import find_kindle_candidates
from kindle.notion_export import DEFAULT_STATE as NOTION_DEFAULT_STATE
from kindle.notion_export import load_state as load_notion_state
from kindle.title_cache import DEFAULT_PATH as DEFAULT_TITLE_CACHE
from kindle.title_cache import get_or_extract, load_cache, save_cache

import sync_kfx as sk

from kindle_gui.clipping_dialog import ClippingDialog
from kindle_gui.device_picker import DevicePickerDialog
from kindle_gui.sync_dialog import SyncDialog
from kindle_gui.widgets import SortItem
from kindle_gui.workers import run_in_background

_COLUMNS = ["#", "제목", "저자", "포맷", "YJR", "Notion", "최종수정"]


class MainWindow(QMainWindow):
    def __init__(self, kindle_root: Optional[Path] = None) -> None:
        super().__init__()
        self.kindle_root = kindle_root
        self.title_cache = load_cache(DEFAULT_TITLE_CACHE)
        self.books: list[dict] = []
        self.filter_text = ""

        self.setWindowTitle("Kindle Clipping Extractor")
        self.resize(980, 640)

        self._build_ui()
        self._build_toolbar()

        if self.kindle_root is None:
            self._detect_device()
        else:
            self._set_device(self.kindle_root)

    # -- UI 구성 --------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)

        self.filter_input = QLineEdit(central)
        self.filter_input.setPlaceholderText("🔍  필터 — 제목·저자·파일명")
        self.filter_input.textChanged.connect(self._on_filter_changed)
        layout.addWidget(self.filter_input)

        table = QTableWidget(0, len(_COLUMNS), central)
        table.setHorizontalHeaderLabels(_COLUMNS)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSortingEnabled(True)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.cellDoubleClicked.connect(self._on_row_activated)
        self.table = table
        layout.addWidget(table)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar(self))
        self._update_status()

    def _build_toolbar(self) -> None:
        bar = QToolBar("메인", self)
        self.addToolBar(bar)

        bar.addAction("제목 새로고침").triggered.connect(self._refresh_titles)
        bar.addAction("동기화").triggered.connect(self._open_sync_dialog)
        bar.addAction("기기 변경").triggered.connect(self._detect_device)

    # -- 기기 감지 --------------------------------------------------------------

    def _detect_device(self) -> None:
        cands = find_kindle_candidates()
        if not cands:
            QMessageBox.warning(self, "기기 없음", "Kindle 후보를 찾지 못했습니다.")
            return
        if len(cands) == 1:
            self._set_device(cands[0]["path"])
            return
        path = DevicePickerDialog.pick(cands, self)
        if path:
            self._set_device(path)

    def _set_device(self, path: Path) -> None:
        self.kindle_root = path
        self._update_status()
        self.reload_books()

    # -- 데이터 로드 --------------------------------------------------------------

    def reload_books(self) -> None:
        """documents/ 스캔 + 제목 캐시 + Notion 상태 합쳐 self.books 채움.

        디렉터리 스캔조차 워커로 뺀다 — MacDroid MTP 마운트가 다른 기기와
        동시에 연결됐을 때 즉시 응답 없음 상태가 될 수 있음을 확인했다
        (DEVLOG.md 9절). 메인 스레드에서 직접 돌리면 그 순간 창 전체가 멈춘다.
        """
        if self.kindle_root is None:
            return
        documents = self.kindle_root / "documents"
        run_in_background(
            self._scan_books, documents,
            on_done=self._on_books_loaded,
            on_error=lambda e: QMessageBox.critical(self, "스캔 실패", e),
        )

    def _scan_books(self, documents: Path) -> list[dict]:
        """워커 스레드에서 실행."""
        raw = sk.list_kfx_books(documents)
        for b in raw:
            hit = self.title_cache.get("books", {}).get(str(b["kfx"].resolve()))
            if hit:
                b["title"], b["author"] = hit["title"], hit["author"]
            else:
                b["title"], b["author"] = b["stem"], ""

        notion_state = load_notion_state(NOTION_DEFAULT_STATE)
        for b in raw:
            b["notion_count"] = len(
                notion_state.get("books", {}).get(b["title"], {})
                                              .get("synced_fingerprints", [])
            )
        return raw

    def _on_books_loaded(self, books: list[dict]) -> None:
        self.books = books
        self._populate_table()
        self._update_status()

    def _update_status(self) -> None:
        bar = self.statusBar()
        if self.kindle_root is None:
            bar.showMessage("Kindle 미감지 — 툴바의 '기기 변경'으로 선택하세요")
            return
        total = len(self.books)
        with_clips = sum(1 for b in self.books if b["yjr_count"] > 0)
        notion_books = sum(1 for b in self.books if b.get("notion_count"))
        ns = load_notion_state(NOTION_DEFAULT_STATE)
        last_sync = ns.get("last_sync") or "never"
        bar.showMessage(
            f"{self.kindle_root}   |   책 {total}   클립 {with_clips}   "
            f"Notion {notion_books}   최근 동기화 {last_sync}"
        )

    # -- 테이블 --------------------------------------------------------------

    def _filtered_books(self) -> list[dict]:
        ft = self.filter_text.lower()
        if not ft:
            return self.books
        return [
            b for b in self.books
            if ft in b["title"].lower() or ft in b["author"].lower() or ft in b["stem"].lower()
        ]

    def _populate_table(self) -> None:
        table = self.table
        table.setSortingEnabled(False)
        rows = self._filtered_books()
        table.setRowCount(len(rows))
        for i, b in enumerate(rows):
            yjr_count = b["yjr_count"]
            yjr_text = str(yjr_count) if yjr_count else ("·" if b["sdr"] else "-")
            notion_count = b.get("notion_count", 0)
            notion_text = str(notion_count) if notion_count else "-"
            mtime = b.get("last_mtime", 0.0)
            mod_text = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d") if mtime else "-"

            items = [
                SortItem(str(i + 1), i),
                SortItem(b["title"], b["title"].lower()),
                SortItem(b["author"], b["author"].lower()),
                SortItem(b["kfx"].suffix, b["kfx"].suffix),
                SortItem(yjr_text, yjr_count),
                SortItem(notion_text, notion_count),
                SortItem(mod_text, mtime),
            ]
            for col, item in enumerate(items):
                table.setItem(i, col, item)
            table.item(i, 0).setData(Qt.ItemDataRole.UserRole, b["stem"])
        table.setSortingEnabled(True)

    def _on_filter_changed(self, text: str) -> None:
        self.filter_text = text
        self._populate_table()

    def _current_book(self) -> Optional[dict]:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        stem = item.data(Qt.ItemDataRole.UserRole)
        return next((b for b in self.books if b["stem"] == stem), None)

    def _on_row_activated(self, _row: int, _col: int) -> None:
        book = self._current_book()
        if book:
            ClippingDialog(book, self).exec()

    # -- 액션 --------------------------------------------------------------

    def _refresh_titles(self) -> None:
        books = self._filtered_books()
        if not books:
            return
        run_in_background(
            self._do_refresh_titles, books,
            on_done=lambda _: (self._populate_table(), self._update_status()),
            on_error=lambda e: QMessageBox.critical(self, "새로고침 실패", e),
        )

    def _do_refresh_titles(self, books: list[dict]) -> None:
        from kindle.ebook import extract_kfx_metadata
        for b in books:
            meta = get_or_extract(self.title_cache, b["kfx"], extract_kfx_metadata, refresh=True)
            b["title"], b["author"] = meta["title"], meta["author"]
        save_cache(DEFAULT_TITLE_CACHE, self.title_cache)

    def _open_sync_dialog(self) -> None:
        if self.kindle_root is None:
            QMessageBox.warning(self, "기기 없음", "Kindle 경로가 없습니다.")
            return
        if self.filter_text:
            scope = self._filtered_books()
            label = f"필터된 {len(scope)}권 (필터: '{self.filter_text}')"
        else:
            scope = self.books
            label = "전체"
        dlg = SyncDialog(self.kindle_root, scope, label, self.books, self)
        dlg.exec()
        # SyncOptions 가 닫히면 상태 reload → 상태바/Notion 카운트 갱신 (TUI 와 동일)
        self.title_cache = load_cache(DEFAULT_TITLE_CACHE)
        self.reload_books()
