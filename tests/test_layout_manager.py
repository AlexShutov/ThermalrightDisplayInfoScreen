# SPDX-License-Identifier: MIT
from hardwarescreen.adapter.layout import (
    AutoGapLayoutManager,
    ExplicitGapLayoutManager,
    SelectionLayoutManager,
    StaticLayoutManager,
)

# Defaults mirror the 1920x440 panel (~2 mm edge margin ≈ 14 px at ~68 px/cm).
W, H = 1920, 440
EDGE = 14.0  # SelectionLayoutManager()._edge_margin (2 mm)


def _gap(lm, n=6):
    return lm.gap_for(H, n)


# -- StaticLayoutManager -----------------------------------------------------
def test_static_equal_heights_and_within_margins():
    lm = StaticLayoutManager()
    r = lm.measure(W, H, 6)
    assert len(r) == 6
    heights = [g.height for g in r.geometry]
    assert max(heights) - min(heights) < 1e-6  # equal
    last = r[5]
    assert abs((last.y + last.height) - (H - EDGE)) < 1e-6
    for geo in r.geometry:
        assert geo.x >= 0 and geo.y >= 0
        assert geo.x + geo.width <= W + 1e-6
        assert geo.y + geo.height <= H + 1e-6


def test_static_ignores_focus():
    lm = StaticLayoutManager()
    a = lm.measure(W, H, 6, focus=[1, 0, 0, 0, 0, 0])
    b = lm.measure(W, H, 6, focus=None)
    assert [g for g in a.geometry] == [g for g in b.geometry]


# -- SelectionLayoutManager --------------------------------------------------
# The measured geometry is CONSTANT (equal height + uniform gap, like the static
# layout); selection is a render-side effect (dim the others, grow + foreground the
# selected), so the layout must not change with focus.
def test_selection_layout_is_constant_and_matches_static():
    lm = SelectionLayoutManager()
    assert lm.is_selection_enabled is True
    a = lm.measure(W, H, 6)
    b = StaticLayoutManager().measure(W, H, 6)
    assert [g for g in a.geometry] == [g for g in b.geometry]


def test_selection_geometry_ignores_focus():
    lm = SelectionLayoutManager()
    a = lm.measure(W, H, 6, focus=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    b = lm.measure(W, H, 6, focus=[0.0] * 6)
    assert [g for g in a.geometry] == [g for g in b.geometry]
    heights = [g.height for g in a.geometry]
    assert max(heights) - min(heights) < 1e-6  # equal heights (constant)
    gaps = [a[i + 1].y - (a[i].y + a[i].height) for i in range(5)]
    assert max(gaps) - min(gaps) < 1e-6        # uniform gap (constant)


def test_selection_top_selected_keeps_2mm():
    lm = SelectionLayoutManager()
    r = lm.measure(W, H, 6, focus=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    assert abs(r[0].y - EDGE) < 0.6  # ~2 mm from the top edge (constant stack)
    assert r[0].y >= 0


def test_selection_bottom_selected_keeps_2mm():
    lm = SelectionLayoutManager()
    r = lm.measure(W, H, 6, focus=[0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
    assert abs((H - (r[5].y + r[5].height)) - EDGE) < 0.6
    assert r[5].y + r[5].height <= H


def test_selection_no_overflow_and_stable_x_width():
    lm = SelectionLayoutManager()
    result_first = lm.measure(W, H, 6, focus=[0.0, 1.0, 0.0, 0.0, 0.0, 0.0])
    for geo in result_first.geometry:
        assert geo.x >= 0 and geo.y >= 0
        assert geo.x + geo.width <= W + 1e-6
        assert geo.y + geo.height <= H + 1e-6
    # x / width are stable across focus (the measured geometry never changes).
    result_second = lm.measure(W, H, 6, focus=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    for i in range(6):
        assert abs(result_first[i].x - result_second[i].x) < 1e-6
        assert abs(result_first[i].width - result_second[i].width) < 1e-6


def test_selection_crowded_keeps_margins_and_no_overflow():
    lm = SelectionLayoutManager()
    n = 50
    r = lm.measure(W, H, n, focus=[0.0, 0.0, 1.0] + [0.0] * (n - 3))
    assert r[0].y >= EDGE - 0.6
    assert r[n - 1].y + r[n - 1].height <= H + 1e-6


# -- dynamic gap -------------------------------------------------------------
def test_gap_scales_down_with_more_indicators():
    lm = SelectionLayoutManager()
    assert lm.gap_for(H, 3) > lm.gap_for(H, 6)
    assert lm.gap_for(H, 6) > lm.gap_for(H, 20)
    assert lm.gap_for(H, 1) == 0.0


def test_gap_bounded():
    lm = SelectionLayoutManager()
    assert lm.gap_for(H, 1) >= 0.0
    assert lm.gap_for(H, 20) >= 0.0
    assert lm.gap_for(H, 3) <= 28.0


def test_empty_count():
    lm = SelectionLayoutManager()
    r = lm.measure(W, H, 0)
    assert len(r) == 0
    assert r.bar_width == W - 2 * lm.padding_x


# -- vertical columns --------------------------------------------------------
def test_vertical_columns_side_by_side_and_within_margins():
    lm = StaticLayoutManager()
    r = lm.measure(W, H, 3, vertical=True)
    assert len(r) == 3
    widths = [g.width for g in r.geometry]
    assert max(widths) - min(widths) < 1e-6          # equal column widths
    heights = [g.height for g in r.geometry]
    assert max(heights) - min(heights) < 1e-6        # equal column heights
    # Все столбцы заполняют высоту панели внутри отступов.
    assert abs(r[0].y - EDGE) < 1e-6
    assert abs((r[0].y + r[0].height) - (H - EDGE)) < 1e-6
    # Столбцы идут подряд по X с равным зазором, не вылезая за края.
    gaps = [r[i + 1].x - (r[i].x + r[i].width) for i in range(2)]
    assert max(gaps) - min(gaps) < 1e-6
    last = r[2]
    assert last.x >= 0 and last.x + last.width <= W + 1e-6


def test_vertical_columns_ignore_focus():
    lm = SelectionLayoutManager()
    a = lm.measure(W, H, 3, focus=[1.0, 0.0, 0.0], vertical=True)
    b = lm.measure(W, H, 3, focus=None, vertical=True)
    assert [g for g in a.geometry] == [g for g in b.geometry]


def test_vertical_empty_count():
    lm = SelectionLayoutManager()
    r = lm.measure(W, H, 0, vertical=True)
    assert len(r) == 0


# -- explicit layout (ways 1 & 2) -------------------------------------------
def test_auto_gap_vertical_uses_explicit_size_and_auto_gap():
    lm = AutoGapLayoutManager(bar_size=80.0)
    r = lm.measure(W, H, 3, vertical=True)
    assert len(r) == 3
    for geo in r.geometry:
        assert abs(geo.width - 80.0) < 1e-6            # explicit column width
        assert abs(geo.y - EDGE) < 1e-6                # top edge margin
        assert abs((geo.y + geo.height) - (H - EDGE)) < 1e-6  # fills the height
    gap = lm.gap_for(W, 3)                              # auto-derived gap
    assert abs((r[1].x - (r[0].x + r[0].width)) - gap) < 1e-6
    # Столбцы центрируются в панели.
    assert abs((r[0].x + (r[2].x + r[2].width)) - W) < 1e-6


def test_auto_gap_defaults_to_selection_enabled():
    """Способ 1 (auto_gap) включён в выбор — рендер накладывает свечение/рост."""
    lm = AutoGapLayoutManager(bar_size=80.0)
    assert lm.is_selection_enabled is True
    # Геометрия постоянна — не меняется от фокуса (как в SelectionLayoutManager).
    a = lm.measure(W, H, 3, vertical=True)
    b = lm.measure(W, H, 3, focus=[1.0, 0.0, 0.0], vertical=True)
    assert [g for g in a.geometry] == [g for g in b.geometry]


def test_explicit_gap_vertical_centers_stack():
    lm = ExplicitGapLayoutManager(bar_size=80.0, gap=40.0)
    r = lm.measure(W, H, 3, vertical=True)
    assert len(r) == 3
    for geo in r.geometry:
        assert abs(geo.width - 80.0) < 1e-6
    gaps = [r[i + 1].x - (r[i].x + r[i].width) for i in range(2)]
    assert all(abs(g - 40.0) < 1e-6 for g in gaps)      # explicit gap
    total = 80.0 * 3 + 40.0 * 2
    assert abs((r[0].x + (r[2].x + r[2].width)) - W) < 1e-6
    assert abs(r[0].x - (W - total) / 2.0) < 1e-6


def test_explicit_gap_horizontal_uses_height_and_gap():
    lm = ExplicitGapLayoutManager(bar_size=60.0, gap=30.0)
    r = lm.measure(W, H, 4)
    assert len(r) == 4
    for geo in r.geometry:
        assert abs(geo.height - 60.0) < 1e-6            # explicit bar height
        assert abs(geo.width - (W - 2 * lm.padding_x)) < 1e-6
    gaps = [r[i + 1].y - (r[i].y + r[i].height) for i in range(3)]
    assert all(abs(g - 30.0) < 1e-6 for g in gaps)


def test_explicit_too_many_columns_clamps_to_fit():
    """Если явная ширина+зазор не влезают, зазор схлопывается, а ширина ужимается."""
    lm = ExplicitGapLayoutManager(bar_size=900.0, gap=400.0)
    r = lm.measure(W, H, 3, vertical=True)
    usable = W - 2 * lm.padding_x
    # Зазор обнулился: столбцы стоят подряд.
    gap = r[1].x - (r[0].x + r[0].width)
    assert gap < 1e-6
    # Итоговый стек не вылезает за доступную ширину (столбцы ужаты до влезания).
    stack_width = (r[2].x + r[2].width) - r[0].x
    assert stack_width <= usable + 1e-6
    assert r[2].x + r[2].width <= W + 1e-6
    # Стек центрирован; ширина столбца = usable/n.
    assert abs(r[0].x - (W - stack_width) / 2.0) < 1e-6
    assert abs(r[0].width - usable / 3) < 1e-6


def test_explicit_gap_vertical_left_aligns_to_padding():
    """stack_align="left" прижимает стек к левому краю (отступ = padding_x), а не центрирует."""
    lm = ExplicitGapLayoutManager(bar_size=80.0, gap=40.0, stack_align="left")
    r = lm.measure(W, H, 3, vertical=True)
    assert abs(r[0].x - lm.padding_x) < 1e-6
    # Горизонтальная раскладка при "left" прижимается к верхнему краю (edge_margin).
    lm_h = ExplicitGapLayoutManager(bar_size=60.0, gap=30.0, stack_align="left")
    r_h = lm_h.measure(W, H, 3)
    assert abs(r_h[0].y - EDGE) < 1e-6
