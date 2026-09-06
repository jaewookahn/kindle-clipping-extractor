"""클리핑 미리보기 다이얼로그 — tui.py 의 ClippingPreview 대응.

터미널 그래픽 프로토콜(Kitty/Sixel/Halfcell) 감지 코드는 전부 사라진다 —
표지는 QPixmap 으로 그대로 그리면 된다.
"""

import re
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
)

from kindle.clip_loader import load_book_clippings
from kindle.covers import load_book_cover
from kindle_gui.widgets import SortItem
from kindle_gui.workers import run_in_background

_COLUMNS = ["#", "타입", "색", "페이지", "위치", "날짜", "챕터", "내용"]
_CONTENT_COL = 7
# 내용 컬럼(7)은 Stretch 라 여기 없다 — 나머지만 고정 폭
_COL_WIDTHS = {0: 52, 1: 44, 2: 52, 3: 56, 4: 84, 5: 116, 6: 150}
_COVER_W = 200

# YJR 이 content 앞에 "[yellow] ..." 같은 prefix 를 붙인다. 분리해서 색 컬럼으로.
_COLOR_RE = re.compile(r"^\[([a-zA-Z]+)\]\s*(.*)", flags=re.DOTALL)

_TYPE_ICON = {
    "highlight": "✎",
    "bookmark": "🔖",
    "note": "✐",
    "last_position": "➤",
}
_COLOR_BG = {
    "yellow": "#e5c890", "blue": "#8caaee", "pink": "#f4b8e4", "orange": "#ef9f76",
    "red": "#e78284", "green": "#a6d189", "purple": "#ca9ee6",
}
_COLOR_KO = {
    "yellow": "노랑", "blue": "파랑", "pink": "분홍", "orange": "주황",
    "red": "빨강", "green": "초록", "purple": "보라",
}


def _split_color(content: str) -> tuple[str, str]:
    if not content:
        return "", ""
    m = _COLOR_RE.match(content)
    if m:
        return m.group(1).lower(), m.group(2)
    return "", content


class ClippingDialog(QDialog):
    def __init__(self, book: dict, parent=None) -> None:
        super().__init__(parent)
        self.book = book
        self.clips: list = []
        self.errors: list[str] = []
        self.search_text = ""

        title = book.get("title", book["stem"])
        author = book.get("author", "")
        self.setWindowTitle(f"{title} — {author}" if author else title)
        self.resize(1100, 680)

        self._build_ui()
        self._load()

    # -- UI --------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("🔍  검색 — 내용·챕터·색·타입")
        self.search_input.textChanged.connect(self._on_search_changed)
        layout.addWidget(self.search_input)

        body = QHBoxLayout()

        # 표지 패널 — 고정 폭, 위쪽 정렬. 나머지 폭은 전부 테이블이 가져간다.
        cover_col = QVBoxLayout()
        self.cover_label = QLabel("표지 로드 중…", self)
        self.cover_label.setFixedWidth(_COVER_W)
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.cover_label.setWordWrap(True)
        self.cover_caption = QLabel("", self)
        self.cover_caption.setFixedWidth(_COVER_W)
        self.cover_caption.setWordWrap(True)
        self.cover_caption.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.cover_caption.setStyleSheet("color: gray; font-size: 11px;")
        cover_col.addWidget(self.cover_label)
        cover_col.addWidget(self.cover_caption)

        self.info_label = QLabel("", self)
        self.info_label.setFixedWidth(_COVER_W)
        self.info_label.setWordWrap(True)
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.info_label.setStyleSheet("color: gray; font-size: 11px;")
        cover_col.addSpacing(8)
        cover_col.addWidget(self.info_label)
        cover_col.addStretch(1)
        body.addLayout(cover_col)

        # 클리핑 테이블
        table = QTableWidget(0, len(_COLUMNS), self)
        table.setHorizontalHeaderLabels(_COLUMNS)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setWordWrap(True)
        table.setAlternatingRowColors(True)

        header = table.horizontalHeader()
        for col, width in _COL_WIDTHS.items():
            table.setColumnWidth(col, width)
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        # 내용 컬럼이 남는 폭을 전부 차지 → 가로 스크롤 없이 본문이 접힌다
        header.setSectionResizeMode(_CONTENT_COL, QHeaderView.ResizeMode.Stretch)
        # 정렬하면 행 순서가 바뀌는데 행 높이는 위치에 남아 있어 내용과 어긋난다.
        # 정렬 직후 높이를 다시 계산해 준다.
        header.sortIndicatorChanged.connect(lambda *_: table.resizeRowsToContents())
        table.sortByColumn(0, Qt.SortOrder.AscendingOrder)

        self.table = table
        body.addWidget(table, stretch=1)

        layout.addLayout(body, stretch=1)

        close_row = QHBoxLayout()
        self.warn_label = QLabel("", self)
        self.warn_label.setStyleSheet("color: #b8860b;")
        self.warn_label.setWordWrap(True)
        close_row.addWidget(self.warn_label, stretch=1)
        close_btn = QPushButton("닫기", self)
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

    # -- 데이터 로드 --------------------------------------------------------

    def _load(self) -> None:
        run_in_background(
            load_book_clippings, self.book,
            on_done=self._on_clips_loaded,
            on_error=lambda e: self._on_clips_loaded(([], [f"로드 실패: {e}"])),
        )
        run_in_background(load_book_cover, self.book, on_done=self._on_cover_loaded)

    def _on_clips_loaded(self, result) -> None:
        self.clips, self.errors = result
        self._redraw_table()

    def _on_cover_loaded(self, result) -> None:
        path, caption = result
        if path:
            pix = QPixmap(str(path))
            if not pix.isNull():
                scaled = pix.scaledToWidth(
                    _COVER_W, Qt.TransformationMode.SmoothTransformation
                )
                # wordWrap 이 켜진 QLabel 은 minimumSizeHint 높이가 0 이라,
                # 아래 addStretch 가 세로 공간을 전부 가져가면서 레이블이
                # 0 높이로 찌그러진다 (pixmap 은 멀쩡한데 화면에선 안 보임).
                # 이미지를 넣을 때는 워드랩을 끄고 높이를 고정한다.
                self.cover_label.setWordWrap(False)
                self.cover_label.setPixmap(scaled)
                self.cover_label.setFixedHeight(scaled.height())
                self.cover_caption.setText(caption)
                return
        self.cover_label.setWordWrap(True)
        self.cover_label.setMinimumHeight(40)
        self.cover_label.setText(caption)

    # -- 테이블 --------------------------------------------------------------

    def _filtered_clips(self) -> list:
        ft = self.search_text.strip().lower()
        if not ft:
            return self.clips
        out = []
        for c in self.clips:
            hay = " ".join([
                c.content or "", c.chapter or "", c.clip_type or "", str(c.page or ""),
            ]).lower()
            if ft in hay:
                out.append(c)
        return out

    def _redraw_table(self) -> None:
        table = self.table
        # 채우는 동안 정렬을 꺼둔다. 켜둔 채로 넣으면 행이 삽입될 때마다
        # 재정렬돼 인덱스가 밀리고 행 높이가 내용과 어긋난다.
        table.setSortingEnabled(False)
        self._update_side_info()

        if not self.clips:
            table.setRowCount(1)
            msg = " / ".join(self.errors) if self.errors else "(클리핑 없음)"
            table.setItem(0, _CONTENT_COL, SortItem(msg, 0))
            for col in range(_CONTENT_COL):
                table.setItem(0, col, SortItem("-", 0))
            table.resizeRowsToContents()
            return

        clips = self._filtered_clips()
        table.setRowCount(len(clips))
        for i, c in enumerate(clips):
            loc = f"L{c.location_start}" if c.location_start is not None else "-"
            if c.location_end and c.location_end != c.location_start:
                loc += f"-{c.location_end}"
            page = c.page or 0
            page_text = str(c.page) if c.page else "-"
            date_text = (c.added_date or "-")[:16]

            color, raw = _split_color(c.content or "")
            if c.clip_type == "bookmark":
                shown = "—"
            else:
                shown = (raw or "").strip() or "·"

            table.setItem(i, 0, SortItem(str(i + 1), i))
            table.setItem(i, 1, SortItem(_TYPE_ICON.get(c.clip_type, c.clip_type), c.clip_type))

            color_item = SortItem(_COLOR_KO.get(color, color[:2] if color else "-"), color)
            if color in _COLOR_BG:
                color_item.setBackground(QColor(_COLOR_BG[color]))
            table.setItem(i, 2, color_item)

            table.setItem(i, 3, SortItem(page_text, page))
            table.setItem(i, 4, SortItem(loc, c.location_start if c.location_start is not None else 0))
            table.setItem(i, 5, SortItem(date_text, c.added_date or ""))

            chapter_item = SortItem(c.chapter or "-", c.chapter or "")
            if c.chapter:
                chapter_item.setToolTip(c.chapter)
            table.setItem(i, 6, chapter_item)

            content_item = SortItem(shown, shown)
            content_item.setToolTip(shown)
            table.setItem(i, _CONTENT_COL, content_item)

        table.setSortingEnabled(True)
        table.resizeRowsToContents()

    def _update_side_info(self) -> None:
        """표지 아래 책 정보 + 하단 경고줄 갱신."""
        title = self.book.get("title", self.book["stem"])
        author = self.book.get("author", "")
        shown = len(self._filtered_clips())
        total = len(self.clips)
        counts: dict[str, int] = {}
        for c in self.clips:
            counts[c.clip_type] = counts.get(c.clip_type, 0) + 1
        breakdown = "  ".join(
            f"{_TYPE_ICON.get(k, k)} {v}" for k, v in sorted(counts.items())
        )
        lines = [f"<b>{title}</b>"]
        if author:
            lines.append(author)
        lines.append("")
        lines.append(f"클리핑 {shown}/{total}" if shown != total else f"클리핑 {total}")
        if breakdown:
            lines.append(breakdown)
        lines.append(f"<span style='color:#999'>{self.book['stem']}</span>")
        self.info_label.setText("<br>".join(lines))

        if self.errors:
            self.warn_label.setText("⚠  " + "  /  ".join(self.errors))
        else:
            self.warn_label.setText("")

    def _on_search_changed(self, text: str) -> None:
        self.search_text = text
        self._redraw_table()
