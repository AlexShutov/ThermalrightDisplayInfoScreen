"""Unit tests for the pure manager state model (no frameworks / HTTP)."""
# SPDX-License-Identifier: MIT
from __future__ import annotations

from manager.state import (
    Gauge,
    ManagerState,
    active_palette,
    active_theme,
    bridge_dict,
    merge_values,
)


def _state(**kw) -> ManagerState:
    return ManagerState(
        themes={"day": "d037", "night": "d029"},
        adapter={
            "day": {"background": "#fff", "text": "#000"},
            "night": {"background": "#000", "text": "#fff"},
        },
        setup=[Gauge(id="cpu", label="CPU", unit="%", min=0, max=100)],
        values={"cpu": 10.0},
        **kw,
    )


def test_active_palette_follows_mode():
    s = _state()
    assert active_palette(s)["background"] == "#fff"
    s.mode = "night"
    assert active_palette(s)["background"] == "#000"


def test_active_theme_follows_mode():
    s = _state()
    assert active_theme(s) == "d037"
    s.mode = "night"
    assert active_theme(s) == "d029"


def test_merge_values_last_wins_no_growth():
    assert merge_values({}, {"cpu": 10}) == {"cpu": 10}
    assert merge_values({"cpu": 10, "gpu": 70}, {"cpu": 42}) == {"cpu": 42, "gpu": 70}
    assert merge_values({"cpu": 10}, {}) == {"cpu": 10}


def test_bridge_dict_is_data_plane_payload():
    s = _state()
    b = bridge_dict(s)
    assert b["mode"] == "day"
    assert b["active_theme"] == "d037"
    assert b["palette"]["background"] == "#fff"
    assert b["setup"] == [{"id": "cpu", "label": "CPU", "unit": "%", "min": 0.0, "max": 100.0}]
    assert b["values"] == {"cpu": 10.0}
    assert "seq" not in b  # seq is added by the StateStore
