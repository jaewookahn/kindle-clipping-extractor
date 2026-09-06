"""여러 Kindle 후보(MacDroid stale + 새 기기 등) 중 선택하는 다이얼로그.

tui.py 의 KindlePicker 대응.
"""

from datetime import datetime
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class DevicePickerDialog(QDialog):
    def __init__(self, candidates: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.candidates = candidates
        self.selected_path = None

        self.setWindowTitle("Kindle 기기 선택")
        self.resize(720, 320)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Kindle 후보 {len(candidates)}개 — 더블클릭으로 선택"))

        table = QTableWidget(len(candidates), 5, self)
        table.setHorizontalHeaderLabels(["라벨", "종류", "최근 활동", "파일", "경로"])
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        for row, c in enumerate(candidates):
            mt = (
                datetime.fromtimestamp(c["latest_mtime"]).strftime("%Y-%m-%d %H:%M")
                if c["latest_mtime"] else "—"
            )
            values = [c["label"], c["source"], mt, str(c["file_count"]), str(c["path"])]
            for col, v in enumerate(values):
                table.setItem(row, col, QTableWidgetItem(v))

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
