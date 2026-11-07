# SPDX-License-Identifier: MIT
"""Тесты выбора «через раз» в Qt linear-индикаторе.

Выбор меняется вместе со сменой данных: ``_advance_selection`` открывает новое окно
(чередуя выбрать случайный бар / пропустить), а ``_selection_focus`` держит выделение
всё окно (~5 с), не мигая.  Проверяем чередование, удержание, случайность позиции и
выключение выбора при статичной раскладке (``StaticLayoutManager``).
"""
from __future__ import annotations

import random

import pytest

from hardwarescreen.adapter import linear_dashboard as ld
from hardwarescreen.adapter import selection_strategy as ss
from hardwarescreen.adapter.dashboard import value_color
from hardwarescreen.adapter.layout import BarGeometry, StaticLayoutManager
from hardwarescreen.adapter.linear_dashboard import LinearDashboardRenderer
from hardwarescreen.adapter.selection_strategy import (
    SelectMultipleIndicators,
    SelectSingleIndicator,
    count_selected,
    select_strategy,
)

BARS = 6
PANEL_W = 1000.0
PANEL_H = 400.0
EDGE_MARGIN = 14.0
SELECTION_SCALE = 1.22


class _FakeQRect:
    """Заменитель ``QRectF`` для стратегий без Qt: хранит и отдаёт геометрию."""

    def __init__(self, x: float, y: float, width: float, height: float) -> None:
        self._x = x
        self._y = y
        self._width = width
        self._height = height

    def x(self) -> float:
        return self._x

    def y(self) -> float:
        return self._y

    def width(self) -> float:
        return self._width

    def height(self) -> float:
        return self._height


@pytest.fixture(autouse=True)
def _geometry_without_qt(monkeypatch):
    """Стратегии выбора работают без Qt: на время тестов файла подменяем ``QRectF``
    на ``_FakeQRect`` и возвращаем реальный по завершении, чтобы не «протекать» в
    другие тест-модули (которые используют настоящий Qt-рендерер)."""
    monkeypatch.setattr(ss, "_QRECTF", _FakeQRect)


def _vertical_geometry(centers: list[float], width: float = 80.0,
                       height: float = 372.0, top: float = EDGE_MARGIN):
    """Вертикальные столбцы: ``BarGeometry`` по центрам X, одинаковой толщиной."""
    return [BarGeometry(cx - width / 2.0, top, width, height) for cx in centers]


class _FakeRect:
    """Минимальный заменитель ``QRectF`` для чистой геометрии (ширина/высота)."""

    def __init__(self, width: float, height: float) -> None:
        self._width = width
        self._height = height

    def width(self) -> float:
        return self._width

    def height(self) -> float:
        return self._height


def test_alternating_selection_selects_every_other_window():
    """Через раз: окна чередуются выбор/пропуск ровно по очереди."""
    renderer = LinearDashboardRenderer((1920, 440), rng=random.Random(3))
    flags = []
    for _ in range(8):
        renderer._advance_selection(0.0, BARS)
        flags.append(renderer._sel_window_is_select)
    assert flags == [True, False, True, False, True, False, True, False]


def test_selection_holds_through_the_window():
    """Выделение держится всё окно (не мигает): полный фокус на 1с/2с/4с."""
    renderer = LinearDashboardRenderer((1920, 440), rng=random.Random(5))
    now = 0.0
    renderer._advance_selection(now, BARS)
    index = renderer._sel_index
    assert index is not None and renderer._sel_window_is_select is True
    for sample_at in (1.0, 2.0, 4.0):
        focus = renderer._selection_focus(now + sample_at, BARS)
        assert focus[index] == 1.0, (sample_at, focus[index])
    # В окне-пропуске предыдущий пульс гаснет к нулю и остаётся выключенным.
    window_plus = 5.0
    renderer._advance_selection(now + window_plus, BARS)
    assert renderer._sel_window_is_select is False
    focus = renderer._selection_focus(now + window_plus + 3.0, BARS)
    assert focus[index] == 0.0, focus[index]


def test_selection_picks_various_positions():
    """Позиция выбранного бара случайна — не один и тот же бар."""
    renderer = LinearDashboardRenderer((1920, 440), rng=random.Random(7))
    now = 0.0
    picks: set[int] = set()
    for _ in range(60):
        renderer._advance_selection(now, BARS)
        if renderer._sel_window_is_select and renderer._sel_index is not None:
            picks.add(renderer._sel_index)
    assert len(picks) >= 3, picks


def test_selection_off_for_static_layout():
    """При StaticLayoutManager фокус всегда нулевой — индикаторы видны в полную яркость."""
    renderer = LinearDashboardRenderer(
        (1920, 440), layout=StaticLayoutManager(), rng=random.Random(5)
    )
    renderer._advance_selection(0.0, BARS)
    assert renderer._sel_index is None
    for sample_at in (0.0, 1.0, 4.0):
        focus = renderer._selection_focus(0.0 + sample_at, BARS)
        assert all(v == 0.0 for v in focus)


def test_content_signature_changes_on_value_update():
    """Сигнатура содержимого меняется при смене значений — триггер нового окна."""
    sig_a = LinearDashboardRenderer._content_signature(
        [{"id": "cpu", "label": "CPU", "min": 0, "max": 100, "unit": "%"}],
        {"cpu": 10},
        {"accent": "#00c85a"},
    )
    sig_b = LinearDashboardRenderer._content_signature(
        [{"id": "cpu", "label": "CPU", "min": 0, "max": 100, "unit": "%"}],
        {"cpu": 90},
        {"accent": "#00c85a"},
    )
    assert sig_a != sig_b


def test_value_tween_runs_to_target_in_three_hundred_ms():
    """Бар «добегает» до нового значения за 300 мс, а не за 5 с."""
    renderer = LinearDashboardRenderer((1920, 440))
    assert renderer._vtween == 0.3
    # стартовое 0, цель 0.8; в 0.1 с ещё в пути, в 0.3 с уже на цели и держится.
    renderer._eased_progress(0, 0.8, 0.0)
    assert renderer._eased_progress(0, 0.8, 0.1) < 0.8
    assert abs(renderer._eased_progress(0, 0.8, 0.3) - 0.8) < 1e-6
    assert abs(renderer._eased_progress(0, 0.8, 1.0) - 0.8) < 1e-6


def test_fill_direction_default_is_left_to_right():
    """По умолчанию столбцы заполняются слева направо (как в сабмодуле)."""
    renderer = LinearDashboardRenderer((1920, 440))
    assert renderer._fill_direction == "left_to_right"


def test_fill_direction_right_to_left_is_accepted_and_stored():
    """Направление «справа-налево» принимается и сохраняется на рендерере."""
    renderer = LinearDashboardRenderer((1920, 440), fill_direction="right_to_left")
    assert renderer._fill_direction == "right_to_left"


def test_fill_direction_unknown_value_rejected():
    """Неизвестное направление отклоняется с ValueError (как в сабмодуле)."""
    with pytest.raises(ValueError):
        LinearDashboardRenderer((1920, 440), fill_direction="diagonal")


def test_fill_direction_vertical_detection():
    """Вертикальные столбцы определяются по направлению заполнения."""
    assert LinearDashboardRenderer._bar_is_vertical("bottom_to_top") is True
    assert LinearDashboardRenderer._bar_is_vertical("top_to_bottom") is True
    assert LinearDashboardRenderer._bar_is_vertical("left_to_right") is False
    assert LinearDashboardRenderer._bar_is_vertical("right_to_left") is False


def test_fill_direction_vertical_values_accepted_and_stored():
    """Вертикальные направления принимаются и сохраняются на рендерере."""
    for direction in ("bottom_to_top", "top_to_bottom"):
        renderer = LinearDashboardRenderer((1920, 440), fill_direction=direction)
        assert renderer._fill_direction == direction
        assert renderer._bar_vertical is True


def test_bar_corner_fraction_defaults_to_full_capsule():
    """По умолчанию капсула — половина толщины (гориз. по высоте, верт. по ширине)."""
    assert ld._bar_track_radius(_FakeRect(300, 40), False, None) == 20.0
    assert ld._bar_track_radius(_FakeRect(60, 300), True, None) == 30.0


def test_bar_corner_fraction_is_fraction_of_thickness():
    """Радиус — доля толщины столбца, зажатая в [0, 0.5]."""
    assert ld._bar_track_radius(_FakeRect(300, 40), False, 0.3) == 12.0
    assert ld._bar_track_radius(_FakeRect(300, 40), False, 0.0) == 0.0
    assert ld._bar_track_radius(_FakeRect(300, 40), False, 0.6) == 20.0  # клип до 0.5
    assert ld._bar_track_radius(_FakeRect(60, 300), True, 0.25) == 15.0


def test_fill_corner_radii_per_direction():
    """Порядок углов (tl, tr, br, bl): конец у начала — ``track_radius``, граница
    роста — ``fill_radius``; направление задаёт, какие углы какому принадлежат."""
    assert ld._fill_corner_radii("left_to_right", 10.0, 6.0) == (10.0, 6.0, 6.0, 10.0)
    assert ld._fill_corner_radii("right_to_left", 10.0, 6.0) == (6.0, 10.0, 10.0, 6.0)
    assert ld._fill_corner_radii("bottom_to_top", 10.0, 6.0) == (6.0, 6.0, 10.0, 10.0)
    assert ld._fill_corner_radii("top_to_bottom", 10.0, 6.0) == (10.0, 10.0, 6.0, 6.0)


def test_fill_boundary_radius_default_flat_unless_full():
    """По умолчанию граница роста плоская, но при 100% повторяет капсулу (как раньше)."""
    assert ld._fill_boundary_radius(0.5, 40.0, None) == 0.0
    assert ld._fill_boundary_radius(1.0, 40.0, None) == 20.0
    # Явная доля используется независимо от заполнения.
    assert ld._fill_boundary_radius(0.5, 40.0, 0.2) == 8.0
    assert ld._fill_boundary_radius(1.0, 40.0, 0.2) == 8.0
    assert ld._fill_boundary_radius(0.5, 40.0, 0.0) == 0.0
    assert ld._fill_boundary_radius(0.5, 40.0, 0.6) == 20.0  # клип до 0.5


def test_renderer_stores_optional_corner_fractions():
    """Опциональные радиусы-доли сохраняются на рендерере (или ``None`` по умолчанию)."""
    renderer = LinearDashboardRenderer(
        (1920, 440), bar_corner_fraction=0.3, fill_corner_fraction=0.2
    )
    assert renderer._bar_corner_fraction == 0.3
    assert renderer._fill_corner_fraction == 0.2
    default = LinearDashboardRenderer((1920, 440))
    assert default._bar_corner_fraction is None
    assert default._fill_corner_fraction is None


def test_value_color_is_red_at_low_and_green_when_full():
    """Солид-цвет колонки из шкалы «значение → цвет»: 0..10% — красный, 100% — зелёный."""
    red = value_color(0.0)
    green = value_color(1.0)
    assert red == (255, 0, 0)
    assert green == (0, 255, 0)
    # Порог 10% — всё ещё красный; при 100% — чисто зелёный.
    assert value_color(0.10) == red
    assert value_color(0.95) != green


def test_value_color_ramps_monotonically_to_green():
    """Между 10% и 100% цвет растёт от красного к зелёному плавно и монотонно."""

    def greenness(color):
        return color[1] - color[0]  # доминанта зелёного над красным

    prev = greenness(value_color(0.10))
    assert prev < 0  # в начале шкалы красный доминирует
    for f in (0.3, 0.5, 0.7, 0.9, 1.0):
        cur = greenness(value_color(f))
        assert cur >= prev, (f, prev, cur)
        prev = cur
    assert greenness(value_color(1.0)) > 0  # в конце зелёный доминирует


def test_specs_color_follows_value_not_accent():
    """Цвет бара определяется его значением (шкала «значение → цвет»), а не палитрой."""
    renderer = LinearDashboardRenderer((1920, 440))
    specs = renderer._specs(
        [
            {"id": "a", "label": "A", "min": 0, "max": 100, "unit": "%"},
            {"id": "b", "label": "B", "min": 0, "max": 100, "unit": "%"},
        ],
        {"a": 0, "b": 100},
        {"accent": (79, 156, 249)},
    )
    assert specs[0]["color"] == value_color(0.0)
    assert specs[1]["color"] == value_color(1.0)
    assert specs[0]["color"] != specs[1]["color"]


def test_specs_color_is_accent_when_value_palette_off():
    """При выключенной палитре цвет бара — общий акцент, а не цвет значения."""
    renderer = LinearDashboardRenderer((1920, 440), value_palette_on=False)
    specs = renderer._specs(
        [{"id": "a", "label": "A", "min": 0, "max": 100, "unit": "%"}],
        {"a": 5.0},
        {"accent": (79, 156, 249)},
    )
    assert specs[0]["color"] == (79, 156, 249)  # акцент по умолчанию (#4f9cf9)
    assert specs[0]["color"] != value_color(0.05)


def test_glow_color_uses_state_color_when_palette_on():
    """Свечение выделенного бара — цвет его текущего состояния при включённой палитре."""
    renderer = LinearDashboardRenderer((1920, 440), value_palette_on=True)
    focus = (0, 200, 90)  # зелёный фокус-цвет
    assert renderer._glow_color(0.4, focus) == value_color(0.4)
    assert renderer._glow_color(0.4, focus) != focus


def test_glow_color_is_focus_when_palette_off():
    """Без палитры свечение остаётся зелёным фокус-цветом (прежнее поведение)."""
    renderer = LinearDashboardRenderer((1920, 440), value_palette_on=False)
    focus = (0, 200, 90)
    assert renderer._glow_color(0.6, focus) == focus


def test_track_color_uniform_when_palette_on():
    """Подложка (задний фон) столбцов — единый нейтральный цвет при включённой палитре."""
    renderer = LinearDashboardRenderer((1920, 440), value_palette_on=True)
    assert renderer._track_color({"muted": (138, 147, 166)}) == (138, 147, 166)


def test_track_color_none_when_palette_off():
    """Без палитры подложка берётся из цвета самой заливки (``None``)."""
    renderer = LinearDashboardRenderer((1920, 440), value_palette_on=False)
    assert renderer._track_color({"muted": (138, 147, 166)}) is None


# --- Стратегии выделения (выбор + рост нескольких индикаторов) ---


def test_strategy_selects_single_when_up_to_one_selected():
    """≤1 выбранной → ``SelectSingleIndicator``; >1 → ``SelectMultipleIndicators``."""
    assert isinstance(select_strategy([], True), SelectSingleIndicator)
    assert isinstance(select_strategy([1.0], True), SelectSingleIndicator)
    assert isinstance(select_strategy([1.0, 0.0], True), SelectSingleIndicator)
    assert isinstance(select_strategy([1.0, 1.0], True), SelectMultipleIndicators)
    assert isinstance(select_strategy([1.0, 0.0, 1.0], True), SelectMultipleIndicators)
    # Выключенное выделение — всегда одиночная стратегия (рост всё равно не применяется).
    assert isinstance(select_strategy([1.0, 1.0], False), SelectSingleIndicator)


def test_count_selected_counts_only_lit_bars():
    """Считаются только бары со строго положительным фокусом."""
    assert count_selected([0.0, 1.0, 0.0, 1.0]) == 2
    assert count_selected([0.0, 0.0, 0.0]) == 0
    assert count_selected([1.0]) == 1


def test_renderer_select_strategy_picks_by_selected_count():
    """Рендерер выбирает ту же стратегию по числу выбранных, что и фабрика."""
    renderer = LinearDashboardRenderer((1920, 440))
    assert renderer._select_strategy([1.0, 0.0], True) is renderer._strategy_single
    assert renderer._select_strategy([1.0, 1.0], True) is renderer._strategy_multiple
    assert renderer._select_strategy([1.0, 0.0], False) is renderer._strategy_single


def test_renderer_resolves_positive_px_per_mm():
    """px_per_mm выводится из диагонали и остаётся в разумном диапазоне (11.3")."""
    renderer = LinearDashboardRenderer((1920, 440))
    px_per_mm = renderer._resolve_px_per_mm((1920, 440))
    assert px_per_mm > 0
    assert 6.0 < px_per_mm < 8.0


def test_multiple_strategy_non_adjacent_grows_like_single():
    """Несоседние выбранные растут на полный ``selection_scale`` (как раньше)."""
    geometry = _vertical_geometry([200.0, 400.0, 600.0, 800.0])
    focus = [1.0, 0.0, 1.0, 0.0]  # бары 0 и 2 — несоседние
    single = SelectSingleIndicator().compute(
        focus, geometry, PANEL_W, PANEL_H, EDGE_MARGIN, SELECTION_SCALE, True, 20.0
    )
    multiple = SelectMultipleIndicators().compute(
        focus, geometry, PANEL_W, PANEL_H, EDGE_MARGIN, SELECTION_SCALE, True, 20.0
    )
    for i in range(len(geometry)):
        assert multiple[i].width() == pytest.approx(single[i].width())
        assert multiple[i].height() == pytest.approx(single[i].height())
        assert multiple[i].x() == pytest.approx(single[i].x())
        assert multiple[i].y() == pytest.approx(single[i].y())
    # Выбранные бара растут на 1.22, невыбранные остаются базовой толщиной.
    assert multiple[0].width() == pytest.approx(80.0 * SELECTION_SCALE)
    assert multiple[2].width() == pytest.approx(80.0 * SELECTION_SCALE)
    assert multiple[1].width() == pytest.approx(80.0)


def test_multiple_strategy_adjacent_trims_growth_and_keeps_min_gap():
    """Соседние выбранные растут меньше, оставляя зазор не менее ``min_gap_px``."""
    geometry = _vertical_geometry([300.0, 410.0, 520.0, 630.0])
    focus = [1.0, 1.0, 0.0, 0.0]  # бары 0 и 1 — соседние
    rects = SelectMultipleIndicators().compute(
        focus, geometry, PANEL_W, PANEL_H, EDGE_MARGIN, SELECTION_SCALE, True, 20.0
    )
    # Зазор базы 30 px, до 20 px достаточно; рост урезан с 1.22 до 1.125.
    assert rects[0].width() == pytest.approx(80.0 * 1.125)
    assert rects[1].width() == pytest.approx(80.0 * 1.125)
    gap = rects[1].x() - (rects[0].x() + rects[0].width())
    assert gap >= 20.0
    # Урезанный рост меньше полного scale.
    assert rects[0].width() < 80.0 * SELECTION_SCALE


def test_multiple_strategy_boundary_zero_growth_when_gap_too_small():
    """Если зазор и без роста уже < ``min_gap_px`` — рост пары нулевой."""
    geometry = _vertical_geometry([300.0, 390.0, 480.0, 570.0])
    focus = [1.0, 1.0, 0.0, 0.0]  # бары 0 и 1 — соседние, зазор 10 px
    rects = SelectMultipleIndicators().compute(
        focus, geometry, PANEL_W, PANEL_H, EDGE_MARGIN, SELECTION_SCALE, True, 20.0
    )
    # Рост не применяется: толщина остаётся базовой, зазор так и остаётся 10 px.
    assert rects[0].width() == pytest.approx(80.0)
    assert rects[1].width() == pytest.approx(80.0)
    assert rects[0].width() < 80.0 * SELECTION_SCALE


def test_multiple_strategy_horizontal_adjacent_trims_along_height():
    """Горизонтальные полосы растут по ВЫСОТЕ; зазор меряется по Y — как по оси роста."""
    geometry = [
        BarGeometry(100.0, 60.0, 800.0, 80.0),   # центр Y = 100
        BarGeometry(100.0, 170.0, 800.0, 80.0),  # центр Y = 210 (зазор 30 px)
    ]
    focus = [1.0, 1.0]
    rects = SelectMultipleIndicators().compute(
        focus, geometry, PANEL_W, PANEL_H, EDGE_MARGIN, SELECTION_SCALE, False, 20.0
    )
    # Рост в высоту (1.125), ширина не меняется.
    assert rects[0].height() == pytest.approx(80.0 * 1.125)
    assert rects[0].width() == pytest.approx(800.0)
    gap = rects[1].y() - (rects[0].y() + rects[0].height())
    assert gap >= 20.0


def test_renderer_density_scales_design_sizes():
    # density=2.0 → дизайн-константы шрифта/отступов/свечения/«нет данных» удваиваются;
    # при density=1.0 они остаются теми же px (обратная совместимость).
    renderer = LinearDashboardRenderer((1920, 440), density=2.0)
    assert renderer._label_font_px == 52
    assert renderer._label_pad_x_px == 28
    assert renderer._label_pad_y_px == 32
    assert renderer._no_data_font_px == 60
    assert renderer._glow_margin_px == 96
    assert renderer._glow_blur_radius_px == 32

    default = LinearDashboardRenderer((1920, 440))
    assert default._label_font_px == 26
    assert default._label_pad_x_px == 14
    assert default._label_pad_y_px == 16
    assert default._no_data_font_px == 30
    assert default._glow_margin_px == 48
    assert default._glow_blur_radius_px == 16


def _layout_result(geometry: list):
    from hardwarescreen.adapter.layout import LayoutResult

    return LayoutResult(geometry=tuple(geometry), bar_width=geometry[0].width)


def test_shared_font_size_fits_longest_label_in_column():
    # Один общий кегль для всех подписей: он обязан быть таким, чтобы даже самая
    # длинная надпись не выходила за ширину столбца (иначе наезжает на соседа).
    renderer = LinearDashboardRenderer((1000, 400), density=1.0)
    renderer._ensure_font_tools()
    geom = _vertical_geometry([120.0, 320.0, 520.0, 720.0, 920.0],
                              width=69.0, height=412.0)
    labels = ["CPU", "GPU", "100%"]
    size = renderer._shared_font_size(labels, "Spacetron", geom, True)
    calc = renderer._font_size_calculator
    for label in labels:
        assert calc.measure_width(label, "Spacetron", size) <= 69.0


def test_paint_text_pil_draws_labels_white_and_within_column():
    # Надписи всегда белые и не выходят за ширину столбца.
    from PIL import Image

    renderer = LinearDashboardRenderer((1000, 400), density=1.0)
    renderer._ensure_font_tools()
    geom = _vertical_geometry([120.0, 340.0], width=69.0, height=412.0)
    layout = _layout_result(geom)
    img = Image.new("RGB", (1000, 400), (0, 0, 0))
    specs = [{"label": "CPU", "text": "42%"}, {"label": "GPU", "text": "100%"}]
    renderer._paint_text_pil(img, specs, layout, vertical=True)
    pixels = img.load()
    lab_size = renderer._shared_font_size([s["label"] for s in specs],
                                          "Spacetron", geom, True)
    for spec in specs:
        assert renderer._font_size_calculator.measure_width(
            spec["label"], "Spacetron", lab_size) <= 69.0
    # Белые надписи на чёрном фоне — в кадре появляются чисто белые пиксели.
    assert any(pixels[x, y] == (255, 255, 255)
               for y in range(400) for x in range(1000))


def test_renderer_font_family_defaults_to_spacetron():
    renderer = LinearDashboardRenderer((1920, 440), density=1.0)
    renderer._ensure_font_tools()
    assert renderer._font_family == "Spacetron"


def test_renderer_accepts_custom_font_family():
    renderer = LinearDashboardRenderer((1920, 440), density=1.0, font_family="Custom")
    renderer._ensure_font_tools()
    assert renderer._font_family == "Custom"


def test_letter_spacing_separates_glyphs():
    # Трекинг добавляет межсимвольный интервал: измеренная ширина больше базовой,
    # чтобы плотный стилевой шрифт не слипался.
    from common.font_size import FontSizeCalculator

    renderer = LinearDashboardRenderer((1000, 400), density=1.0, text_letter_spacing=0.2)
    renderer._ensure_font_tools()
    calc = renderer._font_size_calculator
    size = calc.fit_to_width("100%", "Spacetron", 200.0)
    raw = FontSizeCalculator().measure_width("100%", "Spacetron", size)
    tracked = calc.measure_width("100%", "Spacetron", size)
    assert tracked > raw


def test_letter_spacing_default_is_applied():
    renderer = LinearDashboardRenderer((1000, 400), density=1.0)
    renderer._ensure_font_tools()
    assert renderer._text_letter_spacing == ld.TEXT_LETTER_SPACING_FRACTION


def test_label_font_family_defaults_to_value_font():
    renderer = LinearDashboardRenderer((1000, 400), density=1.0)
    renderer._ensure_font_tools()
    assert renderer._label_font_family == "Spacetron"


def test_label_font_family_can_differ_from_values():
    renderer = LinearDashboardRenderer(
        (1000, 400), density=1.0, font_family="Spacetron", label_font_family="Cyberpunk"
    )
    renderer._ensure_font_tools()
    assert renderer._label_font_family == "Cyberpunk"
    # Значения остаются в базовом семействе; подписи — в отдельном.
    assert renderer._font_family == "Spacetron"


def test_coerce_rgb_accepts_hex_and_tuple():
    """Цвет колонки принимается и как '#rrggbb', и как тройка (r, g, b)."""
    assert ld._coerce_rgb("#ff0000") == (255, 0, 0)
    assert ld._coerce_rgb("#00ff00") == (0, 255, 0)
    assert ld._coerce_rgb((12, 34, 56)) == (12, 34, 56)
    # Невалидная строка/null откатываются к нейтральному (как hex_to_rgb).
    assert ld._coerce_rgb("oops") == (80, 80, 90)


def test_set_color_by_id_overrides_specs_color():
    """Цвет колонки задаётся по id и перекрывает автоматический цвет значения."""
    renderer = LinearDashboardRenderer((1920, 440))
    renderer.set_color("a", "#ff0000")
    specs = renderer._specs(
        [{"id": "a", "label": "A", "min": 0, "max": 100, "unit": "%"}],
        {"a": 100.0},
        {"accent": (79, 156, 249)},
    )
    assert specs[0]["color"] == (255, 0, 0)


def test_set_text_and_label_by_id_override_specs():
    """Текст значения и подпись задаются по id и не зависят от данных."""
    renderer = LinearDashboardRenderer((1920, 440))
    renderer.set_text("a", "99°")
    renderer.set_label("a", "MAG")
    specs = renderer._specs(
        [{"id": "a", "label": "A", "min": 0, "max": 100, "unit": "%"}],
        {"a": 100.0},
        {"accent": (79, 156, 249)},
    )
    assert specs[0]["text"] == "99°"
    assert specs[0]["label"] == "MAG"


def test_set_color_by_index_seeds_from_render():
    """Цвет колонки можно задать по индексу после того, как известен порядок id."""
    renderer = LinearDashboardRenderer((1920, 440))
    renderer._ids = ["a", "b"]  # порядок колонок известен (был первый рендер)
    renderer.set_color(1, (0, 255, 0))
    specs = renderer._specs(
        [{"id": "a", "label": "A", "min": 0, "max": 100, "unit": "%"},
         {"id": "b", "label": "B", "min": 0, "max": 100, "unit": "%"}],
        {"a": 0.0, "b": 0.0},
        {"accent": (79, 156, 249)},
    )
    assert specs[1]["color"] == (0, 255, 0)
    assert specs[0]["color"] == value_color(0.0)


def test_set_column_overloaded_sets_all_or_part():
    """Перегруженный set_column меняет только переданные аспекты колонки."""
    renderer = LinearDashboardRenderer((1920, 440))
    renderer.set_column("a", color="#ff0000", text="50%", label="CPU", text_color="#00ff00")
    renderer.set_column("a", label="GPU")  # только подпись, остальное сохраняется
    specs = renderer._specs(
        [{"id": "a", "label": "A", "min": 0, "max": 100, "unit": "%"}],
        {"a": 0.0},
        {"accent": (79, 156, 249)},
    )
    assert specs[0]["color"] == (255, 0, 0)
    assert specs[0]["text"] == "50%"
    assert specs[0]["label"] == "GPU"
    # Цвет текста не виден в _specs — он задан отдельным переопределением.
    assert renderer._column_text_color["a"] == (0, 255, 0)


def test_resolve_column_id_requires_render_for_index():
    """Индекс без известного порядка колонок отклоняется; id принимается сразу."""
    renderer = LinearDashboardRenderer((1920, 440))
    assert renderer._resolve_column_id("a") == "a"
    with pytest.raises(IndexError):
        renderer._resolve_column_id(0)
    renderer._ids = ["a", "b"]
    assert renderer._resolve_column_id(1) == "b"
    with pytest.raises(IndexError):
        renderer._resolve_column_id(5)


def test_clear_column_and_clear_columns_remove_overrides():
    """Сброс колонки убирает все её переопределения; clear_columns — все сразу."""
    renderer = LinearDashboardRenderer((1920, 440))
    renderer._ids = ["a", "b"]
    renderer.set_column("a", color="#ff0000", text="50%")
    renderer.set_column("b", color="#00ff00")
    renderer.clear_column(0)
    assert "a" not in renderer._column_color
    assert "a" not in renderer._column_text
    assert "b" in renderer._column_color
    renderer.clear_columns()
    assert renderer._column_color == {}
    assert renderer._column_text_color == {}
    assert renderer._column_text == {}
    assert renderer._column_label == {}


def test_paint_text_pil_uses_per_column_text_color():
    """Цвет текста задаётся по id: нужная колонка рисуется заданным цветом."""
    from PIL import Image

    renderer = LinearDashboardRenderer((1000, 400), density=1.0)
    renderer._ensure_font_tools()
    renderer.set_text_color("a", "#00ff00")
    geom = _vertical_geometry([120.0, 340.0], width=69.0, height=412.0)
    layout = _layout_result(geom)
    img = Image.new("RGB", (1000, 400), (0, 0, 0))
    specs = [{"id": "a", "label": "A", "text": "42%"},
             {"id": "b", "label": "B", "text": "100%"}]
    renderer._paint_text_pil(img, specs, layout, vertical=True)
    pixels = img.load()
    # Зелёный текст колонки "a" (заданный цвет) и белый текст колонки "b" (по умолчанию).
    assert any(pixels[x, y] == (0, 255, 0)
               for y in range(400) for x in range(1000))
    assert any(pixels[x, y] == (255, 255, 255)
               for y in range(400) for x in range(1000))



