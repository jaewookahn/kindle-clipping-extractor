"""기기 아이콘 — 킨들 모델별 실루엣을 QPainter 로 직접 그린다.

사진을 번들하거나 내려받지 않는다. 아마존 제품 사진은 저작권이 있고, 외부
네트워크에 의존하면 오프라인에서 깨진다. 대신 모델별로 실제로 다른 특징
(화면비, 베젤, 스타일러스 유무, 컬러 여부)을 반영한 실루엣을 그린다 —
기기를 구분하는 데는 이걸로 충분하다.
"""

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap

# (가로:세로 비율, 스타일러스, 화면 색조)
# Scribe 는 10.2" 로 크고 펜을 쓴다. Colorsoft 는 컬러 e-ink.
_MODELS = {
    "scribe":      {"ratio": 0.74, "stylus": True,  "tint": "#f2f0ec"},
    "colorsoft":   {"ratio": 0.70, "stylus": False, "tint": "#eef3f7", "color": True},
    "oasis":       {"ratio": 0.72, "stylus": False, "tint": "#f2f0ec"},
    "paperwhite":  {"ratio": 0.68, "stylus": False, "tint": "#f2f0ec"},
    "voyage":      {"ratio": 0.68, "stylus": False, "tint": "#f2f0ec"},
    "basic":       {"ratio": 0.68, "stylus": False, "tint": "#f2f0ec"},
}
_DEFAULT = {"ratio": 0.70, "stylus": False, "tint": "#f2f0ec"}


def _spec(label: str) -> dict:
    low = (label or "").lower().replace(" ", "").replace("-", "")
    for key, spec in _MODELS.items():
        if key in low:
            return spec
    return _DEFAULT


def device_pixmap(label: str, height: int = 72) -> QPixmap:
    """기기 라벨에 맞는 킨들 실루엣 QPixmap 을 그려서 반환."""
    spec = _spec(label)
    width = max(24, int(height * spec["ratio"]))

    dpr = 2.0   # Retina 대응 — 실제 픽셀 2배로 그리고 논리 크기를 지정
    pix = QPixmap(int(width * dpr), int(height * dpr))
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.GlobalColor.transparent)

    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    body = QRectF(1, 1, width - 2, height - 2)
    radius = width * 0.09

    # 본체
    p.setPen(QPen(QColor("#8a8f98"), 1.2))
    p.setBrush(QBrush(QColor("#3f444c")))
    p.drawRoundedRect(body, radius, radius)

    # 화면 — Scribe 는 한쪽 베젤이 넓다(펜 그립)
    bezel = width * 0.07
    left_bezel = width * 0.20 if spec["stylus"] else bezel
    screen = QRectF(
        body.left() + left_bezel,
        body.top() + bezel,
        body.width() - left_bezel - bezel,
        body.height() - bezel * 2,
    )
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(spec["tint"])))
    p.drawRoundedRect(screen, radius * 0.4, radius * 0.4)

    # 화면 위 텍스트 라인 — 컬러 모델은 색 줄을 섞어 구분
    line_h = max(1.0, screen.height() * 0.030)
    gap = screen.height() * 0.075
    y = screen.top() + gap
    palette = ["#c96a6a", "#5f8fc9", "#5fa96b"] if spec.get("color") else None
    idx = 0
    while y + line_h < screen.bottom() - gap * 0.5:
        frac = 0.85 if idx % 3 != 2 else 0.55
        if palette:
            p.setBrush(QBrush(QColor(palette[idx % len(palette)])))
        else:
            p.setBrush(QBrush(QColor("#b9bcc2")))
        p.drawRect(QRectF(screen.left() + screen.width() * 0.10, y,
                          screen.width() * 0.80 * frac, line_h))
        y += gap
        idx += 1

    # 스타일러스 (Scribe)
    if spec["stylus"]:
        pen_x = body.left() + left_bezel * 0.42
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor("#d8dce1")))
        p.drawRoundedRect(
            QRectF(pen_x - width * 0.022, body.top() + body.height() * 0.16,
                   width * 0.044, body.height() * 0.68),
            width * 0.022, width * 0.022,
        )

    p.end()
    return pix
