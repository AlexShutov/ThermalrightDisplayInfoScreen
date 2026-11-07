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
import threading
import time
from typing import TYPE_CHECKING

from PIL import Image

from .dashboard import DEFAULT_SIZE, _palette, hex_to_rgb, value_color
from .layout import BaseLayoutManager, SelectionLayoutManager
from .selection_strategy import (
    SelectMultipleIndicators,
    SelectSingleIndicator,
    count_selected,
)
from .ui_density import dp

if TYPE_CHECKING:  # типы сабмодуля нужны только для проверки типов (не на рантайме)
    from common.font import FontResolver, FontSpec
    from common.font_size import FontSizeCalculator

_LF = None


def _coerce_rgb(color: object) -> tuple[int, int, int]:
    """Приводит переопределённый цвет колонки к ``(r, g, b)``.

    Принимает hex-строку ``'#rrggbb'`` (как в палитре) либо тройку ``(r, g, b)``.
    Невалидное значение откатывается к нейтральному (как ``hex_to_rgb``).
    """
    if isinstance(color, str):
        return hex_to_rgb(color)
    if isinstance(color, (tuple, list)):
        try:
            channels = [int(channel) for channel in color[:3]]
        except (TypeError, ValueError):
            return (80, 80, 90)
        if len(channels) == 3:
            return (channels[0], channels[1], channels[2])
    return (80, 80, 90)


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


# ``common.font`` тоже headless на импорте (только dataclass/pathlib); PIL тянут
# лениво внутри ``FontResolver.resolve``.  Импортируем так же лениво, как selection.
_FONT_MODULE = None


def _ensure_font_module():
    """Lazily import the shared ``common.font`` module (no submodule at import)."""
    global _FONT_MODULE
    if _FONT_MODULE is None:
        import common.font as font  # noqa: PLC0415

        _FONT_MODULE = font
    return _FONT_MODULE


class LinearDashboardRenderer:
    """Stateful offscreen renderer: eases progress (value) and pulses a random selection."""

    # Лениво создаваемые шрифтовые инструменты (см. ``_ensure_font_tools``): до
    # первого вызова они ``None``, после — реальные объекты.  Аннотации — строки
    # (``from __future__ import annotations``), нужны только mypy, не тянут ``common``
    # на рантайме.
    _font_resolver: FontResolver | None = None
    _font_size_calculator: FontSizeCalculator | None = None
    _font_spec_cls: type[FontSpec] | None = None
    _font_family: str | None = None
    _label_font_family: str | None = None

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
        font_family: str | None = None,
        label_font_family: str | None = None,
        text_bold: bool = True,
        text_letter_spacing: float | None = None,
        label_text_bold: bool = False,
        label_text_bold_scale: float | None = None,
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
        # Шрифт подписей: семейство выбирается снаружи (общепринятый формат —
        # имя семейства), а фактический кегль подбирается ``FontSizeCalculator``
        # под габариты зоны подписи/значения.  Резолвер и калькулятор создаём
        # лениво (``common`` — сабмодуль, не должен требоваться на импорте).
        self._font_family = font_family
        self._font_resolver = None
        self._font_size_calculator = None
        # Два семейства: значения (проценты) и верхние подписи (буквы) могут
        # рисоваться разными шрифтами.  ``None`` → как у значений (текущее поведение).
        self._label_font_family = label_font_family
        # Жирный текст (нет Bold-актива у стилевого шрифта) — имитируем утолщением
        # глифа (stroke), чтобы надписи читались крупно и контрастно.
        self._text_bold = text_bold
        # Межсимвольный интервал (трекинг) как доля кегля: стилевой шрифт плотный,
        # без него символы слипаются.  ``None`` → константа по умолчанию.
        self._text_letter_spacing = (
            text_letter_spacing
            if text_letter_spacing is not None
            else TEXT_LETTER_SPACING_FRACTION
        )
        # Киберпанк-шрифт подписей широкий и наклонный: грубая утолщающая обводка
        # Жирность подписей: для Spacetron (тот же шрифт, что у значений) обводка
        # даёт тот же жирный вид, что у значений.  ``label_text_bold_scale`` — доля
        # жирности значений, чтобы подписи были чуть легче значений (кегль подписей
        # больше, поэтому без масштаба они выходят жирнее значений).
        self._label_text_bold = label_text_bold
        self._label_text_bold_scale = (
            label_text_bold_scale
            if label_text_bold_scale is not None
            else LABEL_TEXT_BOLD_SCALE
        )
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
        # Внешние переопределения внешнего вида колонок (обращение по id/индексу):
        # цвет заливки, цвет текста (подписи+значения) и сам текст/подпись.  Ключ —
        # уникальный ``id`` колонки, значения-``None`` отсутствуют, чтобы команды
        # «сбросить» могли убрать переопределение целиком.
        self._column_color: dict[str, tuple[int, int, int]] = {}
        self._column_text_color: dict[str, tuple[int, int, int]] = {}
        self._column_text: dict[str, str] = {}
        self._column_label: dict[str, str] = {}
        # Внешний API рассчитан на вызов из менеджера (его async-поток), тогда как
        # ``render`` выполняется в потоке отображения trcc.  Лёгкий лок защищает
        # переопределения и порядок ``_ids`` от гонки между ними.  ``RLock`` — потому
        # что сеттеры вызывают ``_resolve_column_id`` (тоже берущую лок) внутри блока.
        self._overrides_lock = threading.RLock()

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

    def _resolve_column_id(self, target) -> str:
        """Сопоставляет цель (уникальный id или индекс) с id колонки.

        ``str``-id принимается сразу: переопределение применится, как только колонка с
        таким id появится в ``setup`` (на следующем рендере).  ``int``-индекс требует
        хотя бы одного рендера — иначе порядок колонок ещё неизвестен.
        """
        if isinstance(target, str):
            return target
        index = int(target)
        with self._overrides_lock:
            ids = list(self._ids)
        if not ids:
            raise IndexError("column index requires a prior render (ids unknown); use an id")
        if 0 <= index < len(ids):
            return ids[index]
        raise IndexError(f"column index {index} out of range (have {len(ids)} columns)")

    def set_column(self, target, *, color=None, text_color=None,
                   text=None, label=None) -> None:
        """Перегруженный метод: задать цвет/текст колонки по id или индексу.

        ``target`` — уникальный ``str`` id колонки либо её ``int`` индекс (после первого
        рендера).  Любое из переопределений опционально: меняется только переданное,
        остальное остаётся прежним.  Раздельные методы (``set_color``/``set_text_color``/
        ``set_text``/``set_label``) позволяют менять один аспект; этот — все сразу.
        """
        column_id = self._resolve_column_id(target)
        with self._overrides_lock:
            if color is not None:
                self._column_color[column_id] = _coerce_rgb(color)
            if text_color is not None:
                self._column_text_color[column_id] = _coerce_rgb(text_color)
            if text is not None:
                self._column_text[column_id] = str(text)
            if label is not None:
                self._column_label[column_id] = str(label)

    def set_color(self, target, color) -> None:
        """Отдельный метод: задать цвет заливки колонки (по id или индексу)."""
        with self._overrides_lock:
            self._column_color[self._resolve_column_id(target)] = _coerce_rgb(color)

    def set_text_color(self, target, color) -> None:
        """Отдельный метод: задать цвет текста (подписи и значения) колонки."""
        with self._overrides_lock:
            self._column_text_color[self._resolve_column_id(target)] = _coerce_rgb(color)

    def set_text(self, target, text) -> None:
        """Переопределить отображаемое значение колонки (по id или индексу)."""
        with self._overrides_lock:
            self._column_text[self._resolve_column_id(target)] = str(text)

    def set_label(self, target, label) -> None:
        """Переопределить подпись колонки (по id или индексу)."""
        with self._overrides_lock:
            self._column_label[self._resolve_column_id(target)] = str(label)

    def clear_column(self, target) -> None:
        """Убрать все переопределения внешнего вида одной колонки (по id или индексу)."""
        column_id = self._resolve_column_id(target)
        with self._overrides_lock:
            for overrides in (self._column_color, self._column_text_color,
                              self._column_text, self._column_label):
                overrides.pop(column_id, None)

    def clear_columns(self) -> None:
        """Убрать все переопределения цветов/текстов колонок."""
        with self._overrides_lock:
            for overrides in (self._column_color, self._column_text_color,
                              self._column_text, self._column_label):
                overrides.clear()

    def _specs(self, setup: list[dict], values: dict, palette: dict) -> list[dict]:
        # Снимок переопределений под локом: рендер читает их в потоке отображения,
        # а сеттеры менеджера меняют параллельно.  Копия исключает гонку и сдвиг
        # ключей посреди сборки спецификаций.
        with self._overrides_lock:
            column_color = dict(self._column_color)
            column_label = dict(self._column_label)
            column_text = dict(self._column_text)
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
            # Внешнее переопределение по id имеет приоритет над автоматическим цветом.
            color = column_color.get(gid, color)
            specs.append(
                {
                    "id": gid,
                    "label": column_label.get(gid, g.get("label", gid)),
                    "text": column_text.get(gid, text),
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
                    focus_color: tuple[int, int, int],
                    override_color: tuple[int, int, int] | None = None) -> tuple[int, int, int]:
        """Цвет свечения выделенного бара.

        Внешнее переопределение цвета колонки (``override_color``) согласует свечение
        с заливкой вручную окрашенного бара.  Иначе при включённой цветовой палитре —
        цвет ТЕКУЩЕГО состояния бара (``value_color(progress)``); без палитры — прежний
        зелёный фокус-цвет.
        """
        if override_color is not None:
            return override_color
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
        with self._overrides_lock:
            if ids != self._ids:
                self._ids = ids
                self._progress = [0.0] * len(specs)
                self._progress_from = [0.0] * len(specs)
                self._progress_target = [0.0] * len(specs)
                self._progress_start = [0.0] * len(specs)
            bar_overrides = dict(self._column_color)

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
            # Внешнее переопределение по id приоритетнее анимации (статичный цвет).
            override_color = bar_overrides.get(specs[i]["id"])
            bar_color = (
                override_color
                if override_color is not None
                else (value_color(progress[i])
                      if self._value_palette_on
                      else specs[i]["color"])
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
                        glow_color = self._glow_color(
                            progress[i], focus_color,
                            bar_overrides.get(specs[i]["id"]),
                        )
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
            self._paint_text_pil(pil, specs, layout, vertical=self._bar_vertical)
        else:
            from PIL import ImageDraw

            font_resolver = self._ensure_font_tools()
            calc = self._font_size_calculator
            spec_cls = self._font_spec_cls
            assert calc is not None and spec_cls is not None and self._font_family is not None
            no_data_size = calc.fit(
                NO_DATA_TEXT, self._font_family,
                self._size[0] - 2 * self._no_data_text_pad_px,
                self._size[1] * NO_DATA_HEIGHT_FRACTION,
                min_size=calc.min_size,
            )
            no_data_font = font_resolver.resolve(
                spec_cls(self._font_family, no_data_size)
            )
            no_data_stroke = self._text_stroke(no_data_size)
            no_data_spacing = self._letter_spacing(no_data_size)
            d = ImageDraw.Draw(pil)
            if no_data_font is not None:
                no_data_x = self._size[0] // 2 - int(self._no_data_text_pad_px)
                no_data_y = (self._size[1] - int(self._no_data_text_pad_px)) // 2
                self._draw_tracked(d, NO_DATA_TEXT, no_data_x, no_data_y,
                                   no_data_font, LABEL_TEXT_COLOR_WHITE,
                                   no_data_spacing, no_data_stroke)
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

    def _ensure_font_tools(self) -> FontResolver:
        """Лениво создаёт резолвер шрифта, калькулятор кегля и семейство (и кэширует)."""
        if self._font_resolver is None:
            font_module = _ensure_font_module()
            from common.font_size import FontSizeCalculator  # noqa: PLC0415

            self._font_resolver = font_module.FontResolver()
            self._font_size_calculator = FontSizeCalculator(
                self._font_resolver,
                letter_spacing_fraction=self._text_letter_spacing,
            )
            self._font_family = self._font_family or font_module.DEFAULT_FONT_FAMILY
            self._label_font_family = self._label_font_family or self._font_family
            self._font_spec_cls = font_module.FontSpec
        assert (
            self._font_resolver is not None
            and self._font_size_calculator is not None
            and self._font_spec_cls is not None
        )
        return self._font_resolver

    def _column_text_bounds(self, geometry, vertical: bool) -> tuple[float, float]:
        """(ширина, высота) зоны текста: ширина — столбца, высота — доля его высоты.

        Для вертикальных столбцов ширина текста ограничена шириной столбца (надпись
        центрируется и не должна пересекать соседей); высота — долей высоты столбца
        (верх/низ).  Ограничение по высоте нужно, чтобы длинная надпись не занимала
        весь столбец; при равной раскладке зоны совпадают, берём минимум по всем.
        """
        widths = [int(g.width) for g in geometry]
        heights = [int(g.height) for g in geometry]
        if not widths or not heights:
            return 0.0, 0.0
        area_width: float = float(min(widths))
        area_height: float = float(min(heights))
        if vertical:
            area_height *= LABEL_AREA_HEIGHT_FRACTION
        else:
            area_width *= LABEL_AREA_WIDTH_FRACTION
        if self._text_bold:
            # Жирный (stroke) добавляет пару px с каждой стороны глифа — резервируем
            # запас, чтобы текст не вылезал за границу столбца/бара.
            area_width = max(0.0, area_width - TEXT_STROKE_MARGIN_PX)
            area_height = max(0.0, area_height - TEXT_STROKE_MARGIN_PX)
        return area_width, area_height

    def _shared_font_size(self, texts: list[str], family: str, geometry,
                          vertical: bool) -> int:
        """Общий кегль для набора ``texts``: минимум по ``fit`` каждого.

        Проход по всем надписям делает сам виджет (здесь, на стороне индикатора):
        самая длинная надпись задаёт наибольшее общее ограничение, чтобы «для всех
        надписей сверху» был один максимальный читаемый кегль.
        """
        area_width, area_height = self._column_text_bounds(geometry, vertical)
        if area_width <= 0 or not texts:
            return self._label_font_px
        calc = self._font_size_calculator
        if calc is None:
            return self._label_font_px
        sizes = [
            calc.fit(
                text, family, area_width, area_height,
                min_size=calc.min_size,
            )
            for text in texts
        ]
        return min(sizes) if sizes else self._label_font_px

    def _text_stroke(self, size: int, bold: bool | None = None,
                     scale: float = 1.0) -> int:
        """Ширина утолщающей обводки глифа (имитация жирного) для кегля ``size``.

        ``bold`` переопределяет глобальный ``text_bold`` (для подписей, у которых
        жирность задаётся отдельным ``label_text_bold``).  ``scale`` — доля от полной
        жирности (например подписи чуть легче значений).
        """
        bold = self._text_bold if bold is None else bold
        if not bold:
            return 0
        return max(1, round(size / TEXT_STROKE_WIDTH_DIVISOR * scale))

    def _letter_spacing(self, size: int) -> int:
        """Межсимвольный интервал (px) для кегля ``size``."""
        return int(round(size * self._text_letter_spacing))

    @staticmethod
    def _tracked_width(d, text: str, font, spacing: int) -> float:
        """Ширина ``text`` при посимвольной отрисовке с интервалом ``spacing`` (px)."""
        if spacing <= 0 or len(text) <= 1:
            return float(d.textlength(text, font=font))
        per_char = sum(d.textlength(ch, font=font) for ch in text)
        return float(per_char + spacing * (len(text) - 1))

    @staticmethod
    def _draw_tracked(d, text: str, x: float, y: float, font, fill,
                      spacing: int, stroke_width: int) -> None:
        """Рисует ``text`` посимвольно, вставляя ``spacing`` между глифами."""
        if spacing <= 0 or len(text) <= 1:
            d.text((x, y), text, fill=fill, font=font,
                   stroke_width=stroke_width, stroke_fill=fill)
            return
        caret = x
        for ch in text:
            d.text((caret, y), ch, fill=fill, font=font,
                   stroke_width=stroke_width, stroke_fill=fill)
            caret += d.textlength(ch, font=font) + spacing

    def _paint_text_pil(self, img: Image.Image, specs: list[dict],
                        layout, *, vertical: bool = False) -> None:
        from PIL import ImageDraw

        font_resolver = self._ensure_font_tools()
        spec_cls = self._font_spec_cls
        family = self._font_family
        label_family = self._label_font_family
        assert spec_cls is not None and family is not None and label_family is not None
        d = ImageDraw.Draw(img)
        if not specs:
            return
        labels = [spec["label"] for spec in specs if spec.get("label")]
        values = [spec["text"] for spec in specs if spec.get("text")] + [PERCENT_MAX_TEXT]
        label_size = self._shared_font_size(labels, label_family, layout.geometry, vertical)
        value_size = self._shared_font_size(values, family, layout.geometry, vertical)
        label_font = font_resolver.resolve(spec_cls(label_family, label_size))
        value_font = font_resolver.resolve(spec_cls(family, value_size))
        with self._overrides_lock:
            text_color_by_id = dict(self._column_text_color)
        label_stroke = self._text_stroke(label_size, self._label_text_bold,
                                     self._label_text_bold_scale)
        value_stroke = self._text_stroke(value_size)
        label_spacing = self._letter_spacing(label_size)
        value_spacing = self._letter_spacing(value_size)
        for spec, geo in zip(specs, layout.geometry, strict=True):
            # Внешнее переопределение цвета текста по id (по умолчанию белый).
            text_color = text_color_by_id.get(spec.get("id", ""), LABEL_TEXT_COLOR_WHITE)
            if vertical:
                # Подпись — по центру X столбца: метка у верхнего края, значение у
                # нижнего, чтобы стек заливки (снизу вверх/сверху вниз) не перекрыл текст.
                center_x = int(geo.x + geo.width / 2.0)
                bar_top = int(geo.y)
                bar_bottom = int(geo.y + geo.height)
                if spec.get("label") and label_font is not None:
                    label_w = self._tracked_width(d, spec["label"], label_font, label_spacing)
                    self._draw_tracked(d, spec["label"],
                                       center_x - label_w / 2.0, bar_top + self._label_pad_y_px,
                                       label_font, text_color, label_spacing, label_stroke)
                if spec.get("text") and value_font is not None:
                    value_w = self._tracked_width(d, spec["text"], value_font, value_spacing)
                    self._draw_tracked(d, spec["text"],
                                       center_x - value_w / 2.0,
                                       bar_bottom - self._label_pad_y_px - value_size,
                                       value_font, text_color, value_spacing, value_stroke)
                continue
            center_y = int(geo.center_y)
            bar_left = int(geo.x)
            bar_length = int(geo.width)
            if spec.get("label") and label_font is not None:
                self._draw_tracked(d, spec["label"],
                                   bar_left + self._label_pad_x_px, center_y - self._label_pad_y_px,
                                   label_font, text_color, label_spacing, label_stroke)
            if spec.get("text") and value_font is not None:
                value_w = self._tracked_width(d, spec["text"], value_font, value_spacing)
                self._draw_tracked(d, spec["text"],
                                   bar_left + bar_length - self._label_pad_x_px - value_w,
                                   center_y - self._label_pad_y_px,
                                   value_font, text_color, value_spacing, value_stroke)

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
        from PIL import Image, ImageChops, ImageFilter

        if fill_direction is None:
            fill_direction = FILL_DIRECTION_LEFT_TO_RIGHT
        if fill_direction not in ALLOWED_FILL_DIRECTIONS:
            fill_direction = FILL_DIRECTION_LEFT_TO_RIGHT
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
        # Капсулу рисуем Qt-QPainter с антиалиасингом (тот же хелпер, что и маску
        # региона заливки), а не ``ImageDraw.rounded_rectangle``: Pillow не сглаживает
        # кромку, из-за чего у скругления ПОДСВЕЧЕННОГО столбца ореол «шакалится».
        silhouette = _rounded_region_mask(
            bar_rect, box_left, box_top, box_width, box_height,
            track_radius, track_radius, track_radius, track_radius,
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
LABEL_TEXT_COLOR_WHITE = (255, 255, 255)
LABEL_PAD_X = 14
LABEL_PAD_Y = 16
# Доля зоны столбца, отведённая под текст: ширина — для горизонтального бара,
# высота — для вертикального столбца (верх/низ).  Надпись не должна занимать весь
# столбец/бар, поэтому кегль ограничен и шириной, и этой долей.
LABEL_AREA_WIDTH_FRACTION = 0.4
LABEL_AREA_HEIGHT_FRACTION = 0.5
# Максимальное значение-процент: гарантирует, что "100%" читается даже при
# частично заполненном баре (эталон ширины для общего кегля значений).
PERCENT_MAX_TEXT = "100%"
# Имитация «жирного» через утолщающую обводку глифа (stroke): делитель кегля для
# ширины обводки и запас по краям, который резервируем, чтобы текст оставался
# внутри столбца/бара после утолщения.
TEXT_STROKE_WIDTH_DIVISOR = 14
# Доля жирности значений для подписей: кегль подписей больше, поэтому без масштаба
# обводка выходила бы жирнее значений.  0.6 → чуть легче жирности значений.
LABEL_TEXT_BOLD_SCALE = 0.6
TEXT_STROKE_MARGIN_PX = 4
# Межсимвольный интервал (трекинг) как доля кегля: стилевой шрифт плотный, без
# трекинга символы слипаются.  Доля, а не фикс. px, чтобы интервал рос вместе с кеглем.
TEXT_LETTER_SPACING_FRACTION = 0.12

# Заглушка «нет данных».
NO_DATA_FONT_SIZE = 30
NO_DATA_TEXT = "no data"
NO_DATA_TEXT_PAD = 34
NO_DATA_HEIGHT_FRACTION = 0.2
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
