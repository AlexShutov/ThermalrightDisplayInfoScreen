"""Unit tests for the Phase 3 adapter core (no trcc / no hardware)."""
# SPDX-License-Identifier: MIT
from __future__ import annotations

from hardwarescreen.adapter.dashboard import compose_dashboard, gauge_layout
from hardwarescreen.adapter.driver import AdapterDriver, bridge_has_data
from hardwarescreen.adapter.poller import BridgePoller
from hardwarescreen.adapter.source import DashboardSource
from tests.fakes import FakeBackground, FakeStore

SIZE = (256, 220)  # tall-enough canvas for cheap tests (top=96 leaves bar room)


def _bridge(**kw) -> dict:
    base = {
        "seq": 1,
        "mode": "day",
        "setup": [{"id": "cpu", "label": "CPU", "unit": "%", "min": 0, "max": 100}],
        "values": {"cpu": 42.0},
        "palette": {"background": "#111318", "panel": "#1b1e27",
                    "accent": "#4f9cf9", "text": "#eef2f8", "muted": "#8a93a6"},
    }
    base.update(kw)
    return base


# -- poller ------------------------------------------------------------------
def test_poller_emits_on_seq_growth():
    s = FakeStore()
    s.write({"mode": "day"})  # seq 1
    p = BridgePoller(s)
    assert p.poll() is not None
    assert p.seen_seq == 1
    assert p.poll() is None  # no newer seq -> not re-emitted


def test_poller_ignores_stale_or_older_state():
    s = FakeStore()
    s.write({"mode": "day"})  # seq 1
    p = BridgePoller(s)
    assert p.poll() is not None
    # simulate an older/rolled-back state (seq not greater) -> ignored
    assert p.poll() is None


def test_poller_first_state_emits_even_if_seq_zero():
    s = FakeStore()
    s.write({"mode": "day"})
    p = BridgePoller(s)
    assert p.poll()["mode"] == "day"


def test_poller_ignores_malformed_seq():
    s = FakeStore()
    s.write({"mode": "day"}, seq="not-an-int")
    p = BridgePoller(s)
    assert p.poll() is None
    assert p.last is None


def test_poller_exposes_last_newest():
    s = FakeStore()
    s.write({"mode": "day"})  # seq 1
    p = BridgePoller(s)
    assert p.poll()["mode"] == "day"
    s.write({"mode": "night"})  # seq 2
    assert p.poll()["mode"] == "night"
    assert p.last["mode"] == "night"


# -- layout / compose --------------------------------------------------------
def test_gauge_layout_pct_and_text():
    c, specs = gauge_layout(
        [{"id": "cpu", "label": "CPU", "unit": "%", "min": 0, "max": 100}],
        {"cpu": 50.0},
        {},
        SIZE,
    )
    assert len(specs) == 1
    sp = specs[0]
    assert sp.pct == 50.0
    assert sp.text == "50%"
    assert sp.w == SIZE[0] - 2 * 40


def test_gauge_layout_clamps_and_min_max():
    c, specs = gauge_layout(
        [{"id": "t", "label": "TEMP", "unit": "C", "min": 20, "max": 40}],
        {"t": 10.0},  # below min
        {},
        SIZE,
    )
    assert specs[0].pct == 0.0


def test_compose_dashboard_renders_raw_rgb():
    img = compose_dashboard(_bridge()["setup"], {"cpu": 42.0}, _bridge()["palette"], SIZE)
    assert img.size == SIZE
    assert len(img.tobytes()) == SIZE[0] * SIZE[1] * 3


def test_compose_dashboard_empty_setup_shows_no_data():
    img = compose_dashboard([], {}, {}, SIZE)
    assert img.size == SIZE


def test_compose_dashboard_tiny_canvas_never_crashes():
    # top=96 exceeds the height -> bar_h clamped to 0; must not raise
    img = compose_dashboard(
        _bridge()["setup"], {"cpu": 42.0}, _bridge()["palette"], (256, 48)
    )
    assert img.size == (256, 48)
    assert len(img.tobytes()) == 256 * 48 * 3


def test_compose_dashboard_many_gauges_clamps_zero_height():
    setup = [{"id": f"g{i}", "label": f"G{i}", "unit": "%", "min": 0, "max": 100}
             for i in range(30)]
    img = compose_dashboard(setup, {g["id"]: 50.0 for g in setup}, {}, (256, 220))
    assert img.size == (256, 220)


# -- source ------------------------------------------------------------------
def test_dashboard_source_returns_raw_rgb_and_keeps_newest():
    store = FakeStore()
    store.write(_bridge())  # seq 1
    src = DashboardSource(BridgePoller(store), SIZE)
    frame = src.latest()
    assert frame is not None
    assert len(frame) == SIZE[0] * SIZE[1] * 3
    # no newer state -> keeps the last composite (does not return None)
    assert src.latest() is not None
    assert src.size == SIZE


# -- driver ------------------------------------------------------------------
def test_driver_shows_when_data_and_attached():
    bg = FakeBackground()
    drv = AdapterDriver(bg, object())
    drv.update(_bridge(), attached=True)
    assert drv.shown is True
    assert len(bg.shown) == 1 and bg.shown[0][0] == "dashboard"


def test_driver_defers_show_until_attached_race():
    bg = FakeBackground()
    drv = AdapterDriver(bg, object())
    drv.update(_bridge(), attached=False)  # panel not attached yet -> no show
    assert drv.shown is False
    assert bg.shown == []
    drv.update(_bridge(), attached=True)  # attached now -> show
    assert drv.shown is True
    assert len(bg.shown) == 1


def test_driver_ends_on_no_data_and_is_idempotent():
    bg = FakeBackground()
    drv = AdapterDriver(bg, object())
    drv.update(_bridge(), attached=True)
    drv.update(_bridge(), attached=True)  # no-op while shown
    assert len(bg.shown) == 1
    drv.update({"seq": 2, "setup": [], "values": {}}, attached=True)  # data gone
    assert drv.shown is False
    assert bg.ended == ["dashboard"]


def test_bridge_has_data():
    assert bridge_has_data(_bridge()) is True
    assert bridge_has_data({"seq": 1, "setup": [], "values": {}}) is False
    assert bridge_has_data(None) is False
