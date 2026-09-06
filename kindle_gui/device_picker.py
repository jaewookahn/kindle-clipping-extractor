"""여러 Kindle 후보(MacDroid stale + 새 기기 등) 중 선택하는 다이얼로그.

tui.py 의 KindlePicker 대응.
"""

from datetime import datetime
from typing import Optional

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from kindle_gui.device_icons import device_pixmap


class DevicePickerDialog(QDialog):
    def __init__(self, candidates: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.candidates = candidates
        self.selected_path = None

        self.setWindowTitle("Kindle 기기 선택")
        self.resize(880, 540)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Kindle 후보 {len(candidates)}개 — 더블클릭으로 선택"))

        table = QTableWidget(len(candidates), 5, self)
        table.setHorizontalHeaderLabels(["기기", "종류", "최근 활동", "파일", "경로"])
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        # 기기 실루엣이 들어가므로 행·아이콘 크기를 키운다
        table.setIconSize(QSize(56, 76))
        table.setColumnWidth(0, 230)
        # 경로가 길다 — 줄바꿈 대신 말줄임(…)으로 한 줄 유지
        table.setWordWrap(False)
        table.setTextElideMode(Qt.TextElideMode.ElideMiddle)

        for row, c in enumerate(candidates):
            mt = (
                datetime.fromtimestamp(c["latest_mtime"]).strftime("%Y-%m-%d %H:%M")
                if c["latest_mtime"] else "—"
            )
            name_item = QTableWidgetItem(c["label"])
            name_item.setIcon(QIcon(device_pixmap(c["label"], height=76)))
            table.setItem(row, 0, name_item)

            for col, v in enumerate([c["source"], mt, str(c["file_count"]), str(c["path"])], start=1):
                item = QTableWidgetItem(v)
                item.setToolTip(v)
                table.setItem(row, col, item)
            table.setRowHeight(row, 84)

        table.cellDoubleClicked.connect(self._on_double_click)
        table.selectRow(0)
        self.table = table
        layout.addWidget(table)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_double_click(self, row: int, _col: int) -> None:
        self.selected_path = self.candidates[row]["path"]
        self.accept()

    def _on_accept(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.selected_path = self.candidates[row]["path"]
        self.accept()

    @staticmethod
    def pick(candidates: list[dict], parent=None):
        """모달로 띄우고 선택된 Path 를 반환. 취소하면 None."""
        dlg = DevicePickerDialog(candidates, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.selected_path
        return None
