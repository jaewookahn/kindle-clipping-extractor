#!/usr/bin/env python3
"""gui.py — Kindle 클리핑 동기화 맥 GUI 앱 (PyQt6).

tui.py(Textual)의 GUI 대응. 백엔드(kindle/ 패키지)는 완전히 공유하고,
동기화 실행은 sync_kfx.py 를 subprocess 로 그대로 부른다.

사용법:
    python gui.py                                    # 자동 감지
    python gui.py --kindle "/path/Internal Storage"  # 경로 직접 지정
"""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from PyQt6.QtWidgets import QApplication

from kindle_gui.main_window import MainWindow


def main() -> None:
    parser = argparse.ArgumentParser(description="Kindle 클리핑 동기화 GUI")
    parser.add_argument("--kindle", default=None, metavar="PATH",
                        help="킨들 마운트 경로 (생략 시 자동 감지)")
    args = parser.parse_args()

    root = Path(args.kindle) if args.kindle else None
    if root is not None and not root.exists():
        print(f"오류: {root} 가 존재하지 않습니다.", file=sys.stderr)
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName("Kindle Clipping Extractor")
    window = MainWindow(kindle_root=root)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
