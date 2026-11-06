"""Qt (PySide6) linear-indicator dashboard renderer (MIT, lazy Qt).

Builds a vertical stack of full-width horizontal bars from bridge gauge
``setup``/``values``/``palette`` and renders an offscreen frame as raw RGB bytes, then
paints each gauge's ``label``/``value`` text over it.

Bar geometry (size + position) is delegated to a ``BaseLayoutManager`` — like Android's
layout manager, a separate object measures the items inside the panel so the renderer
only draws at the returned rects.  The measured geometry is always constant (every
bar the same height, the same uniform gap) regardless of selection; the selection
layout (``is_selection_enabled``) is a *render-side* effect: the non-selected bars are
dimmed, the selected bar grows in thickness, is pushed onto the foreground through
the overlay z-order, and gets a green glow.  Bars are painted with a ``QPainter`` in
increasing z-order (dimmed neighbours first, the selected bar last on top), and the
green glow is a symmetric layered halo drawn behind the whole selected bar — fixing
the old lopsided under-glow that was clipped to the filled part.

Both the selection (focus) and the value (progress) transitions are eased over time.
Rendering paints the bars into a standalone ``QImage`` with a ``QPainter`` — no
``QApplication`` and no ``QGraphicsScene`` — then hands the frame to Pillow for the
label/value text.  QImage painting is reentrant and does not touch the GUI, so the
whole render is thread-safe inside the trcc daemon's native display loop.  Qt is
imported lazily, so importing this module (and ``hardwarescreen``) does not require Qt.
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
        # The measured geometry is always constant (equal height + gap); selection
        # is a render-side effect (dim the others, grow + foreground the selected).
        layout = self._layout.measure(
            self._size[0],
            self._size[1],
            n,
            focus if self._layout.is_selection_enabled else None,
        )
        progress: list[float] = []
        for i, spec in enumerate(specs):
            progress.append(self._eased_progress(i, spec["pct"] / 100.0, now))

        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter

        img = self._make_image(pal, background)
        selection_on = self._layout.is_selection_enabled
        active = self._active_index(focus, selection_on)
        focus_color = _focus_color()

        # Grow the selected bar around its centre; non-selected bars keep the
        # constant layout height, so all columns stay the same size when idle.
        # Clamp the grown rect into the panel margins so the selected bar never
        # overflows the panel at low gauge counts (it can only grow so tall).
        panel_h = self._size[1]
        margin = self._layout.edge_margin
        rects: list[QRectF] = []
        for i, geo in enumerate(layout.geometry):
            f = focus[i] if selection_on else 0.0
            scale = (1.0 + (self._layout.selection_scale - 1.0) * f) if selection_on else 1.0
            h = geo.height * scale
            cy = geo.center_y
            top = max(float(margin), min(float(panel_h) - float(margin), cy - h / 2.0))
            bottom = max(top, min(float(panel_h) - float(margin), cy + h / 2.0))
            rects.append(QRectF(geo.x, top, geo.width, max(0.0, bottom - top)))

        # Draw in increasing z-order: dimmed neighbours first, the selected bar (with
        # its green glow) last, on top — the correct overlay z-buffer ordering.
        order = sorted(
            range(n),
            key=lambda i: 100.0 if i == active else 1.0 + 2.0 * (focus[i] if selection_on else 0.0),
        )
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        for i in order:
            f = focus[i] if selection_on else 0.0
            # Невыделенные бары (f=0) гаснут в затемнение, выделенный (f=1) — яркий.
            dim = (0.35 + 0.65 * f) if selection_on else 1.0
            self._paint_bar(p, rects[i], progress[i], specs[i]["color"], dim)
        p.end()

        pil = self._qimage_to_pil(img)
        # Плавная зелёная тень ТОЛЬКО наружу: гауссово-размытый силуэт выделенного
        # бара, из которого вырезан собственный footprint, — свет расходится ровно
        # вокруг бара и не тонирует сам бар.
        if selection_on:
            for i in range(n):
                f = focus[i] if i < len(focus) else 0.0
                if f > 0.0:
                    self._paint_glow_pil(pil, rects[i], focus_color, f)
        if specs:
            self._paint_text_pil(
                pil, specs, layout, pal["text"],
                focus if selection_on else None, pal["background"],
            )
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
                        layout, text_color: tuple[int, int, int],
                        focus: list[float] | None = None,
                        bg: tuple[int, int, int] = (0, 0, 0)) -> None:
        from PIL import ImageDraw

        d = ImageDraw.Draw(img)
        for idx, (spec, geo) in enumerate(zip(specs, layout.geometry, strict=True)):
            if focus is not None:
                # Non-selected labels dim with their bar; the selected label stays bright.
                b = 0.35 + 0.65 * (focus[idx] if idx < len(focus) else 0.0)
            else:
                b = 1.0
            # Blend the label toward the background so it dims with its bar.
            col = (
                int(round(text_color[0] * b + bg[0] * (1.0 - b))),
                int(round(text_color[1] * b + bg[1] * (1.0 - b))),
                int(round(text_color[2] * b + bg[2] * (1.0 - b))),
            )
            cy = int(geo.center_y)
            left = int(geo.x)
            length = int(geo.width)
            _pil_text(d, spec["label"], left + 14, cy - 16, size=26, fill=col)
            font = _pil_font(26)
            value_w = d.textlength(spec["text"], font=font)
            _pil_text(d, spec["text"], left + length - 14 - int(value_w),
                      cy - 16, size=26, fill=col)

    @staticmethod
    def _paint_glow_pil(img, rect, focus_color, strength: float) -> None:
        """Накладывает плавную зелёную тень ТОЛЬКО наружу ``rect`` на ``img``.

        Рисует силуэт бара (скруглённый прямоугольник), размывает его гауссовым
        блюром (настоящий плавный градиент, без дискретных колец), затем вырезает
        из размытой маски собственный footprint бара, чтобы свет расходился только
        вне бара и не тонировал заливку.  ``strength`` (0..1) = уровень фокуса.
        """
        from PIL import Image, ImageDraw, ImageFilter

        left, t = int(rect.x()), int(rect.y())
        w, h = int(rect.width()), int(rect.height())
        if w <= 0 or h <= 0:
            return
        margin = 48
        bx0 = max(0, left - margin)
        by0 = max(0, t - margin)
        bx1 = min(img.width, left + w + margin)
        by1 = min(img.height, t + h + margin)
        bw = bx1 - bx0
        bh = by1 - by0
        if bw <= 0 or bh <= 0:
            return
        radius = max(2.0, h / 2.0)
        sil = Image.new("L", (bw, bh), 0)
        ImageDraw.Draw(sil).rounded_rectangle(
            [left - bx0, t - by0, left - bx0 + w, t - by0 + h], radius=radius, fill=255
        )
        blurred = sil.filter(ImageFilter.GaussianBlur(16))
        # 1 - footprint: тень равен нулю ровно там, где стоит бар (только наружу).
        inv = sil.point(lambda v: 255 - v)
        mask = Image.composite(
            blurred, Image.new("L", (bw, bh), 0), inv
        ).point(lambda v: int(v * min(1.0, max(0.0, strength))))
        glow = Image.new("RGBA", (bw, bh), (*focus_color, 0))
        glow.putalpha(mask)
        img.paste(glow.convert("RGB"), (bx0, by0), mask)

    @staticmethod
    def _paint_bar(p, rect, fill_frac: float, color, dim: float) -> None:
        """Draw one bar (low-alpha track + gradient fill), dimmed by ``dim`` (0..1)."""
        from PySide6.QtCore import QRectF, Qt
        from PySide6.QtGui import QBrush, QColor, QLinearGradient

        t = rect.height()
        radius = max(2.0, t / 2.0)
        base = QColor(*color)
        p.setPen(Qt.NoPen)
        p.setOpacity(max(0.0, min(1.0, dim)))
        # Backdrop track (low-alpha unfilled band).
        bcol = QColor(base)
        bcol.setAlpha(40)
        p.setBrush(bcol)
        p.drawRoundedRect(rect, radius, radius)
        # Fill (gradient colour) with rounded caps, up to the value fraction.
        frac = max(0.0, min(1.0, fill_frac))
        if frac > 0:
            fill = QRectF(rect.x(), rect.y(), rect.width() * frac, rect.height())
            g = QLinearGradient(rect.x(), 0.0, rect.x() + rect.width(), 0.0)
            g.setColorAt(0.0, base.lighter(118))
            g.setColorAt(0.12, QColor(base))
            g.setColorAt(0.85, base.darker(96))
            g.setColorAt(1.0, base.lighter(118))
            p.setBrush(QBrush(g))
            p.drawRoundedRect(fill, radius, radius)
        p.setOpacity(1.0)

    def _active_index(self, focus: list[float], selection_on: bool):
        """Index of the currently selected bar (the one with the highest focus)."""
        if not selection_on or not focus:
            return None
        peak = max(focus)
        return focus.index(peak) if peak > 1e-6 else None

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
