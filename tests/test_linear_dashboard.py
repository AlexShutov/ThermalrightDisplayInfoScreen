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


ss._QRECTF = _FakeQRect


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



