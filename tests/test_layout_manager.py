# SPDX-License-Identifier: MIT
from hardwarescreen.adapter.layout import SelectionLayoutManager, StaticLayoutManager

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
    r = lm.measure(W, H, 6, focus=[0.0, 1.0, 0.0, 0.0, 0.0, 0.0])
    for geo in r.geometry:
        assert geo.x >= 0 and geo.y >= 0
        assert geo.x + geo.width <= W + 1e-6
        assert geo.y + geo.height <= H + 1e-6
    # x / width are stable across focus (the measured geometry never changes).
    r2 = lm.measure(W, H, 6, focus=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    for i in range(6):
        assert abs(r[i].x - r2[i].x) < 1e-6
        assert abs(r[i].width - r2[i].width) < 1e-6


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
