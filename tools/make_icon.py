#!/usr/bin/env python3
"""앱 아이콘 생성 — assets/icon.png + assets/icon.icns.

Pillow 로 1024px 원본을 그리고 `iconutil` 로 .icns 로 묶는다. 외부 디자인 파일
없이 코드로만 만들기 때문에 색·비율을 바꾸려면 아래 상수만 고치면 된다.

작게 줄었을 때(16px) 뭉개지지 않도록 요소를 셋으로만 제한했다:
어두운 배경 사각형 / 흰 페이지 / 노란 하이라이트 획. 텍스트 줄은 장식이라
16px 에서 사라져도 실루엣은 그대로 읽힌다.

    python tools/make_icon.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

S = 1024                      # 원본 한 변
BG = (28, 34, 44, 255)        # 배경 (짙은 남색 계열)
BG_EDGE = (44, 54, 68, 255)   # 배경 테두리 하이라이트
PAPER = (250, 249, 246, 255)  # 페이지 (살짝 따뜻한 흰색)
LINE = (176, 184, 196, 255)   # 본문 줄
LINE_DIM = (206, 212, 221, 255)
MARK = (255, 199, 44, 255)    # 하이라이트 노랑
MARK_EDGE = (240, 176, 16, 255)

# macOS 아이콘은 가장자리에 여백을 두는 것이 관례 (squircle 안쪽에 내용)
PAD = int(S * 0.085)
RADIUS = int(S * 0.225)

_ICNS_SIZES = [16, 32, 64, 128, 256, 512, 1024]


def _rounded(draw: ImageDraw.ImageDraw, box, radius, fill, outline=None, width=0):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def render(size: int = S) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    k = size / S                      # 1024 기준 좌표를 임의 크기로 환산

    def u(v: float) -> int:
        return int(round(v * k))

    # 배경 squircle
    _rounded(d, (u(PAD), u(PAD), size - u(PAD), size - u(PAD)),
             u(RADIUS), BG, outline=BG_EDGE, width=u(6))

    # 페이지 — 세로로 긴 직사각형, 좌우 여백을 넉넉히
    px0, py0 = u(285), u(215)
    px1, py1 = size - u(285), size - u(215)
    _rounded(d, (px0, py0, px1, py1), u(26), PAPER)

    # 본문 줄 — 가운데 하이라이트 줄을 비워두고 위아래로 배치
    line_x0, line_x1 = px0 + u(58), px1 - u(58)
    line_h = u(26)
    gap = u(62)
    top = py0 + u(86)
    for i in range(3):
        y = top + i * gap
        x1 = line_x1 if i != 2 else line_x1 - u(120)   # 마지막 줄은 짧게
        _rounded(d, (line_x0, y, x1, y + line_h), line_h // 2, LINE_DIM)

    # 하이라이트 획 — 이 아이콘의 핵심. 페이지 밖으로 살짝 삐져나가게 그려
    # "칠했다"는 인상을 준다.
    hy = top + 3 * gap + u(16)
    hh = u(78)
    _rounded(d, (px0 - u(22), hy, px1 - u(96), hy + hh), hh // 2, MARK)
    _rounded(d, (px0 - u(22), hy, px1 - u(96), hy + hh), hh // 2,
             None, outline=MARK_EDGE, width=max(1, u(5)))

    # 하이라이트 아래 남은 본문 줄
    bottom = hy + hh + u(46)
    for i in range(2):
        y = bottom + i * gap
        x1 = line_x1 if i == 0 else line_x1 - u(210)
        _rounded(d, (line_x0, y, x1, y + line_h), line_h // 2, LINE)

    return img


def build_icns(png: Path, out: Path) -> bool:
    """iconutil 로 .icns 생성. 실패하면 False (PNG 는 그대로 남는다)."""
    if not shutil.which("iconutil"):
        print("iconutil 이 없어 .icns 를 건너뜁니다 (macOS 전용).", file=sys.stderr)
        return False
    src = Image.open(png)
    with tempfile.TemporaryDirectory() as td:
        iconset = Path(td) / "icon.iconset"
        iconset.mkdir()
        for s in _ICNS_SIZES:
            src.resize((s, s), Image.LANCZOS).save(iconset / f"icon_{s}x{s}.png")
            if s * 2 <= 1024:
                src.resize((s * 2, s * 2), Image.LANCZOS).save(
                    iconset / f"icon_{s}x{s}@2x.png")
        r = subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"iconutil 실패: {r.stderr.strip()}", file=sys.stderr)
            return False
    return True


def main() -> int:
    ASSETS.mkdir(exist_ok=True)
    png = ASSETS / "icon.png"
    render().save(png)
    print(f"생성: {png}")
    icns = ASSETS / "icon.icns"
    if build_icns(png, icns):
        print(f"생성: {icns}  ({icns.stat().st_size:,} bytes)")
    # 육안 확인용 작은 미리보기
    prev = ASSETS / "icon_preview.png"
    src = Image.open(png)
    sizes = [128, 64, 32, 16]
    strip = Image.new("RGBA", (sum(sizes) + 20 * len(sizes), 140), (255, 255, 255, 0))
    x = 0
    for s in sizes:
        strip.paste(src.resize((s, s), Image.LANCZOS), (x, (140 - s) // 2))
        x += s + 20
    strip.save(prev)
    print(f"생성: {prev} (크기별 미리보기)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
