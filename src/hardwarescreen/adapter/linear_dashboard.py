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
the overlay z-order, and gets a green glow.  Selection changes TOGETHER WITH the data:
each new data window (a change in setup/values/palette) alternates "every other time"
— one window highlights a random bar, the next skips — and the highlight is held for
the whole window (~5 s) instead of blipping.  While no bar is highlighted every gauge
is at full brightness, so the indicators are always visible.  Bars are painted with a
``QPainter`` in increasing z-order (dimmed neighbours first, the selected bar last on
top), and the glow is a smooth gaussian halo that radiates only OUTWARD from the
selected bar — the silhouette is blurred and then carved back to its own footprint so
the light never tints the fill (fixes the old lopsided under-glow that was clipped to
the bar).

The value (progress) transition runs from the previous value to the new one in
``VALUE_TWEEN_S`` (0.3 s) — the bar "runs" to its new state quickly, then holds it
for the rest of the ~5 s data window; the selection is keyed to the data change
itself, so it moves in step with the values.
Rendering paints the bars into a standalone ``QImage`` with a ``QPainter`` — no
``QApplication`` and no ``QGraphicsScene`` — then hands the frame to Pillow for the
label/value text.  QImage painting is reentrant and does not touch the GUI, so the
whole render is thread-safe inside the trcc daemon's native display loop.  Qt is
imported lazily, so importing this module (and ``hardwarescreen``) does not require Qt.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
import random
import time

from PIL import Image

from .dashboard import DEFAULT_SIZE, _palette, hex_to_rgb
from .layout import BaseLayoutManager, SelectionLayoutManager

_LF = None


def _fallback_ease_out_cubic(t: float) -> float:
    """Кубическое ease-out, если сабмодуль ``activity_linear_indicator`` недоступен."""
    return 1.0 - (1.0 - t) ** 3


def _ensure_qt():
    """Lazily import the focus easing (no QApplication needed)."""
    global _LF
    if _LF is None:
        try:
            from activity_linear_indicator.linear_focus import ease_out_cubic

            _LF = ease_out_cubic
        except ImportError:
            _LF = _fallback_ease_out_cubic
    return _LF


def _focus_color() -> tuple[int, int, int]:
    return hex_to_rgb(os.environ.get("HARDWARESCREEN_FOCUS_COLOR", "#00c85a"))


def _pil_font(size: int):
    try:
        from PIL import ImageFont

        return ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:
        return None


def _pil_text(d, s: str, x: int, y: int, size=None,
              fill: tuple[int, int, int] | None = None) -> None:
    if size is None:
        size = LABEL_FONT_SIZE
    if fill is None:
        fill = LABEL_DEFAULT_TEXT_COLOR
    d.text((x, y), s, fill=fill, font=_pil_font(size))


class LinearDashboardRenderer:
    """Stateful offscreen renderer: eases progress (value) and pulses a random selection."""

    def __init__(
        self,
        size: tuple[int, int] = DEFAULT_SIZE,
        *,
        background=None,
        fps: float = 15.0,
        value_tween_s: float | None = None,
        selection_transition_s: float | None = None,
        rng: random.Random | None = None,
        layout: BaseLayoutManager | None = None,
    ) -> None:
        self._ease = _ensure_qt()
        self._size: tuple[int, int] = (int(size[0]), int(size[1]))
        self._background = background
        self._fps = fps
        # Константы заданы в конце файла, поэтому дефолты разрешаем здесь
        # (в сигнатуре имена констант ещё не объявлены на этапе определения класса).
        self._vtween = VALUE_TWEEN_S if value_tween_s is None else value_tween_s
        self._selection_transition_s = (
            SELECTION_TRANSITION_S if selection_transition_s is None else selection_transition_s
        )
        self._rng = rng if rng is not None else random.Random()
        self._layout = layout if layout is not None else SelectionLayoutManager()
        self._ids: list[str] = []
        self._anim_t = 0.0
        self._anim_step = 1.0 / max(1.0, fps)
        # Значение бара: текущее, стартовое (в момент смены цели) и цель + время.
        self._progress: list[float] = []
        self._progress_from: list[float] = []
        self._progress_target: list[float] = []
        self._progress_start: list[float] = []
        # Выбор меняется вместе со сменой данных (через раз) и держится всё окно
        # данных (~5 с), а не мигает на собственном таймере.
        self._sel_index: int | None = None
        self._sel_should_select = True
        self._sel_window_start = 0.0
        self._sel_window_is_select = False
        self._last_content_signature: object | None = None

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
                pct = ((raw - vmin) / denom * PERCENT_SCALE) if denom else 0.0
                pct = max(0.0, min(PERCENT_SCALE, pct))
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
        # Advance the value-easing clock by one step per RENDER: trcc re-renders
        # at a capped cadence (source.fps) or on a real change, so the value tween
        # proceeds frame-by-frame and never overruns the panel's ~18 fps budget.
        # The random selection below deliberately uses wall-clock instead, so it
        # is independent of how often the values change.
        self._anim_t += self._anim_step
        now = self._anim_t
        pal = _palette(palette)
        specs = self._specs(setup or [], values or {}, pal)
        ids = [s["id"] for s in specs]
        if ids != self._ids:
            self._ids = ids
            self._progress = [0.0] * len(specs)
            self._progress_from = [0.0] * len(specs)
            self._progress_target = [0.0] * len(specs)
            self._progress_start = [0.0] * len(specs)

        n = len(specs)
        # Выбор меняется ВМЕСТЕ со сменой данных: каждое новое окно (сигнатура
        # setup/values/palette) чередует select/skip и держит выделение до следующего
        # окна (~5 с), а не мигает на собственном таймере.  Значения при этом «добегают»
        # до цели за ``_vtween`` (0.3 с) и держатся до следующего окна данных.
        content_signature = self._content_signature(setup or [], values or {}, palette or {})
        selection_now = time.monotonic()
        if content_signature != self._last_content_signature:
            self._last_content_signature = content_signature
            self._advance_selection(selection_now, n)
        focus = self._selection_focus(selection_now, n)
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
            progress.append(self._eased_progress(i, spec["pct"] / PERCENT_SCALE, now))

        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter

        img = self._make_image(pal, background)
        selection_on = self._layout.is_selection_enabled
        active = self._active_index(focus, selection_on)
        selection_strength = max(focus) if (selection_on and focus) else 0.0
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
            key=lambda i: Z_FOREGROUND if i == active
            else Z_BASE + Z_FOCUS_WEIGHT * (focus[i] if selection_on else 0.0),
        )
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        for i in order:
            f = focus[i] if selection_on else 0.0
            dim = self._selection_dim(f, selection_on, selection_strength)
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
            _pil_text(d, "no data", self._size[0] // 2 - NO_DATA_TEXT_PAD,
                      (self._size[1] - NO_DATA_TEXT_PAD) // 2,
                      size=NO_DATA_FONT_SIZE, fill=pal["text"])
            d.line(
                [(self._size[0] // 2 - NO_DATA_DASH_X_SPAN,
                  (self._size[1] - NO_DATA_TEXT_PAD) // 2 + NO_DATA_DASH_Y_SPAN),
                 (self._size[0] // 2 + NO_DATA_DASH_X_SPAN,
                  (self._size[1] - NO_DATA_TEXT_PAD) // 2 + NO_DATA_DASH_Y_SPAN)],
                fill=pal["muted"], width=2,
            )
        return pil.tobytes()

    def _make_image(self, palette: dict, background=None):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage

        w, h = self._size
        bg = self._background if background is None else background
        if bg is None:
            # Прозрачный слой: дашборд — это ОВЕРЛЕЙ поверх живой темы панели,
            # поэтому фон не заливаем — иначе тема под ним не видна.  Рисуем
            # только бары/текст/свечение, сквозь прозрачные места видна тема.
            img = QImage(w, h, QImage.Format_ARGB32)
            img.fill(Qt.GlobalColor.transparent)
            return img
        from PIL import ImageOps

        rgb = bg.convert(IMAGE_MODE_RGB)
        if rgb.size != (w, h):
            rgb = ImageOps.fit(rgb, (w, h))
        raw = rgb.tobytes()
        return QImage(raw, w, h, w * RGB_BYTES_PER_PIXEL, QImage.Format_RGB888)

    @staticmethod
    def _qimage_to_pil(img) -> Image.Image:
        from PySide6.QtGui import QImage

        img = img.convertToFormat(QImage.Format_RGBA8888)
        w, h = img.width(), img.height()
        ptr = img.constBits()
        raw = ptr.tobytes() if hasattr(ptr, "tobytes") else bytes(ptr)
        stride = img.bytesPerLine()
        return Image.frombuffer(
            IMAGE_MODE_RGBA, (w, h), raw, PIL_RAW_MODE, IMAGE_MODE_RGBA, stride, 1
        )

    def _paint_text_pil(self, img: Image.Image, specs: list[dict],
                        layout, text_color: tuple[int, int, int],
                        focus: list[float] | None = None,
                        bg: tuple[int, int, int] = (0, 0, 0)) -> None:
        from PIL import ImageDraw

        d = ImageDraw.Draw(img)
        focus_values = focus if focus is not None else []
        selection_on = focus is not None
        selection_strength = max(focus_values) if focus_values else 0.0
        for idx, (spec, geo) in enumerate(zip(specs, layout.geometry, strict=True)):
            focus_value = focus_values[idx] if idx < len(focus_values) else 0.0
            brightness = self._selection_dim(focus_value, selection_on, selection_strength)
            # Приглушаем подпись к фону, чтобы она гасла вместе с баром.
            text_color_rgb = (
                int(round(text_color[0] * brightness + bg[0] * (1.0 - brightness))),
                int(round(text_color[1] * brightness + bg[1] * (1.0 - brightness))),
                int(round(text_color[2] * brightness + bg[2] * (1.0 - brightness))),
            )
            center_y = int(geo.center_y)
            bar_left = int(geo.x)
            bar_length = int(geo.width)
            _pil_text(d, spec["label"], bar_left + LABEL_PAD_X, center_y - LABEL_PAD_Y,
                      size=LABEL_FONT_SIZE, fill=text_color_rgb)
            font = _pil_font(LABEL_FONT_SIZE)
            value_w = d.textlength(spec["text"], font=font)
            _pil_text(d, spec["text"],
                      bar_left + bar_length - LABEL_PAD_X - int(value_w),
                      center_y - LABEL_PAD_Y, size=LABEL_FONT_SIZE, fill=text_color_rgb)

    @staticmethod
    def _paint_glow_pil(img, rect, focus_color, strength: float) -> None:
        """Накладывает плавную зелёную тень ТОЛЬКО наружу ``rect`` на ``img``.

        Рисует силуэт бара (скруглённый прямоугольник), размывает его гауссовым
        блюром (настоящий плавный градиент, без дискретных колец), затем вырезает
        из размытой маски собственный footprint бара, чтобы свет расходился только
        вне бара и не тонировал заливку.  ``strength`` (0..1) = уровень фокуса.
        """
        from PIL import Image, ImageDraw, ImageFilter

        bar_left = int(rect.x())
        bar_top = int(rect.y())
        bar_width = int(rect.width())
        bar_height = int(rect.height())
        if bar_width <= 0 or bar_height <= 0:
            return
        # Ограничиваем область тени вокруг бара, чтобы не размывать весь кадр.
        box_left = max(0, bar_left - GLOW_MARGIN_PX)
        box_top = max(0, bar_top - GLOW_MARGIN_PX)
        box_right = min(img.width, bar_left + bar_width + GLOW_MARGIN_PX)
        box_bottom = min(img.height, bar_top + bar_height + GLOW_MARGIN_PX)
        box_width = box_right - box_left
        box_height = box_bottom - box_top
        if box_width <= 0 or box_height <= 0:
            return
        corner_radius = max(MIN_BAR_CORNER_RADIUS, bar_height / 2.0)
        silhouette = Image.new(IMAGE_MODE_LUMINANCE, (box_width, box_height), LUMINANCE_MIN)
        ImageDraw.Draw(silhouette).rounded_rectangle(
            [bar_left - box_left, bar_top - box_top,
             bar_left - box_left + bar_width, bar_top - box_top + bar_height],
            radius=corner_radius,
            fill=LUMINANCE_MAX,
        )
        blurred = silhouette.filter(ImageFilter.GaussianBlur(GLOW_BLUR_RADIUS_PX))
        # Внутри footprint бара тень равна нулю — свет идёт только наружу.
        outside_mask = silhouette.point(lambda v: LUMINANCE_MAX - v)
        outward_alpha = Image.composite(
            blurred,
            Image.new(IMAGE_MODE_LUMINANCE, (box_width, box_height), LUMINANCE_MIN),
            outside_mask,
        ).point(lambda v: int(v * min(1.0, max(0.0, strength))))
        glow = Image.new(IMAGE_MODE_RGBA, (box_width, box_height), (*focus_color, LUMINANCE_MIN))
        glow.putalpha(outward_alpha)
        img.paste(glow, (box_left, box_top), outward_alpha)

    @staticmethod
    def _paint_bar(p, rect, fill_frac: float, color, dim: float) -> None:
        """Рисует один бар (полоса низкого альфа + градиентная заливка), dim (0..1)."""
        from PySide6.QtCore import QRectF, Qt
        from PySide6.QtGui import QBrush, QColor, QLinearGradient

        bar_height = rect.height()
        corner_radius = max(MIN_BAR_CORNER_RADIUS, bar_height / 2.0)
        base_color = QColor(*color)
        p.setPen(Qt.NoPen)
        p.setOpacity(max(0.0, min(1.0, dim)))
        # Подложка (полоса низкого альфа-канала).
        track_color = QColor(base_color)
        track_color.setAlpha(TRACK_ALPHA)
        p.setBrush(track_color)
        p.drawRoundedRect(rect, corner_radius, corner_radius)
        # Заливка (градиент) со скруглёнными концами, до доли значения.
        fill_fraction = max(0.0, min(1.0, fill_frac))
        if fill_fraction > 0:
            fill_rect = QRectF(rect.x(), rect.y(), rect.width() * fill_fraction, rect.height())
            gradient = QLinearGradient(rect.x(), 0.0, rect.x() + rect.width(), 0.0)
            gradient.setColorAt(GRADIENT_START, base_color.lighter(FILL_GRADIENT_LIGHTER))
            gradient.setColorAt(GRADIENT_EDGE_HI, QColor(base_color))
            gradient.setColorAt(GRADIENT_EDGE_DARK, base_color.darker(FILL_GRADIENT_DARKER))
            gradient.setColorAt(GRADIENT_END, base_color.lighter(FILL_GRADIENT_LIGHTER))
            p.setBrush(QBrush(gradient))
            p.drawRoundedRect(fill_rect, corner_radius, corner_radius)
        p.setOpacity(1.0)

    def _active_index(self, focus: list[float], selection_on: bool):
        """Index of the currently selected bar (the one with the highest focus)."""
        if not selection_on or not focus:
            return None
        peak = max(focus)
        return focus.index(peak) if peak > FOCUS_EPSILON else None

    @staticmethod
    def _content_signature(setup: list[dict], values: dict, palette: dict) -> object:
        """Отпечаток отображаемого содержимого — его смена = новое окно данных."""
        return (
            tuple((g.get("id"), g.get("label"), g.get("min"), g.get("max"),
                   g.get("unit")) for g in (setup or [])),
            tuple(sorted((values or {}).items())),
            tuple(sorted((palette or {}).items())),
        )

    def _advance_selection(self, now: float, count: int) -> None:
        """Открывает новое окно данных: «через раз» выбрать случайный бар или пропустить."""
        if count == 0 or not self._layout.is_selection_enabled:
            self._sel_index = None
            return
        # Через раз: одно окно выделяет случайный бар, следующее — нет.
        if self._sel_should_select:
            self._sel_index = self._rng.randrange(count)
            self._sel_window_is_select = True
        else:
            self._sel_window_is_select = False  # предыдущий пульс гаснет в этом окне
        self._sel_window_start = now
        self._sel_should_select = not self._sel_should_select

    def _selection_focus(self, now: float, count: int) -> list[float]:
        """Яркость/фокус выделения в текущем окне данных.

        Если окно «выбрано» — случайный бар плавно нарастает и держится (фокус 1.0)
        до конца окна; если окно «пропуск» — предыдущий пульс плавно гаснет и
        остаётся выключенным.  Так выделение меняется вместе с данными и держится
        весь показ (~5 с), а не мигает.
        """
        focus = [0.0] * count
        if count == 0 or not self._layout.is_selection_enabled or self._sel_index is None:
            return focus
        if self._sel_index >= count:
            # Баров стало меньше под активным пульсом — убираем его (иначе OOB).
            self._sel_index = None
            self._sel_window_is_select = False
            return focus
        transition = max(FOCUS_EPSILON, self._selection_transition_s)
        elapsed = now - self._sel_window_start
        if self._sel_window_is_select:
            focus[self._sel_index] = (
                self._ease(elapsed / transition) if elapsed <= transition else 1.0
            )
        else:
            focus[self._sel_index] = (
                1.0 - self._ease(elapsed / transition) if elapsed <= transition else 0.0
            )
        return focus

    def _selection_dim(self, focus_value: float, selection_on: bool,
                       selection_strength: float) -> float:
        """Яркость бара/подписи: при idle полная, при пульсе — затемняется всё,
        кроме подсвеченного бара."""
        if selection_on and selection_strength > FOCUS_EPSILON:
            if focus_value > FOCUS_EPSILON:
                return 1.0
            return 1.0 - DIM_RANGE * selection_strength
        return 1.0

    def _eased_progress(self, idx: int, target: float, now: float) -> float:
        """Возвращает текущее значение бара.

        Бар «добегает» от предыдущего значения до цели за ``_vtween`` секунд (0.3 с):
        при смене цели фиксируется стартовое значение и время, идёт ease-out, а после
        достижения цели значение держится, пока не придёт следующее окно данных.
        """
        if idx >= len(self._progress):
            fill = idx + 1 - len(self._progress)
            self._progress.extend([0.0] * fill)
            self._progress_from.extend([0.0] * fill)
            self._progress_target.extend([0.0] * fill)
            self._progress_start.extend([0.0] * fill)
        if self._vtween <= 0:
            value = target
        else:
            if target != self._progress_target[idx]:
                self._progress_from[idx] = self._progress[idx]
                self._progress_target[idx] = target
                self._progress_start[idx] = now
            frac = min(1.0, max(0.0, (now - self._progress_start[idx]) / self._vtween))
            value = self._progress_from[idx] + (
                self._progress_target[idx] - self._progress_from[idx]
            ) * self._ease(frac)
        self._progress[idx] = value
        return value

    def close(self) -> None:
        self._background = None


# --- Константы (PEP 8: UPPER_CASE_WITH_UNDERSCORES, на уровне модуля) ---
# Вынесены в конец файла, чтобы тела функций читались как логика, а не как
# набор «магических» строк и чисел.  Значения соответствуют прежним литералам.

# Режимы PIL-изображений.
IMAGE_MODE_RGB = "RGB"
IMAGE_MODE_LUMINANCE = "L"
IMAGE_MODE_RGBA = "RGBA"
PIL_RAW_MODE = "raw"
RGB_BYTES_PER_PIXEL = 3

# Границы яркости (L-режим / альфа-канал).
LUMINANCE_MAX = 255
LUMINANCE_MIN = 0

# Свечение (glow) выбранного бара.
GLOW_MARGIN_PX = 48          # запас области размытия вокруг бара
GLOW_BLUR_RADIUS_PX = 16     # радиус гауссова размытия силуэта

# Бар.
MIN_BAR_CORNER_RADIUS = 2.0  # минимальный радиус скругления бара (px)
TRACK_ALPHA = 40             # прозрачность полосы-подложки (0..255)
FILL_GRADIENT_LIGHTER = 118  # осветление к краям заливки (%)
FILL_GRADIENT_DARKER = 96    # затемнение в центре заливки (%)
GRADIENT_START = 0.0
GRADIENT_EDGE_HI = 0.12
GRADIENT_EDGE_DARK = 0.85
GRADIENT_END = 1.0

# Скорость анимации значения: бар «добегает» от прежнего значения до нового за
# 300 мс (окно данных ~5 с, но движение полоски — быстрым, а не 5-секундным).
VALUE_TWEEN_S = 0.3

# Выбор «через раз», меняется вместе со сменой данных (событие по сигнатуре):
# одно окно выделяет случайный бар, следующее — пропускает; выделение держится
# всё окно (~5 с), не мигает.
SELECTION_TRANSITION_S = 0.6  # время нарастания/затухания выделения в начале окна

# Выделение (selection) — затемнение и z-порядок.
DIM_MIN = 0.35               # яркость полностью затемнённого (невыделенного) бара
DIM_RANGE = 1.0 - DIM_MIN    # насколько гаснет невыделенный бар при активном пульсе
Z_FOREGROUND = 100.0         # выделенный бар всегда поверх
Z_BASE = 1.0                 # остальные бары в порядке фокуса
Z_FOCUS_WEIGHT = 2.0         # вес фокуса при сортировке
FOCUS_EPSILON = 1e-6         # порог «есть активный бар»

# Шкала процентов (для приведения 0..100 к доле 0..1).
PERCENT_SCALE = 100.0

# Подписи (labels).
LABEL_FONT_SIZE = 26
LABEL_DEFAULT_TEXT_COLOR = (238, 242, 248)
LABEL_PAD_X = 14
LABEL_PAD_Y = 16

# Заглушка «нет данных».
NO_DATA_FONT_SIZE = 30
NO_DATA_TEXT_PAD = 34
NO_DATA_DASH_X_SPAN = 51
NO_DATA_DASH_Y_SPAN = 28
