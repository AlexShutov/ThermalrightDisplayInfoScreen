"""Qt (PySide6) linear-indicator dashboard renderer (MIT, lazy Qt).

Builds a vertical stack of full-width horizontal bars from bridge gauge
``setup``/``values``/``palette`` and renders an offscreen frame as raw RGB bytes, then
paints each gauge's ``label``/``value`` text over it.

Bar geometry (size + position) is delegated to a ``BaseLayoutManager`` — like Android's
layout manager, a separate object measures the items inside the panel so the renderer
only draws at the returned rects.  The selection layout (``is_selection_enabled``)
grows the focused bar in thickness while the others keep their size, sizing the stack
so it never overflows and an extreme bar keeps ~2 mm to the edge; a static layout
(``is_selection_enabled=False``) draws equal-height bars.  The highlight (green
outline + bounded under-glow) wraps the ACTIVE (filled) part of the bar only.

Both the selection (focus) and the value (progress) transitions are eased over time.
Rendering paints the bars directly into a standalone ``QImage`` with a ``QPainter`` —
no ``QApplication`` and no ``QGraphicsScene`` — then hands the frame to Pillow for the
label/value text.  QImage painting is reentrant and does not touch the GUI, and text
needs no Qt app, so the whole render is thread-safe inside the trcc daemon's native
display loop.  ``activity_linear_indicator`` (the Widgets submodule) is imported
lazily, so importing this module (and ``hardwarescreen``) does not require Qt.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
from math import exp

from PIL import Image

from .dashboard import DEFAULT_SIZE, _palette, hex_to_rgb
from .layout import BaseLayoutManager, SelectionLayoutManager

_LF = None


def _ensure_qt():
    """Lazily import the focus easing (no QApplication needed)."""
    global _LF
    if _LF is None:
        from activity_linear_indicator.linear_focus import ease_out_cubic

        _LF = ease_out_cubic
    return _LF


def _focus_color() -> tuple[int, int, int]:
    return hex_to_rgb(os.environ.get("HARDWARESCREEN_FOCUS_COLOR", "#00c85a"))


def _pil_font(size: int):
    try:
        from PIL import ImageFont

        return ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:
        return None


def _pil_text(d, s: str, x: int, y: int, size: int = 26,
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
        layout: BaseLayoutManager | None = None,
    ) -> None:
        self._ease = _ensure_qt()
        self._size: tuple[int, int] = (int(size[0]), int(size[1]))
        self._background = background
        self._fps = fps
        self._cycle = focus_cycle_s
        self._ftrans = focus_transition_s
        self._vtween = value_tween_s
        self._layout = layout if layout is not None else SelectionLayoutManager()
        self._ids: list[str] = []
        self._anim_t = 0.0
        self._anim_step = 1.0 / max(1.0, fps)
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
        # Advance the animation clock by one step per RENDER, not per wall-clock
        # tick: the caller re-renders only on data change (+keepalive), so a frame
        # stays byte-identical between changes and trcc never floods the panel
        # with a new (churning) frame every tick.  This keeps the dashboard
        # reactive (AGENTS) while still easing the selection/value on change.
        self._anim_t += self._anim_step
        now = self._anim_t
        t = self._anim_t
        pal = _palette(palette)
        specs = self._specs(setup or [], values or {}, pal)
        ids = [s["id"] for s in specs]
        if ids != self._ids:
            self._ids = ids
            self._progress = [0.0] * len(specs)
            self._last_time = {}

        n = len(specs)
        focus = [self._focus_factor(t, i, n) for i in range(n)]
        # The selection layout grows the focused bar; a static layout ignores this.
        layout = self._layout.measure(
            self._size[0],
            self._size[1],
            n,
            focus if self._layout.is_selection_enabled else None,
        )
        progress: list[float] = []
        for i, spec in enumerate(specs):
            progress.append(self._eased_progress(i, spec["pct"] / 100.0, now))

        from PySide6.QtGui import QPainter

        img = self._make_image(pal, background)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        focus_color = _focus_color()
        for i, spec in enumerate(specs):
            f = self._focus_factor(t, i, n)
            self._paint_bar(p, layout[i], progress[i], f, spec["color"], focus_color)
        p.end()

        pil = self._qimage_to_pil(img)
        if specs:
            self._paint_text_pil(pil, specs, layout, pal["text"])
        else:
            from PIL import ImageDraw

            d = ImageDraw.Draw(pil)
            _pil_text(d, "no data", self._size[0] // 2 - 34,
                      (self._size[1] - 34) // 2, size=30, fill=pal["text"])
            d.line(
                [(self._size[0] // 2 - 51, (self._size[1] - 34) // 2 + 28),
                 (self._size[0] // 2 + 51, (self._size[1] - 34) // 2 + 28)],
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

    def _paint_bar(self, p, geo, fr: float, f: float, color, focus_color) -> None:
        from PySide6.QtCore import QRectF, Qt
        from PySide6.QtGui import QBrush, QColor, QLinearGradient

        color = QColor(*color)
        left = geo.x
        top = geo.y
        length = geo.width
        t = geo.height
        radius = t / 2.0
        track = QRectF(left, top, length, t)
        fill = QRectF(left, top, length * max(0.0, min(fr, 1.0)), t)
        focus = f > 0.0

        # Under-glow beneath the ACTIVE (filled) part only: accent when idle,
        # green when selected.  Never spans the empty track, so the highlight
        # tracks the value rather than painting a full-width band.
        if fill.width() > 0:
            glow_color = focus_color if focus else (color.red(), color.green(), color.blue())
            self._under_glow(
                p,
                fill,
                glow_color,
                t,
                f if focus else 0.4,
                bloom_px=22.0 if focus else 12.0,
                alpha_base=56 if focus else 30,
            )

        # Backdrop track (low-alpha unfilled band).
        bcol = QColor(color)
        bcol.setAlpha(38)
        p.setBrush(QBrush(bcol))
        p.setPen(Qt.NoPen)
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

        # Selection: green glowing outline around the ACTIVE (filled) part only.
        if focus and fill.width() > 0:
            self._paint_outline(p, fill, focus_color, radius, f)

    @staticmethod
    def _paint_outline(p, rect, focus_color, radius: float, f: float) -> None:
        """Green glowing outline around the focused bar (soft glow + bright core)."""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QPen

        c0 = QColor(*focus_color)
        p.setBrush(Qt.NoBrush)
        core = max(2.2, rect.height() * 0.05)
        for mult, alpha in ((5.0, 26), (2.8, 60), (1.5, 120)):
            c = QColor(c0)
            c.setAlpha(int(alpha * f))
            pen = QPen(c, core * mult)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.drawRoundedRect(rect, radius, radius)
        c = QColor(c0)
        c.setAlpha(int(255 * f))
        pen = QPen(c, core)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.drawRoundedRect(rect, radius, radius)

    @staticmethod
    def _under_glow(p, rect, color, t: float, strength: float, *,
                    bloom_px: float = 16.0, alpha_base: int = 30,
                    seam_alpha: int = 0) -> None:
        """Soft, bounded light blooming DOWNWARD from ``rect``'s bottom edge.

        Thin horizontal layers drop a small ``bloom_px`` below the bar, narrowing
        and fading with depth, and (when ``seam_alpha`` is set) a thin bright
        strip hugs the lower edge.  ``strength`` (0..1) scales the alpha so the
        glow fades in/out with the focus level.  Sized small on purpose — it must
        read as a light cast under the bar, not a blob that swallows the bar.
        """
        from PySide6.QtCore import QRectF, Qt
        from PySide6.QtGui import QColor

        c0 = QColor(*color)
        bottom = rect.bottom()
        cx = rect.center().x()
        hw0 = rect.width() / 2.0
        p.setPen(Qt.NoPen)
        layers = 9
        for i in range(layers):
            depth = 2.0 + i * (bloom_px / layers)
            grow = 16.0 - i * (16.0 / (layers + 1))
            c = QColor(c0)
            c.setAlpha(int(alpha_base * strength * (1.0 - i / (layers + 1))))
            p.setBrush(c)
            lh = 3.0
            w = 2.0 * (hw0 + grow)
            p.drawRoundedRect(
                QRectF(cx - hw0 - grow, bottom + depth, w, lh),
                lh / 2.0,
                lh / 2.0,
            )
        if seam_alpha > 0:
            core = max(1.2, t * 0.04)
            c = QColor(c0)
            c.setAlpha(int(seam_alpha * strength))
            p.setBrush(c)
            p.drawRoundedRect(
                QRectF(rect.left(), bottom, rect.width(), core),
                core / 2.0,
                core / 2.0,
            )

    @staticmethod
    def _qimage_to_pil(img) -> Image.Image:
        from PySide6.QtGui import QImage

        img = img.convertToFormat(QImage.Format_RGB888)
        w, h = img.width(), img.height()
        ptr = img.constBits()
        raw = ptr.tobytes() if hasattr(ptr, "tobytes") else bytes(ptr)
        stride = img.bytesPerLine()
        return Image.frombuffer("RGB", (w, h), raw, "raw", "RGB", stride, 1)

    def _paint_text_pil(self, img: Image.Image, specs: list[dict],
                        layout, text_color: tuple[int, int, int]) -> None:
        from PIL import ImageDraw

        d = ImageDraw.Draw(img)
        for spec, geo in zip(specs, layout.geometry, strict=True):
            cy = int(geo.center_y)
            left = int(geo.x)
            length = int(geo.width)
            _pil_text(d, spec["label"], left + 14, cy - 16, size=26, fill=text_color)
            font = _pil_font(26)
            value_w = d.textlength(spec["text"], font=font)
            _pil_text(d, spec["text"], left + length - 14 - int(value_w),
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
