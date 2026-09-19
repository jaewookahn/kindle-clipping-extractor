#!/usr/bin/env python3
"""`Kindle Clippings.app` 번들 생성 (macOS).

## 왜 py2app/PyInstaller 를 쓰지 않나

이 앱은 **어차피 로컬 환경에 묶여 있다**:

- KFX 본문 추출은 Calibre 의 `KFX Input.zip` 플러그인을 런타임에 읽는다
  (`kindle/ebook.py`). 번들 안에 넣을 수 있는 물건이 아니다.
- 킨들은 MacDroid 가 `~/Library/CloudStorage/` 에 마운트해 준다.
- `.env` 의 토큰도 사용자 머신에 있다.

그래서 인터프리터까지 통째로 싸도 "어디서나 도는 앱"이 되지 않는다. 대신 이
저장소를 가리키는 **얇은 런처 번들**을 만든다. Dock·Launchpad·Spotlight 에서
아이콘으로 띄울 수 있고, 코드를 고치면 재빌드 없이 바로 반영된다.

    python tools/make_app.py              # dist/ 에 생성
    python tools/make_app.py --install    # 만든 뒤 /Applications 로 복사
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "Kindle Clippings"
BUNDLE_ID = "com.jaewookahn.kindle-clippings"
VERSION = "1.0.0"

_LAUNCHER = """#!/bin/bash
# {app} 런처 — 저장소의 gui.py 를 그 저장소를 cwd 로 두고 실행한다.
#
# cwd 가 중요하다: SyncDialog 가 동기화를 'python sync_kfx.py' 처럼 **상대 경로**로
# 띄우고, load_dotenv() 도 cwd 에서 .env 를 찾는다.
set -e
REPO="{repo}"
PY="{python}"

if [ ! -x "$PY" ]; then
    # 빌드 때 쓴 인터프리터가 사라졌으면 PATH 에서 찾는다.
    PY="$(command -v python3 || true)"
fi
if [ -z "$PY" ] || [ ! -d "$REPO" ]; then
    osascript -e 'display alert "Kindle Clippings" message "저장소 또는 파이썬을 찾지 못했습니다. tools/make_app.py 로 다시 빌드하세요."'
    exit 1
fi

cd "$REPO"
exec "$PY" gui.py "$@"
"""


def build(dest_dir: Path, python: str) -> Path:
    app = dest_dir / f"{APP_NAME}.app"
    if app.exists():
        shutil.rmtree(app)
    macos = app / "Contents" / "MacOS"
    res = app / "Contents" / "Resources"
    macos.mkdir(parents=True)
    res.mkdir(parents=True)

    launcher = macos / "run"
    launcher.write_text(_LAUNCHER.format(app=APP_NAME, repo=ROOT, python=python),
                        encoding="utf-8")
    launcher.chmod(0o755)

    icns = ROOT / "assets" / "icon.icns"
    icon_key = {}
    if icns.exists():
        shutil.copy2(icns, res / "icon.icns")
        icon_key = {"CFBundleIconFile": "icon"}
    else:
        print("assets/icon.icns 가 없습니다 — 먼저 tools/make_icon.py 를 실행하세요.",
              file=sys.stderr)

    info = {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleVersion": VERSION,
        "CFBundleShortVersionString": VERSION,
        "CFBundleExecutable": "run",
        "CFBundlePackageType": "APPL",
        "CFBundleInfoDictionaryVersion": "6.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        # 터미널 없이 GUI 로만 뜨게 한다
        "LSBackgroundOnly": False,
        "LSUIElement": False,
        **icon_key,
    }
    with open(app / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump(info, f)

    # Finder 가 아이콘 캐시를 갱신하도록 번들 mtime 을 건드린다
    os.utime(app, None)
    return app


def main() -> int:
    ap = argparse.ArgumentParser(description="Kindle Clippings.app 번들 생성")
    ap.add_argument("--dest", default=str(ROOT / "dist"), metavar="DIR",
                    help="번들을 만들 위치 (기본: dist/)")
    ap.add_argument("--python", default=sys.executable, metavar="PATH",
                    help="런처가 쓸 파이썬 (기본: 지금 실행 중인 인터프리터)")
    ap.add_argument("--install", action="store_true",
                    help="만든 뒤 /Applications 로 복사")
    args = ap.parse_args()

    dest = Path(args.dest).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    app = build(dest, args.python)
    print(f"생성: {app}")
    print(f"  저장소 : {ROOT}")
    print(f"  파이썬 : {args.python}")

    if args.install:
        target = Path("/Applications") / app.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(app, target, symlinks=True)
        subprocess.run(["touch", str(target)], check=False)
        print(f"설치: {target}")
    else:
        print("\n설치하려면:  python tools/make_app.py --install")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
