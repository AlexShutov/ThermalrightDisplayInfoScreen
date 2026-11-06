# SPDX-License-Identifier: MIT
from hardwarescreen.adapter.layout import DashboardLayoutManager

# Defaults mirror the 1920x440 panel: ~0.5 cm top/bottom margin (~34 px at
# ~68 px/cm) and a 48 px side padding.
W, H = 1920, 440


def test_measure_fills_height_within_margins():
    lm = DashboardLayoutManager()
    r = lm.measure(W, H, 6)
    assert len(r) == 6
    assert r.bar_width == W - 2 * lm.padding_x
    last = r[5]
    # The stack must fill the available height exactly (leave the bottom margin).
    assert abs((last.y + last.height) - (H - lm.padding_bottom)) < 1e-6
    for geo in r.geometry:
        assert geo.x >= 0 and geo.y >= 0
        assert geo.x + geo.width <= W + 1e-6
        assert geo.y + geo.height <= H + 1e-6
    # All bars share one width (the layout is static).
    assert all(abs(g.width - r.bar_width) < 1e-6 for g in r.geometry)


def test_measure_respects_top_margin():
    lm = DashboardLayoutManager()
    r = lm.measure(W, H, 6)
    assert r[0].y == lm.padding_top


def test_measure_empty_count():
    lm = DashboardLayoutManager()
    r = lm.measure(W, H, 0)
    assert len(r) == 0
    assert r.bar_width == W - 2 * lm.padding_x


def test_measure_no_overflow_many_gauges():
    lm = DashboardLayoutManager()
    r = lm.measure(W, H, 30)
    last = r[29]
    assert last.y + last.height <= H + 1e-6
    assert all(g.y + g.height <= H + 1e-6 for g in r.geometry)


def test_measure_collapses_gap_rather_than_overflow():
    # A tall stack (many bars) compresses the gap to zero and still fits.
    lm = DashboardLayoutManager()
    r = lm.measure(W, H, 20)
    first, last = r[0], r[19]
    # With the gap collapsed, the span is height * n within the available strip.
    assert abs(first.y + first.height - last.y - last.height) <= (
        H - lm.padding_top - lm.padding_bottom) + 1e-6
    assert last.y + last.height <= H + 1e-6


def test_measure_layout_is_static_regardless_of_focus():
    # The layout API has no focus input: geometry is identical for the same count.
    lm = DashboardLayoutManager()
    a = lm.measure(W, H, 6)
    b = lm.measure(W, H, 6)
    assert [g for g in a.geometry] == [g for g in b.geometry]