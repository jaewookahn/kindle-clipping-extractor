"""kindle_gui — TUI(tui.py, Textual)의 macOS 네이티브 GUI 대응.

kindle/ 패키지의 백엔드 로직(kfxlib 로딩, YJR 파싱, Notion REST, 표지 조회)을
그대로 in-process 로 import 해서 쓴다. 동기화 실행만 sync_kfx.py 를
subprocess(QProcess) 로 부른다 — TUI 도 원래 그렇게 하고 있었다.
"""
