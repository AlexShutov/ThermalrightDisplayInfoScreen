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

from .dashboard import DEFAULT_SIZE, _palette, hex_to_rgb, value_color
from .layout import BaseLayoutManager, SelectionLayoutManager
from .selection_strategy import (
    SelectMultipleIndicators,
    SelectSingleIndicator,
    count_selected,
)
from .ui_density import dp

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


# ``common.selection`` импортируем лениво — он headless (без Qt/Pillow), но живёт в
# сабмодуле ``DashboardWidgets``, поэтому основной ``hardwarescreen`` не должен
# требовать сабмодуль на импорте.
_SELECTION = None


def _ensure_selection_module():
    """Lazily import the shared ``common.selection`` module (no submodule at import)."""
    global _SELECTION
    if _SELECTION is None:
        import common.selection as selection  # noqa: PLC0415

        _SELECTION = selection
    return _SELECTION


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
        fill_direction: str | None = None,
        bar_corner_fraction: float | None = None,
        fill_corner_fraction: float | None = None,
        value_palette_on: bool = True,
        density: float = 1.0,
    ) -> None:
        # Константы заданы в конце файла, поэтому дефолт направления разрешаем
        # здесь (в сигнатуре имя константы ещё не объявлено на этапе определения).
        if fill_direction is None:
            fill_direction = FILL_DIRECTION_LEFT_TO_RIGHT
        if fill_direction not in ALLOWED_FILL_DIRECTIONS:
            raise ValueError(
                f"fill_direction must be one of {ALLOWED_FILL_DIRECTIONS}, "
                f"got {fill_direction!r}"
            )
        self._fill_direction = fill_direction
        self._bar_vertical = self._bar_is_vertical(fill_direction)
        # Радиусы скругления как ДОЛЯ толщины столбца (0..0.5): ``bar_corner_fraction``
        # — общий для капсулы столбца (и его конца у начала бара); ``fill_corner_fraction``
        # — для границы (кромки) заполненной части со стороны роста (сверху/справа).
        # ``None`` = прежнее поведение: капсула (0.5) + плоская граница роста.
        # Так размер зависит от толщины, а не от абсолютных px — не надо пересчитывать
        # под другое разрешение панели.
        self._bar_corner_fraction = bar_corner_fraction
        self._fill_corner_fraction = fill_corner_fraction
        self._value_palette_on = value_palette_on
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
        # Дизайн-величины задаются в dp и приводятся к px через плотность (см.
        # ``ui_density``): шрифт/отступы подписей, «нет данных», запас и радиус
        # размытия свечения.  Геометрия (бары/зазоры/скругления) уже масштабируется
        # через мм/доли, поэтому сюда не входит.
        self._density = density if density > 0 else 1.0
        self._label_font_px = int(dp(LABEL_FONT_SIZE, self._density))
        self._label_pad_x_px = int(dp(LABEL_PAD_X, self._density))
        self._label_pad_y_px = int(dp(LABEL_PAD_Y, self._density))
        self._no_data_font_px = int(dp(NO_DATA_FONT_SIZE, self._density))
        self._no_data_text_pad_px = int(dp(NO_DATA_TEXT_PAD, self._density))
        self._no_data_dash_x_px = int(dp(NO_DATA_DASH_X_SPAN, self._density))
        self._no_data_dash_y_px = int(dp(NO_DATA_DASH_Y_SPAN, self._density))
        # Запас области размытия обязан остаться целым (иначе PIL-размер кадра станет дробным).
        self._glow_margin_px = int(dp(GLOW_MARGIN_PX, self._density))
        self._glow_blur_radius_px = int(dp(GLOW_BLUR_RADIUS_PX, self._density))
        self._rng = rng if rng is not None else random.Random()
        self._layout = layout if layout is not None else SelectionLayoutManager()
        # Стратегии роста выбранных индикаторов (внутренняя деталь).  ``select`` —
        # прежнее поведение; ``multiple`` применяется только когда выбрано больше
        # одного индикатора и урезает рост соседних выбранных до зазора под свечение.
        self._strategy_single = SelectSingleIndicator()
        self._strategy_multiple = SelectMultipleIndicators()
        self._ids: list[str] = []
        self._anim_t = 0.0
        self._anim_step = 1.0 / max(1.0, fps)
        # Значение бара: текущее, стартовое (в момент смены цели) и цель + время.
        self._progress: list[float] = []
        self._progress_from: list[float] = []
        self._progress_target: list[float] = []
        self._progress_start: list[float] = []
        # Выбор меняется вместе со сменой данных (через раз) и держится всё окно
        # данных (~5 с), а не мигает на собственном таймере.  Логика выделения
        # общая (см. ``common/selection``) — её же использует кольцевой индикатор.
        self._selection = _ensure_selection_module().SelectionController(
            transition_s=self._selection_transition_s, ease=self._ease, rng=self._rng
        )
        self._last_content_signature: object | None = None

    @property
    def _sel_index(self) -> int | None:
        """Индекс подсвеченного столбца (общий драйвер выделения)."""
        return self._selection.active_index

    @property
    def _sel_window_is_select(self) -> bool:
        """Истина, если текущее окно данных «выбранное» (а не «пропуск»)."""
        return self._selection.is_select_window

    def __call__(
        self,
        setup: list[dict],
        values: dict,
        palette: dict,
        size: tuple[int, int],
        background=None,
        focus: list[float] | None = None,
    ) -> bytes:
        return self.render(setup, values, palette, size, background, focus=focus)

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
            # Солид-цвет колонки: при включённой цветовой палитре — из шкалы
            # «значение → цвет» (красный → зелёный по заполнению), иначе — общий
            # акцент палитры (прежнее поведение).  Так «градиентная тема» столбцов
            # включается/выключается одним флагом.
            if self._value_palette_on:
                color = value_color(pct / PERCENT_SCALE)
            else:
                color = palette["accent"]
            specs.append(
                {
                    "id": gid,
                    "label": g.get("label", gid),
                    "text": text,
                    "pct": pct,
                    "color": color,
                }
            )
        return specs

    def _track_color(self, palette: dict) -> tuple[int, int, int] | None:
        """Цвет подложки (заднего фона) столбцов.

        При включённой цветовой палитре — ОДИН и тот же нейтральный цвет
        (``palette["muted"]``) для всех столбцов, независимо от цвета их заливки;
        иначе ``None`` — подложка берётся из цвета самой заливки (прежнее поведение,
        все столбцы в общий акцент).
        """
        return palette["muted"] if self._value_palette_on else None

    def _glow_color(self, progress: float,
                    focus_color: tuple[int, int, int]) -> tuple[int, int, int]:
        """Цвет свечения выделенного бара.

        При включённой цветовой палитре — цвет ТЕКУЩЕГО состояния бара
        (``value_color(progress)``); без палитры — прежний зелёный фокус-цвет.
        """
        if self._value_palette_on:
            return value_color(progress)
        return focus_color

    def render(
        self,
        setup: list[dict],
        values: dict,
        palette: dict,
        size: tuple[int, int],
        background=None,
        focus: list[float] | None = None,
    ) -> bytes:
        """Render one eased frame as raw bytes (RGBA ``w*h*4`` for the transparent
        overlay; the Pillow-fallback images stay ``w*h*3``)."""
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
        # Если передан ``focus`` (композитный режим: выделение общее с кольцами) —
        # используем его как есть и внутреннее выделение не двигаем.
        if focus is None:
            content_signature = self._content_signature(setup or [], values or {}, palette or {})
            selection_now = time.monotonic()
            if content_signature != self._last_content_signature:
                self._last_content_signature = content_signature
                self._advance_selection(selection_now, n)
            focus = self._selection_focus(selection_now, n)
        else:
            focus = list(focus[:n]) + [0.0] * max(0, n - len(focus))
        # The measured geometry is always constant (equal height + gap); selection
        # is a render-side effect (dim the others, grow + foreground the selected).
        layout = self._layout.measure(
            self._size[0],
            self._size[1],
            n,
            focus if self._layout.is_selection_enabled else None,
            vertical=self._bar_vertical,
        )
        progress: list[float] = []
        for i, spec in enumerate(specs):
            progress.append(self._eased_progress(i, spec["pct"] / PERCENT_SCALE, now))

        from PySide6.QtGui import QPainter

        img = self._make_image(pal, background)
        selection_on = self._layout.is_selection_enabled
        selection_active_epsilon = _ensure_selection_module().SELECTION_ACTIVE_EPSILON
        selection_strength = max(focus) if (selection_on and focus) else 0.0
        focus_color = _focus_color()
        # При включённой цветовой палитре подложка (задний фон столбца) — ОДИН и
        # тот же нейтральный цвет для всех столбцов (не их индивидуальный цвет
        # значения), а свечение выделенного бара — цвет его текущего состояния.
        track_color = self._track_color(pal)

        # Рост выбранного бара вокруг центра делегирован стратегии выделения: один
        # бар (или ни одного) растёт на ``selection_scale`` (прежнее поведение), а при
        # нескольких выбранных рядом рост урезается, чтобы между увеличенными полосами
        # остался зазор не менее ``GLOW_MIN_GAP_MM`` — места под свечение, чтобы
        # свечения соседних выбранных не смешивались.
        panel_h = self._size[1]
        panel_w = self._size[0]
        margin = self._layout.edge_margin
        min_gap_px = GLOW_MIN_GAP_MM * self._resolve_px_per_mm(self._size)
        rects = self._select_strategy(focus, selection_on).compute(
            focus, layout.geometry, panel_w, panel_h, margin,
            self._layout.selection_scale, self._bar_vertical, min_gap_px,
        )

        # Draw in increasing z-order: dimmed neighbours first, the selected bars (with
        # their glow) last, on top — the correct overlay z-buffer ordering.  Выбранных
        # может быть несколько, поэтому «поверх» — любой бар с ненулевым фокусом.
        order = sorted(
            range(n),
            key=lambda i: Z_FOREGROUND
            if (selection_on and focus[i] > selection_active_epsilon)
            else Z_BASE + Z_FOCUS_WEIGHT * (focus[i] if selection_on else 0.0),
        )
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        for i in order:
            f = focus[i] if selection_on else 0.0
            dim = self._selection_dim(f, selection_on, selection_strength)
            # Цвет заливки бара — ТЕКУЩЕЕ (анимированное) значение при включённой
            # палитре, чтобы заливка и свечение выделенного бара не расходились на
            # 0.3 с анимации; без палитры — общий акцент (прежнее поведение).
            bar_color = (
                value_color(progress[i])
                if self._value_palette_on
                else specs[i]["color"]
            )
            self._paint_bar(p, rects[i], progress[i], bar_color, dim,
                            self._fill_direction,
                            bar_corner_fraction=self._bar_corner_fraction,
                            fill_corner_fraction=self._fill_corner_fraction,
                            track_color=track_color)
        p.end()

        pil = self._qimage_to_pil(img)
        # Плавная тень ТОЛЬКО наружу (гауссово-размытый силуэт заливки с вырезанным
        # собственным footprint): при включённой цветовой палитре свечение выделенного
        # бара — цветом ЕГО текущего состояния (значение → цвет), а не зелёным; без
        # палитры — прежнее зелёное свечение (HARDWARESCREEN_FOCUS_COLOR).
        if selection_on:
            for i in range(n):
                f = focus[i] if i < len(focus) else 0.0
                if f > 0.0:
                    # Подсветка идёт вокруг ЗАПОЛНЕННОЙ части бара (ширина ×
                    # прогресс), а не вокруг всей полосы — и следует за анимацией
                    # значения (progress[i] меняется каждый кадр).  При нулевой
                    # заливке подсвечивать нечего — бар ещё пуст.
                    fill_fraction = max(0.0, min(1.0, progress[i]))
                    if fill_fraction > 0.0:
                        fill_rect = self._fill_rect(
                            rects[i], fill_fraction, self._fill_direction,
                        )
                        glow_color = self._glow_color(progress[i], focus_color)
                        pil = self._paint_glow_pil(
                            pil, rects[i], fill_rect, glow_color, f,
                            vertical=self._bar_vertical,
                            bar_corner_fraction=self._bar_corner_fraction,
                            fill_corner_fraction=self._fill_corner_fraction,
                            fill_direction=self._fill_direction,
                            glow_margin_px=self._glow_margin_px,
                            glow_blur_radius_px=self._glow_blur_radius_px,
                        )
        if specs:
            self._paint_text_pil(
                pil, specs, layout, pal["text"],
                focus if selection_on else None, pal["background"],
                vertical=self._bar_vertical,
            )
        else:
            from PIL import ImageDraw

            d = ImageDraw.Draw(pil)
            _pil_text(d, "no data", self._size[0] // 2 - int(self._no_data_text_pad_px),
                      (self._size[1] - int(self._no_data_text_pad_px)) // 2,
                      size=self._no_data_font_px, fill=pal["text"])
            center_x = self._size[0] // 2
            dash_y = (
                (self._size[1] - int(self._no_data_text_pad_px)) // 2
                + int(self._no_data_dash_y_px)
            )
            d.line(
                [
                    (center_x - int(self._no_data_dash_x_px), dash_y),
                    (center_x + int(self._no_data_dash_x_px), dash_y),
                ],
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
                        bg: tuple[int, int, int] = (0, 0, 0),
                        *,
                        vertical: bool = False) -> None:
        from PIL import ImageDraw

        d = ImageDraw.Draw(img)
        focus_values = focus if focus is not None else []
        selection_on = focus is not None
        selection_strength = max(focus_values) if focus_values else 0.0
        label_font = _pil_font(self._label_font_px)
        for idx, (spec, geo) in enumerate(zip(specs, layout.geometry, strict=True)):
            focus_value = focus_values[idx] if idx < len(focus_values) else 0.0
            brightness = self._selection_dim(focus_value, selection_on, selection_strength)
            # Приглушаем подпись к фону, чтобы она гасла вместе с баром.
            text_color_rgb = (
                int(round(text_color[0] * brightness + bg[0] * (1.0 - brightness))),
                int(round(text_color[1] * brightness + bg[1] * (1.0 - brightness))),
                int(round(text_color[2] * brightness + bg[2] * (1.0 - brightness))),
            )
            if vertical:
                # Подпись — по центру X столбца: метка у верхнего края, значение у
                # нижнего, чтобы стек заливки (снизу вверх/сверху вниз) не перекрыл текст.
                center_x = int(geo.x + geo.width / 2.0)
                bar_top = int(geo.y)
                bar_bottom = int(geo.y + geo.height)
                label_w = d.textlength(spec["label"], font=label_font)
                d.text((center_x - int(label_w / 2.0), bar_top + self._label_pad_y_px),
                       spec["label"], fill=text_color_rgb, font=label_font)
                value_w = d.textlength(spec["text"], font=label_font)
                d.text((center_x - int(value_w / 2.0),
                        bar_bottom - self._label_pad_y_px - self._label_font_px),
                       spec["text"], fill=text_color_rgb, font=label_font)
                continue
            center_y = int(geo.center_y)
            bar_left = int(geo.x)
            bar_length = int(geo.width)
            _pil_text(d, spec["label"], bar_left + self._label_pad_x_px,
                      center_y - self._label_pad_y_px,
                      size=self._label_font_px, fill=text_color_rgb)
            value_w = d.textlength(spec["text"], font=label_font)
            _pil_text(d, spec["text"],
                      bar_left + bar_length - self._label_pad_x_px - int(value_w),
                      center_y - self._label_pad_y_px,
                      size=self._label_font_px, fill=text_color_rgb)

    @staticmethod
    def _paint_glow_pil(img, bar_rect, fill_rect, glow_color,
                        strength: float, *, vertical: bool = False,
                        bar_corner_fraction: float | None = None,
                        fill_corner_fraction: float | None = None,
                        fill_direction: str | None = None,
                        glow_margin_px: int | None = None,
                        glow_blur_radius_px: int | None = None):
        """Возвращает ``img`` с плавной цветной тенью ТОЛЬКО наружу заполненной части.

        Силуэт заливки строится той же формой, которой рисуется сама заливка в
        ``_paint_bar``: капсула бара (``bar_rect``) с радиусом ``bar_corner_fraction``
        (доля толщины) либо полной капсулой, ограниченная областью заливки
        (``fill_rect``), у которой скруглены только углы границы роста
        (``fill_corner_fraction`` либо плоская кромка).  Такой силуэт размывается
        гауссовым блюром (настоящий плавный градиент, без дискретных колец), затем
        из размытой маски вырезается собственный footprint заливки, чтобы свет
        расходился только вне залитой части и не тонировал её.  ``strength`` (0..1)
        = уровень фокуса; ``glow_color`` — цвет свечения (цвет состояния бара при
        включённой палитре либо зелёный фокус-цвет).  ``glow_margin_px``/``glow_blur_radius_px``
        — запас области размытия и радиус блюра в px (приходят из density-разрешённых
        величин рендерера).

        ``vertical=True`` — капсула относится к вертикальному столбцу, у которого
        скругление по ширине, а не по высоте (как у горизонтального бара).

        Возвращает обработанный кадр: ``outward_alpha`` кладётся в альфа-канал
        полноразмерного светового слоя, который смешивается с ``img`` через
        ``Image.alpha_composite`` (SourceOver).  Метод ``img.alpha_composite``
        полагался на изменение изображения на месте и в Pillow 13 этого не
        гарантирует, поэтому смешение здесь — статическое и возвращает новый кадр.
        """
        # Константы заданы в конце файла, поэтому дефолт разрешаем здесь (в сигнатуре
        # их имя ещё не объявлено на этапе определения класса).
        if glow_margin_px is None:
            glow_margin_px = GLOW_MARGIN_PX
        if glow_blur_radius_px is None:
            glow_blur_radius_px = GLOW_BLUR_RADIUS_PX
        from PIL import Image, ImageChops, ImageDraw, ImageFilter

        if fill_direction is None:
            fill_direction = FILL_DIRECTION_LEFT_TO_RIGHT
        if fill_direction not in ALLOWED_FILL_DIRECTIONS:
            fill_direction = FILL_DIRECTION_LEFT_TO_RIGHT
        bar_left = int(bar_rect.x())
        bar_top = int(bar_rect.y())
        bar_width = int(bar_rect.width())
        bar_height = int(bar_rect.height())
        if bar_width <= 0 or bar_height <= 0:
            return img
        fill_left = int(fill_rect.x())
        fill_top = int(fill_rect.y())
        fill_width = int(fill_rect.width())
        fill_height = int(fill_rect.height())
        if fill_width <= 0 or fill_height <= 0:
            return img
        # Ограничиваем область тени вокруг залитой части, чтобы не размывать весь кадр.
        box_left = max(0, fill_left - glow_margin_px)
        box_top = max(0, fill_top - glow_margin_px)
        box_right = min(img.width, fill_left + fill_width + glow_margin_px)
        box_bottom = min(img.height, fill_top + fill_height + glow_margin_px)
        box_width = box_right - box_left
        box_height = box_bottom - box_top
        if box_width <= 0 or box_height <= 0:
            return img
        # Толщина бара (короткая сторона) — база для радиусов-долей.
        cap_size = bar_height if not vertical else bar_width
        track_radius = _bar_track_radius(bar_rect, vertical, bar_corner_fraction)
        fill_fraction = (fill_height / bar_height) if vertical else (fill_width / bar_width)
        fill_radius = _fill_boundary_radius(fill_fraction, cap_size, fill_corner_fraction)
        # Силуэт = капсула бара ∩ регион заливки со скруглением границы роста.
        silhouette = Image.new(IMAGE_MODE_LUMINANCE, (box_width, box_height), LUMINANCE_MIN)
        ImageDraw.Draw(silhouette).rounded_rectangle(
            [bar_left - box_left, bar_top - box_top,
             bar_left - box_left + bar_width, bar_top - box_top + bar_height],
            radius=track_radius,
            fill=LUMINANCE_MAX,
        )
        region_mask = _rounded_region_mask(
            fill_rect, box_left, box_top, box_width, box_height,
            *_fill_corner_radii(fill_direction, 0.0, fill_radius),
        )
        silhouette = ImageChops.multiply(silhouette, region_mask)
        blurred = silhouette.filter(ImageFilter.GaussianBlur(glow_blur_radius_px))
        # Внутри footprint заливки тень равна нулю — свет идёт только наружу.
        outside_mask = silhouette.point(lambda v: LUMINANCE_MAX - v)
        outward_alpha = Image.composite(
            blurred,
            Image.new(IMAGE_MODE_LUMINANCE, (box_width, box_height), LUMINANCE_MIN),
            outside_mask,
        ).point(lambda v: int(v * min(1.0, max(0.0, strength))))
        # Световой слой: полноразмерный RGBA (цвет свечения), альфа = outward_alpha
        # ровно в области вокруг заливки, 0 вне её.
        light = Image.new(IMAGE_MODE_RGBA, img.size, (*glow_color, LUMINANCE_MIN))
        alpha_channel = light.getchannel("A")
        alpha_channel.paste(outward_alpha, (box_left, box_top))
        light.putalpha(alpha_channel)
        return Image.alpha_composite(img, light)

    @staticmethod
    def _bar_is_vertical(fill_direction: str) -> bool:
        """True for vertical columns (``bottom_to_top`` / ``top_to_bottom``)."""
        return fill_direction in VERTICAL_FILL_DIRECTIONS

    @staticmethod
    def _fill_rect(rect, fill_fraction: float, fill_direction: str):
        """Участок бара, который заполнен значением (доля от его длины).

        Горизонтальные бары заполняются по ширине, вертикальные столбцы — по
        высоте.  Направление задаёт, от какого края растёт заливка:
        ``left_to_right``/``top_to_bottom`` — от левого/верхнего края,
        ``right_to_left``/``bottom_to_top`` — от правого/нижнего края.
        Возвращает ``QRectF`` той же итоговой геометрии, что и заливка бара,
        чтобы подсветка совпадала с заливкой.
        """
        from PySide6.QtCore import QRectF

        if fill_direction not in ALLOWED_FILL_DIRECTIONS:
            raise ValueError(
                f"fill_direction must be one of {ALLOWED_FILL_DIRECTIONS}, "
                f"got {fill_direction!r}"
            )
        fraction = max(0.0, min(1.0, fill_fraction))
        if fill_direction == FILL_DIRECTION_RIGHT_TO_LEFT:
            width = rect.width() * fraction
            return QRectF(rect.right() - width, rect.y(), width, rect.height())
        if fill_direction == FILL_DIRECTION_TOP_TO_BOTTOM:
            height = rect.height() * fraction
            return QRectF(rect.x(), rect.y(), rect.width(), height)
        if fill_direction == FILL_DIRECTION_BOTTOM_TO_TOP:
            height = rect.height() * fraction
            return QRectF(rect.x(), rect.bottom() - height, rect.width(), height)
        width = rect.width() * fraction
        return QRectF(rect.x(), rect.y(), width, rect.height())

    @staticmethod
    def _paint_bar(p, rect, fill_frac: float, color, dim: float,
                   fill_direction: str | None = None,
                   bar_corner_fraction: float | None = None,
                   fill_corner_fraction: float | None = None,
                   track_color: tuple[int, int, int] | None = None) -> None:
        """Рисует один бар (полоса низкого альфа + солид-заливка цветом колонки), dim (0..1).

        ``fill_direction`` задаёт и ориентацию бара, и сторону роста заливки:
        горизонтальные бары (``left_to_right``/``right_to_left``) заполняются по
        ширине, вертикальные столбцы (``bottom_to_top``/``top_to_bottom``) — по
        высоте.

        ``bar_corner_fraction`` (если задан) — радиус капсулы столбца как доля
        толщины (0..0.5); ``None`` = полная капсула (0.5).  ``fill_corner_fraction``
        (если задан) — радиус границы (кромки) залитой части со стороны роста как
        доля толщины; ``None`` = плоская кромка.  Так размеры зависят от толщины
        столбца, а не от абсолютных px.

        ``track_color`` — цвет подложки (заднего фона) столбца.  Если задан, он
        используется для всех столбцов ОДИНАКОВЫЙ (независимо от цвета их заливки);
        если ``None`` — подложка берётся из ``color`` (цвет самой заливки).  Это
        нужно, чтобы при градиентной теме (цвет по значению) подложки всех столбцов
        были единого нейтрального цвета, а не каждая своего.
        """
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor

        if fill_direction is None:
            fill_direction = FILL_DIRECTION_LEFT_TO_RIGHT

        vertical = LinearDashboardRenderer._bar_is_vertical(fill_direction)
        # У горизонтального бара скруглены концы по высоте; у вертикального
        # столбца — по ширине (капсула повёрнута на 90°).  Толщина (короткая
        # сторона) — база для радиусов-долей.
        cap_size = rect.height() if not vertical else rect.width()
        track_radius = _bar_track_radius(rect, vertical, bar_corner_fraction)
        base_color = QColor(*color)
        p.setPen(Qt.NoPen)
        p.setOpacity(max(0.0, min(1.0, dim)))
        # Подложка (полоса низкого альфа-канала) — капсула всего бара.  Цвет —
        # либо единый ``track_color``, либо цвет заливки ``color``.
        track_rgb = track_color if track_color is not None else tuple(color)
        track_color_q = QColor(*track_rgb)
        track_color_q.setAlpha(TRACK_ALPHA)
        p.setBrush(track_color_q)
        p.drawRoundedRect(rect, track_radius, track_radius)
        # Заливка — капсула подложки, ограниченная областью заливки, у которой
        # скруглены только углы границы роста (``fill_corner_fraction`` либо
        # плоская кромка).  Так конец у начала бара всегда совпадает с капсулой
        # подложки даже при малом заполнении, а радиус задаётся независимо для
        # стороны роста (Qt клипит radius к min(w,h)/2, из-за чего раньше скругления
        # заливки и подложки расходились).
        fill_fraction = max(0.0, min(1.0, fill_frac))
        if fill_fraction > 0:
            fill_region = LinearDashboardRenderer._fill_rect(
                rect, fill_fraction, fill_direction,
            )
            fill_radius = _fill_boundary_radius(
                fill_fraction, cap_size, fill_corner_fraction,
            )
            region_path = _rounded_path(
                fill_region,
                *_fill_corner_radii(fill_direction, 0.0, fill_radius),
            )
            p.save()
            p.setClipPath(region_path)
            # Солид-заливка: один плоский цвет колонки (из шкалы «значение → цвет»),
            # без пространственного градиента внутри бара — как «solid» в CSS.
            p.setBrush(base_color)
            p.drawRoundedRect(rect, track_radius, track_radius)
            p.restore()
        p.setOpacity(1.0)

    def _select_strategy(self, focus: list[float], selection_on: bool):
        """Выбирает стратегию роста по числу выбранных индикаторов.

        Больше одной выбранной — ``SelectMultipleIndicators``; одна (или ни одной) —
        ``SelectSingleIndicator`` (прежнее поведение).
        """
        if selection_on and count_selected(focus) > 1:
            return self._strategy_multiple
        return self._strategy_single

    def _resolve_px_per_mm(self, size: tuple[int, int]) -> float:
        """Сколько пикселей панели приходится на 1 мм.

        Выводится из физической диагонали панели (``DEVICE_DIAGONAL_INCHES``) и её
        пиксельного разрешения при квадратных пикселях:
        ``px_per_mm = diagonal_px / diagonal_mm`` (та же формула, что в dev-модуле
        ``trcc-module-dashboard``).
        """
        width = max(1, int(size[0]))
        height = max(1, int(size[1]))
        diagonal_px = (width * width + height * height) ** 0.5
        diagonal_mm = DEVICE_DIAGONAL_INCHES * MM_PER_INCH
        return diagonal_px / diagonal_mm

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
        self._selection.enabled = self._layout.is_selection_enabled
        self._selection.advance(now, count)

    def _selection_focus(self, now: float, count: int) -> list[float]:
        """Яркость/фокус выделения в текущем окне данных.

        Если окно «выбрано» — случайный бар плавно нарастает и держится (фокус 1.0)
        до конца окна; если окно «пропуск» — предыдущий пульс плавно гаснет и
        остаётся выключенным.  Так выделение меняется вместе с данными и держится
        весь показ (~5 с), а не мигает.
        """
        return self._selection.focus(now, count)

    def _selection_dim(self, focus_value: float, selection_on: bool,
                       selection_strength: float) -> float:
        """Яркость бара/подписи: при idle полная, при пульсе — затемняется всё,
        кроме подсвеченного бара."""
        return self._selection.dim(focus_value, selection_on, selection_strength)

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
TRACK_ALPHA = 40             # прозрачность полосы-подложки (0..255)

# Скорость анимации значения: бар «добегает» от прежнего значения до нового за
# 300 мс (окно данных ~5 с, но движение полоски — быстрым, а не 5-секундным).
VALUE_TWEEN_S = 0.3

# Выбор «через раз», меняется вместе со сменой данных (событие по сигнатуре):
# одно окно выделяет случайный бар, следующее — пропускает; выделение держится
# всё окно (~5 с), не мигает.
SELECTION_TRANSITION_S = 0.6  # время нарастания/затухания выделения в начале окна

# Выделение (selection) — затемнение и z-порядок.  Значения затемнения (DIM_MIN/
# DIM_RANGE) и порога активности (SELECTION_ACTIVE_EPSILON) теперь живут в общем
# ``common/selection``; здесь — только z-порядок, зависящий от формы столбца.
Z_FOREGROUND = 100.0         # выделенный бар всегда поверх
Z_BASE = 1.0                 # остальные бары в порядке фокуса
Z_FOCUS_WEIGHT = 2.0         # вес фокуса при сортировке

# Физические размеры панели (Trofeo Vision, маркетинговый «11.3») — для пересчёта
# мм → px через px_per_mm, чтобы зазоры под свечение были заданы в миллиметрах, а не
# в пикселях конкретного разрешения.
DEVICE_DIAGONAL_INCHES = 11.3
MM_PER_INCH = 25.4
# Минимальный зазор между краями двух СМЕЖНЫХ увеличенных выбранных полос: места под
# свечение, чтобы свечения соседних выбранных не смешивались.
GLOW_MIN_GAP_MM = 3.0

# Шкала процентов (для приведения 0..100 к доле 0..1).
PERCENT_SCALE = 100.0

# Направление заполнения столбцов (аналог параметра направления колец).  Горизонт
# альные полосы: ``left_to_right`` (от левого края вправо) и ``right_to_left`` (от
# правого края влево); вертикальные столбцы: ``bottom_to_top`` (снизу вверх) и
# ``top_to_bottom`` (сверху вниз).  Все значения совпадают с сабмодулем
# activity_linear_indicator.
FILL_DIRECTION_LEFT_TO_RIGHT = "left_to_right"
FILL_DIRECTION_RIGHT_TO_LEFT = "right_to_left"
FILL_DIRECTION_BOTTOM_TO_TOP = "bottom_to_top"
FILL_DIRECTION_TOP_TO_BOTTOM = "top_to_bottom"
ALLOWED_FILL_DIRECTIONS = (
    FILL_DIRECTION_LEFT_TO_RIGHT,
    FILL_DIRECTION_RIGHT_TO_LEFT,
    FILL_DIRECTION_BOTTOM_TO_TOP,
    FILL_DIRECTION_TOP_TO_BOTTOM,
)
# Направления вертикальных столбцов (бар растёт по высоте, а не ширине).
VERTICAL_FILL_DIRECTIONS = (FILL_DIRECTION_BOTTOM_TO_TOP, FILL_DIRECTION_TOP_TO_BOTTOM)

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

# --- Вспомогательная геометрия скруглений (индивидуальные радиусы по углам) ---
# Вынесена в конец файла: использует константы выше (GLOW_MARGIN_PX, режимы
# PIL-изображений) — в момент вызова они уже определены.


def _bar_track_radius(rect, vertical: bool,
                      bar_corner_fraction: float | None) -> float:
    """Радиус капсулы бара/столбца как доля толщины (короткой стороны).

    ``bar_corner_fraction`` (0..0.5) от толщины: у горизонтального бара толщина —
    высота, у вертикального столбца — ширина.  По умолчанию (``None``) — полная
    капсула (0.5).  Зажимается в [0, 0.5], чтобы не превышать половину толщины
    (иначе Qt клипил бы сам, и заливка с подложкой расходились бы по углу).
    """
    thickness = rect.width() if vertical else rect.height()
    fraction = 0.5 if bar_corner_fraction is None else bar_corner_fraction
    fraction = min(max(0.0, fraction), 0.5)
    return fraction * thickness


def _fill_boundary_radius(fill_fraction: float, thickness: float,
                          fill_corner_fraction: float | None) -> float:
    """Радиус границы (кромки) заливки со стороны роста как доля толщины бара.

    ``fill_corner_fraction`` (0..0.5) от ``thickness``.  По умолчанию (``None``)
    граница плоская, но при 100% заполнении повторяет капсулу (0.5 толщины) — как в
    прежнем поведении «капсула ∩ регион», когда регион заполнения совпадает с полным
    баром и его конец оставался скруглённым.  Зажимается в [0, 0.5].
    """
    if fill_corner_fraction is None:
        return 0.5 * thickness if fill_fraction >= 1.0 else 0.0
    fraction = min(max(0.0, fill_corner_fraction), 0.5)
    return fraction * thickness


def _fill_corner_radii(fill_direction: str, track_radius: float,
                       fill_radius: float) -> tuple[float, float, float, float]:
    """Радиусы по 4 углам заливки в порядке (top-left, top-right, bottom-right,
    bottom-left) для заданного направления заполнения.

    ``track_radius`` относится к углам со стороны начала бара, ``fill_radius`` — к
    углам со стороны границы роста.  В текущем рисовании заливка строится как
    «капсула бара ∩ регион» (клип-капсула), поэтому для клип-региона в оба вызова
    передаётся ``track_radius=0.0``: региону нужно скруглить только границу роста,
    а конец у начала бара даёт сама капсула.  Если рисовать регион напрямую (без
    подложки-капсулы), сюда нужно передать реальный ``track_radius``.
    """
    if fill_direction == FILL_DIRECTION_LEFT_TO_RIGHT:
        return (track_radius, fill_radius, fill_radius, track_radius)
    if fill_direction == FILL_DIRECTION_RIGHT_TO_LEFT:
        return (fill_radius, track_radius, track_radius, fill_radius)
    if fill_direction == FILL_DIRECTION_BOTTOM_TO_TOP:
        return (fill_radius, fill_radius, track_radius, track_radius)
    return (track_radius, track_radius, fill_radius, fill_radius)


def _rounded_path(rect, tl: float, tr: float, br: float, bl: float):
    """QPainterPath прямоугольника с индивидуальными радиусами по 4 углам.

    Углы в порядке (top-left, top-right, bottom-right, bottom-left); каждый радиус
    клипится к половине короткой стороны — как это делает Qt у ``drawRoundedRect``,
    но здесь радиусы на концах разные, поэтому форма рисуется вручную.
    """
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainterPath

    x, y = rect.x(), rect.y()
    width, height = rect.width(), rect.height()
    limit = min(width, height) / 2.0
    tl = min(max(0.0, tl), limit)
    tr = min(max(0.0, tr), limit)
    br = min(max(0.0, br), limit)
    bl = min(max(0.0, bl), limit)
    path = QPainterPath()
    path.moveTo(x + tl, y)
    path.lineTo(x + width - tr, y)
    if tr > 0:
        path.arcTo(QRectF(x + width - 2.0 * tr, y, 2.0 * tr, 2.0 * tr), 90.0, -90.0)
    path.lineTo(x + width, y + height - br)
    if br > 0:
        path.arcTo(
            QRectF(x + width - 2.0 * br, y + height - 2.0 * br, 2.0 * br, 2.0 * br),
            0.0, -90.0,
        )
    path.lineTo(x + bl, y + height)
    if bl > 0:
        path.arcTo(QRectF(x, y + height - 2.0 * bl, 2.0 * bl, 2.0 * bl), 270.0, -90.0)
    path.lineTo(x, y + tl)
    if tl > 0:
        path.arcTo(QRectF(x, y, 2.0 * tl, 2.0 * tl), 180.0, -90.0)
    path.closeSubpath()
    return path


def _gray_qimage_to_luminance(qimg) -> Image.Image:
    """QImage Grayscale8 -> PIL ``L`` (1 байт/пиксель)."""
    width = qimg.width()
    height = qimg.height()
    ptr = qimg.constBits()
    raw = ptr.tobytes() if hasattr(ptr, "tobytes") else bytes(ptr)
    stride = qimg.bytesPerLine()
    return Image.frombuffer(
        IMAGE_MODE_LUMINANCE, (width, height), raw, PIL_RAW_MODE,
        IMAGE_MODE_LUMINANCE, stride, 1,
    )


def _rounded_region_mask(rect, box_left: int, box_top: int,
                         box_width: int, box_height: int,
                         tl: float, tr: float, br: float, bl: float) -> Image.Image:
    """Белая L-маска прямоугольника с индивидуальными радиусами в области кадра.

    Используется как маска региона заливки (граница роста скруглена, углы со стороны
    начала — 0), которой ограничивается капсула бара и в ``_paint_bar``, и в
    ``_paint_glow_pil``, чтобы силуэт свечения совпадал с заливкой.  Отрисовывается
    в квадранте рамки (``box_left``/``box_top``).
    """
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter

    path = _rounded_path(
        QRectF(rect.x() - box_left, rect.y() - box_top,
               rect.width(), rect.height()),
        tl, tr, br, bl,
    )
    qimg = QImage(box_width, box_height, QImage.Format_Grayscale8)
    qimg.fill(0)
    painter = QPainter(qimg)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(LUMINANCE_MAX, LUMINANCE_MAX, LUMINANCE_MAX))
    painter.drawPath(path)
    painter.end()
    return _gray_qimage_to_luminance(qimg)
