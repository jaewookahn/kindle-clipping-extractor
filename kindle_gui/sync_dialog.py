"""동기화 옵션 다이얼로그 — tui.py 의 SyncOptions 대응.

sync_kfx.py 를 QProcess 로 그대로 실행한다. 동기화 로직 자체는 이 세션
전체에서 한 줄도 다시 구현하지 않는다 — TUI 도 원래 subprocess 로 불렀다.
"""

import html
import os
import re
import sys
from pathlib import Path

from PyQt6.QtCore import QProcess, QProcessEnvironment
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_PROG_RE = re.compile(r"^\[\s*\d+\s*/\s*\d+\]")

# tui.py SyncOptions._pump_output 의 색 규칙과 동일 (Catppuccin 톤 근사치)
_COLOR_PROGRESS = "#4a90d9"
_COLOR_GOOD     = "#2e8b57"
_COLOR_DONE     = "#1a9080"
_COLOR_BAD      = "#c0392b"
_COLOR_WARN     = "#b8860b"


def _line_color(text: str) -> str | None:
    s = text.lstrip()
    if _PROG_RE.match(text):
        return _COLOR_PROGRESS
    if s.startswith(("→", "↻")):
        return _COLOR_GOOD
    if s.startswith("✓"):
        return _COLOR_DONE
    if s.startswith(("×", "✗", "오류", "[경고]", "[warn]", "Traceback")):
        return _COLOR_BAD
    return None


class SyncDialog(QDialog):
    def __init__(self, kindle_root: Path, scope_books: list[dict],
                scope_label: str, all_books: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.kindle_root = kindle_root
        self.scope_books = scope_books
        self.all_books = all_books
        self.proc: QProcess | None = None

        self.setWindowTitle("동기화")
        self.resize(720, 560)
        self._build_ui(scope_label)

    # -- UI --------------------------------------------------------------

    def _build_ui(self, scope_label: str) -> None:
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(f"<b>대상</b>  {scope_label}   ·   {len(self.scope_books)}권", self))

        layout.addWidget(QLabel("<b>출력 대상</b>", self))
        self.chk_file = QCheckBox("파일로 저장", self)
        self.chk_file.setChecked(True)
        layout.addWidget(self.chk_file)

        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("경로", self))
        self.file_path = QLineEdit(str(Path("kindle_sync.json").resolve()), self)
        self.file_path.setPlaceholderText(".json / .csv / .md / .txt — 확장자로 형식 결정")
        file_row.addWidget(self.file_path)
        layout.addLayout(file_row)

        has_env = bool(os.environ.get("NOTION_TOKEN") and os.environ.get("NOTION_DB"))
        self.chk_notion = QCheckBox("Notion 데이터베이스에 업로드", self)
        self.chk_notion.setChecked(has_env)
        layout.addWidget(self.chk_notion)
        if not has_env:
            hint = QLabel("NOTION_TOKEN·NOTION_DB 환경변수 필요", self)
            hint.setStyleSheet("color: gray;")
            layout.addWidget(hint)

        layout.addWidget(QLabel("<b>실행 모드</b>", self))
        self.chk_dry = QCheckBox("미리보기만 — 아무것도 저장 안 함 (dry-run)", self)
        layout.addWidget(self.chk_dry)
        self.chk_rewrite = QCheckBox("챕터 정보 다시 쓰기 — 기존 페이지 본문 재작성", self)
        layout.addWidget(self.chk_rewrite)

        layout.addWidget(QLabel("<b>상태 초기화  ⚠ 주의</b>", self))
        self.chk_reset = QCheckBox("로컬 동기화 상태 비우기 (--reset)", self)
        layout.addWidget(self.chk_reset)
        self.chk_reset_notion = QCheckBox("Notion 동기화 상태 비우기 (--reset-notion)", self)
        layout.addWidget(self.chk_reset_notion)

        self.log = QPlainTextEdit(self)
        self.log.setReadOnly(True)
        layout.addWidget(self.log, stretch=1)

        btn_row = QHBoxLayout()
        self.status_label = QLabel("", self)
        btn_row.addWidget(self.status_label)
        btn_row.addStretch(1)
        self.run_btn = QPushButton("▶  실행", self)
        self.run_btn.clicked.connect(self._start_run)
        btn_row.addWidget(self.run_btn)
        self.close_btn = QPushButton("닫기", self)
        self.close_btn.clicked.connect(self._on_close_clicked)
        btn_row.addWidget(self.close_btn)
        layout.addLayout(btn_row)

    # -- 실행 --------------------------------------------------------------

    def _on_close_clicked(self) -> None:
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self._cancel()
        else:
            self.accept()

    def _set_running(self, running: bool) -> None:
        self.run_btn.setDisabled(running)
        self.close_btn.setText("■  중단" if running else "닫기")
        self.status_label.setText("동기화 중…" if running else "")

    def _all_book_stems(self) -> list[str]:
        return [b["stem"] for b in self.all_books]

    @staticmethod
    def _mask(cmd: list[str]) -> list[str]:
        out, skip = [], False
        for a in cmd:
            if skip:
                out.append("***")
                skip = False
                continue
            if a == "--notion-token":
                skip = True
            out.append(a)
        return out

    def _start_run(self) -> None:
        self.log.clear()

        dry = self.chk_dry.isChecked()
        file_o = self.chk_file.isChecked()
        notion = self.chk_notion.isChecked()
        reset = self.chk_reset.isChecked()
        reset_notion = self.chk_reset_notion.isChecked()
        rewrite = self.chk_rewrite.isChecked()

        if not (dry or file_o or notion):
            self._log("실행할 동작이 없습니다. dry-run / 파일 / Notion 중 하나 선택", _COLOR_BAD)
            return

        active = []
        if dry: active.append("dry-run")
        if file_o: active.append("파일저장")
        if notion: active.append("Notion업로드")
        if reset: active.append("로컬reset")
        if reset_notion: active.append("Notion-reset")
        if rewrite: active.append("챕터백필")
        self._log(f"모드: {' · '.join(active)}")
        if rewrite and not notion:
            self._log("챕터 백필은 Notion 업로드와 함께 써야 효과가 있습니다.", _COLOR_WARN)

        cmd = [
            sys.executable, "sync_kfx.py",
            "--kindle", str(self.kindle_root),
            "--no-progress",
        ]
        if dry:
            cmd.append("--dry-run")
        if file_o:
            path_str = self.file_path.text().strip()
            if not path_str:
                self._log("파일 경로가 비어 있습니다.", _COLOR_BAD)
                return
            out = Path(path_str).expanduser().resolve()
            try:
                out.parent.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                self._log(f"상위 디렉터리 생성 실패: {e}", _COLOR_BAD)
                return
            cmd += ["-o", str(out)]
            self._log(f"→ 파일 출력: {out}")
        if notion:
            tok = os.environ.get("NOTION_TOKEN", "")
            db = os.environ.get("NOTION_DB", "")
            if not tok or not db:
                self._log("NOTION_TOKEN / NOTION_DB 환경변수가 필요합니다.", _COLOR_BAD)
                return
            cmd += ["--notion-token", tok, "--notion-db", db]
        if reset:
            cmd.append("--reset")
        if reset_notion:
            cmd.append("--reset-notion")
        if rewrite:
            cmd.append("--rewrite-bodies")

        stems = [b["stem"] for b in self.scope_books]
        if 0 < len(stems) < len(self._all_book_stems()):
            for s in stems:
                cmd += ["--book", s]

        self._log(f"$ {' '.join(self._mask(cmd))}")

        proc = QProcess(self)
        proc.setWorkingDirectory(str(Path(__file__).resolve().parent.parent))
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        proc.setProcessEnvironment(env)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(self._on_output)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(
            lambda err: self._log(f"subprocess 실행 실패: {err}", _COLOR_BAD)
        )
        proc.start(cmd[0], cmd[1:])
        self.proc = proc
        self._set_running(True)

    def _log(self, text: str, color: str | None = None) -> None:
        safe = html.escape(text)
        if color:
            self.log.appendHtml(f'<span style="color:{color};">{safe}</span>')
        else:
            self.log.appendPlainText(text)
        self.log.moveCursor(QTextCursor.MoveOperation.End)

    def _cancel(self) -> None:
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.terminate()
            self._log("⊘ 중단 요청 — 프로세스 종료 중…", _COLOR_WARN)

    def _on_output(self) -> None:
        if not self.proc:
            return
        raw = bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in raw.splitlines():
            text = _ANSI_RE.sub("", line)
            if "\r" in text:
                text = text.rsplit("\r", 1)[-1]
            text = text.rstrip()
            if text:
                self._log(text, _line_color(text))

    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        if exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit:
            self._log("✓ 완료", _COLOR_DONE)
        elif exit_status == QProcess.ExitStatus.CrashExit:
            self._log("⊘ 중단됨", _COLOR_WARN)
        else:
            self._log(f"✗ 실패 (exit {exit_code})", _COLOR_BAD)
        self._set_running(False)

    def reject(self) -> None:
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.warning(self, "실행 중", "'중단' 버튼으로 멈춘 뒤 닫으세요.")
            return
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt 오버라이드
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.terminate()
        super().closeEvent(event)
