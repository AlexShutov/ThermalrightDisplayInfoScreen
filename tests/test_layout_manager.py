# SPDX-License-Identifier: MIT
from hardwarescreen.adapter.layout import SelectionLayoutManager, StaticLayoutManager

# Defaults mirror the 1920x440 panel (~2 mm edge margin ≈ 14 px at ~68 px/cm).
W, H = 1920, 440
EDGE = 14.0  # SelectionLayoutManager()._edge_margin (2 mm)
SCALE = 1.22


def _gap(lm, n=6):
    return lm.gap_for(H, n)


def _base(lm, n=6):
    gap = _gap(lm, n)
    return (H - 2.0 * EDGE - gap * (n - 1)) / (n + SCALE - 1.0)


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
def test_selection_grows_focused_bar_without_shrinking_others():
    lm = SelectionLayoutManager()
    base = _base(lm)
    none = lm.measure(W, H, 6, focus=[0.0] * 6)
    assert abs(none[0].height - base) < 1e-6
    r = lm.measure(W, H, 6, focus=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    assert abs(r[0].height - base * SCALE) < 1e-6   # focused grows
    for i in range(1, 6):
        assert abs(r[i].height - base) < 1e-6       # neighbours keep size
    assert abs(none[0].height - r[0].height) > 1e-6  # it grew vs the idle state


def test_selection_top_selected_keeps_2mm():
    lm = SelectionLayoutManager()
    r = lm.measure(W, H, 6, focus=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    assert r[0].y >= EDGE - 0.6  # keeps at least ~2 mm from the top edge
    assert r[0].y >= 0


def test_selection_bottom_selected_keeps_2mm():
    lm = SelectionLayoutManager()
    r = lm.measure(W, H, 6, focus=[0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
    assert (H - (r[5].y + r[5].height)) >= EDGE - 0.6
    assert r[5].y + r[5].height <= H


def test_selection_no_overflow_and_stable_x_width():
    lm = SelectionLayoutManager()
    r = lm.measure(W, H, 6, focus=[0.0, 1.0, 0.0, 0.0, 0.0, 0.0])
    for geo in r.geometry:
        assert geo.x >= 0 and geo.y >= 0
        assert geo.x + geo.width <= W + 1e-6
        assert geo.y + geo.height <= H + 1e-6
    # x / width are stable across focus (only heights + gaps ease).
    r2 = lm.measure(W, H, 6, focus=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    for i in range(6):
        assert abs(r[i].x - r2[i].x) < 1e-6
        assert abs(r[i].width - r2[i].width) < 1e-6


def test_selection_gap_around_focused_is_tight():
    # A gap next to the focused bar tightens to selection_gap (~0.3 mm).
    lm = SelectionLayoutManager()
    sel_gap = lm.selection_gap
    general = _gap(lm)
    r = lm.measure(W, H, 6, focus=[0.0, 1.0, 0.0, 0.0, 0.0, 0.0])
    g0 = r[1].y - (r[0].y + r[0].height)  # between bar0 and bar1 (next to focus)
    g1 = r[2].y - (r[1].y + r[1].height)  # between bar1 and bar2 (next to focus)
    g2 = r[3].y - (r[2].y + r[2].height)  # between bar2 and bar3 (far from focus)
    assert abs(g0 - sel_gap) < 0.6
    assert abs(g1 - sel_gap) < 0.6
    assert abs(g2 - general) < 0.6


def test_selection_gap_unfocused_is_general():
    lm = SelectionLayoutManager()
    general = _gap(lm)
    r = lm.measure(W, H, 6, focus=[0.0] * 6)
    for i in range(5):
        g = r[i + 1].y - (r[i].y + r[i].height)
        assert abs(g - general) < 1e-6


def test_selection_crowded_never_grows_gap():
    # With enough bars the dynamic gap collapses to 0; a tightened gap must not
    # grow past the general gap (sel_gap > general_gap there), keeping margins.
    lm = SelectionLayoutManager()
    n = 50
    r = lm.measure(W, H, n, focus=[0.0, 0.0, 0.0, 0.0, 1.0] + [0.0] * (n - 5))
    assert r[0].y >= EDGE - 0.6
    assert r[n - 1].y + r[n - 1].height <= H + 1e-6
    for i in range(n - 1):
        g = r[i + 1].y - (r[i].y + r[i].height)
        assert g <= lm.gap_for(H, n) + 1e-6  # never grows past the general gap


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