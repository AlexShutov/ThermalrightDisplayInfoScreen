"""Qt (PySide6) linear-indicator dashboard renderer (MIT, lazy Qt).

Builds a gap-preserving vertical stack of horizontal bars from bridge gauge
``setup``/``values``/``palette`` and renders an offscreen frame as raw RGB bytes, then
paints each gauge's ``label``/``value`` text over it. Both the selection (focus) and the
value (progress) transitions are eased over time — the bars never jump. A focused bar
widens (``FOCUS_SCALE``) and reflows its neighbours, gets a glowing outline, and its drop
shadow snaps to the active outline colour (green by default, overridable via
``HARDWARESCREEN_FOCUS_COLOR``).

Rendering paints the bars directly into a standalone ``QImage`` with a ``QPainter`` — no
``QApplication`` and no ``QGraphicsScene`` — then hand the frame to Pillow for the label/
value text. QImage painting is reentrant and does not touch the GUI, and text needs no
Qt app, so the whole render is thread-safe inside the trcc daemon's native display loop.
``activity_linear_indicator`` (the Widgets submodule) is imported lazily, so importing
this module (and ``hardwarescreen``) does not require Qt; only the render path needs it.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
import time
from math import exp

from PIL import Image

from .dashboard import DEFAULT_SIZE, _palette, hex_to_rgb

_LF = None


def _ensure_qt():
    """Lazily import the focus-model constants (no QApplication needed)."""
    global _LF
    if _LF is None:
        from activity_linear_indicator.linear_focus import (
            FOCUS_SCALE,
            ease_out_cubic,
        )

        _LF = (FOCUS_SCALE, ease_out_cubic)
    return _LF


def _focus_color() -> tuple[int, int, int]:
    return hex_to_rgb(os.environ.get("HARDWARESCREEN_FOCUS_COLOR", "#00c85a"))


def _gap_preserving_positions(widths: list[float], gap: float) -> list[float]:
    """Top-anchored gap-preserving y-centers (first bar's top edge at 0)."""
    positions: list[float] = []
    acc = 0.0
    for w in widths:
        acc += w / 2.0
        positions.append(acc)
        acc += w / 2.0 + gap
    return positions


def _pil_font(size: int):
    try:
        from PIL import ImageFont

        return ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:
        return None


def _pil_text(d, s: str, x: int, y: int, size: int = 28,
              fill: tuple[int, int, int] = (238, 242, 248)) -> None:
    d.text((x, y), s, fill=fill, font=_pil_font(size))


class LinearDashboardRenderer:
    """Stateful offscreen renderer: eases focus (selection) and progress (value)."""

    def __init__(
        self,
        size: tuple[int, int] = DEFAULT_SIZE,
        *,
        background=None,
        fps: float = 15.0,
        focus_cycle_s: float = 2.0,
        focus_transition_s: float = 0.6,
        value_tween_s: float = 0.6,
        margin: int = 40,
        top: int = 96,
        footer: int = 40,
        gap: int = 16,
    ) -> None:
        (self._fscale, self._ease) = _ensure_qt()
        self._size: tuple[int, int] = (int(size[0]), int(size[1]))
        self._background = background
        self._fps = fps
        self._cycle = focus_cycle_s
        self._ftrans = focus_transition_s
        self._vtween = value_tween_s
        self._margin = margin
        self._top = top
        self._footer = footer
        self._gap = gap
        self._ids: list[str] = []
        self._t0 = time.monotonic()
        self._progress: list[float] = []
        self._last_time: dict[int, float] = {}

    def __call__(
        self,
        setup: list[dict],
        values: dict,
        palette: dict,
        size: tuple[int, int],
        background=None,
    ) -> bytes:
        return self.render(setup, values, palette, size, background)

    def _specs(self, setup: list[dict], values: dict, palette: dict) -> list[dict]:
        w, _ = self._size
        length = max(0, w - 2 * self._margin)
        specs: list[dict] = []
        for g in setup:
            gid = g.get("id", "")
            vmin = float(g.get("min", 0))
            vmax = float(g.get("max", 100))
            unit = g.get("unit", "")
            raw = values.get(gid)
            if not isinstance(raw, (int, float)):
                pct, text = 0.0, "n/a"
            else:
                denom = (vmax - vmin) if vmax > vmin else 0
                pct = ((raw - vmin) / denom * 100.0) if denom else 0.0
                pct = max(0.0, min(100.0, pct))
                text = f"{raw:.0f}{unit}"
            specs.append(
                {
                    "id": gid,
                    "label": g.get("label", gid),
                    "text": text,
                    "pct": pct,
                    "color": palette["accent"],
                    "length": length,
                }
            )
        return specs

    def render(
        self,
        setup: list[dict],
        values: dict,
        palette: dict,
        size: tuple[int, int],
        background=None,
    ) -> bytes:
        """Render one eased frame and return raw RGB bytes (``w*h*3``)."""
        self._size = (int(size[0]), int(size[1]))
        now = time.monotonic()
        t = now - self._t0
        pal = _palette(palette)
        specs = self._specs(setup or [], values or {}, pal)
        ids = [s["id"] for s in specs]
        if ids != self._ids:
            self._ids = ids
            self._progress = [0.0] * len(specs)
            self._last_time = {}

        n = len(specs)
        available = self._size[1] - self._top - self._footer
        gap = float(self._gap)
        base = max(4.0, (available - gap * (n - 1)) / n) if n else 1.0

        widths: list[float] = []
        for i, _ in enumerate(specs):
            f = self._focus_factor(t, i, n)
            widths.append(base * (1.0 + f * (self._fscale - 1.0)))
        # Keep the expanded (focused) stack within the available region so a bar's
        # back edge never intrudes into the footer / off the panel edge.
        total = sum(widths) + gap * (n - 1)
        if n and total > available > 0:
            fit = available / total
            widths = [w * fit for w in widths]
        positions = _gap_preserving_positions(widths, gap)

        progress: list[float] = []
        for i, spec in enumerate(specs):
            progress.append(self._eased_progress(i, spec["pct"] / 100.0, now))

        from PySide6.QtGui import QPainter

        img = self._make_image(pal, background)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        focus_color = _focus_color()
        cx = self._size[0] / 2.0
        for i, spec in enumerate(specs):
            cy = positions[i] + self._top
            self._paint_bar(
                p,
                cx,
                cy,
                spec["length"],
                widths[i],
                progress[i],
                self._focus_factor(t, i, n),
                spec["color"],
                focus_color,
            )
        p.end()

        pil = self._qimage_to_pil(img)
        if specs:
            self._paint_text_pil(pil, specs, positions, pal["text"])
        else:
            from PIL import ImageDraw

            d = ImageDraw.Draw(pil)
            _pil_text(d, "no data", self._size[0] // 2 - 34,
                      (self._size[1] - self._footer) // 2, size=30, fill=pal["text"])
            d.line(
                [(self._size[0] // 2 - 51, (self._size[1] - self._footer) // 2 + 28),
                 (self._size[0] // 2 + 51, (self._size[1] - self._footer) // 2 + 28)],
                fill=pal["muted"], width=2,
            )
        return pil.tobytes()

    def _make_image(self, palette: dict, background=None):
        from PySide6.QtGui import QColor, QImage

        w, h = self._size
        bg = self._background if background is None else background
        if bg is None:
            img = QImage(w, h, QImage.Format_RGB888)
            img.fill(
                QColor(
                    int(palette["background"][0]),
                    int(palette["background"][1]),
                    int(palette["background"][2]),
                )
            )
            return img
        from PIL import ImageOps

        rgb = bg.convert("RGB")
        if rgb.size != (w, h):
            rgb = ImageOps.fit(rgb, (w, h))
        raw = rgb.tobytes()
        return QImage(raw, w, h, w * 3, QImage.Format_RGB888)

    def _paint_bar(
        self,
        p,
        cx: float,
        cy: float,
        length: float,
        t: float,
        fr: float,
        f: float,
        color,
        focus_color,
    ) -> None:
        from PySide6.QtCore import QRectF, Qt
        from PySide6.QtGui import QBrush, QColor, QLinearGradient

        color = QColor(*color)
        left = cx - length / 2.0
        top = cy - t / 2.0
        radius = t / 2.0
        track = QRectF(left, top, length, t)
        fill = QRectF(left, top, length * max(0.0, min(fr, 1.0)), t)

        # Soft drop shadow (layered, offset down).
        p.setPen(Qt.NoPen)
        for spread, alpha in ((0.0, 55), (6, 36), (12, 22), (18, 12), (24, 6)):
            c = QColor(0, 0, 0)
            c.setAlpha(alpha)
            p.setBrush(c)
            x = left - spread
            y = top - spread + 8
            p.drawRoundedRect(
                QRectF(x, y, length + 2 * spread, t + 2 * spread),
                (t + 2 * spread) / 2.0,
                (t + 2 * spread) / 2.0,
            )

        # Backdrop track (low-alpha unfilled band).
        bcol = QColor(color)
        bcol.setAlpha(38)
        p.setBrush(QBrush(bcol))
        p.drawRoundedRect(track, radius, radius)

        # Fill (gradient colour) with rounded caps.
        if fill.width() > 0:
            g = QLinearGradient(left, 0, left + length, 0)
            g.setColorAt(0.0, color.lighter(118))
            g.setColorAt(0.12, QColor(color))
            g.setColorAt(0.85, color.darker(96))
            g.setColorAt(1.0, color.lighter(118))
            p.setBrush(QBrush(g))
            p.drawRoundedRect(fill, radius, radius)

        # Selection: glowing outline in the active outline colour, faded by focus level.
        if f > 0.0 and fill.width() > 0:
            self._paint_glow(p, fill, radius, focus_color, t, f)

    @staticmethod
    def _paint_glow(p, rect, radius: float, focus_color, t: float, f: float) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPen

        focus_color = QColor(*focus_color)
        core = max(1.5, t * 0.03)
        for mult, alpha in ((7.0, 22), (4.5, 40), (2.8, 62)):
            c = QColor(focus_color)
            c.setAlpha(int(alpha * f))
            pen = QPen(c, core * mult)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect, radius, radius)
        fc = QColor(focus_color)
        fc.setAlpha(int(255 * f))
        g = QLinearGradient(rect.left(), 0, rect.right(), 0)
        g.setColorAt(0.0, fc.lighter(140))
        g.setColorAt(0.12, fc)
        g.setColorAt(0.85, fc.darker(130))
        g.setColorAt(1.0, fc.lighter(140))
        pen = QPen(QBrush(g), core)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.drawRoundedRect(rect, radius, radius)

    @staticmethod
    def _qimage_to_pil(img) -> Image.Image:
        from PySide6.QtGui import QImage

        img = img.convertToFormat(QImage.Format_RGB888)
        w, h = img.width(), img.height()
        ptr = img.constBits()
        raw = ptr.tobytes() if hasattr(ptr, "tobytes") else bytes(ptr)
        stride = img.bytesPerLine()
        return Image.frombuffer("RGB", (w, h), raw, "raw", "RGB", stride, 1)

    def _paint_text_pil(self, img: Image.Image, specs: list[dict], positions,
                        text_color: tuple[int, int, int]) -> None:
        from PIL import ImageDraw

        d = ImageDraw.Draw(img)
        for spec, pos in zip(specs, positions, strict=True):
            cy = int(pos + self._top)
            length = spec["length"]
            left = int((self._size[0] - length) / 2.0)
            _pil_text(d, spec["label"], left + 14, cy - 16, size=26, fill=text_color)
            font = _pil_font(26)
            value_w = d.textlength(spec["text"], font=font)
            _pil_text(d, spec["text"], left + int(length) - 14 - int(value_w),
                      cy - 16, size=26, fill=text_color)

    def _focus_factor(self, t: float, i: int, n: int) -> float:
        if n == 0:
            return 0.0
        cycle = self._cycle
        ts = self._ftrans
        period = cycle * n
        seg = (t - i * cycle) % period
        if seg <= ts:
            return self._ease(seg / ts)
        if seg <= cycle:
            return 1.0
        if seg <= cycle + ts:
            return 1.0 - self._ease((seg - cycle) / ts)
        return 0.0

    def _eased_progress(self, idx: int, target: float, now: float) -> float:
        if idx >= len(self._progress):
            self._progress.extend([0.0] * (idx + 1 - len(self._progress)))
        prev = self._progress[idx]
        if self._vtween <= 0:
            value = target
        else:
            dt = now - self._last_time.get(idx, now)
            alpha = 1.0 - exp(-max(0.0, dt) / self._vtween)
            value = prev + (target - prev) * alpha
        self._progress[idx] = value
        self._last_time[idx] = now
        return value

    def close(self) -> None:
        self._background = None
