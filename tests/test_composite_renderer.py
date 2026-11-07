"""Integration tests for the module's composite renderer (linear-left + gauge rings).

The GPL module (``dev/trcc-module-dashboard/dashboard/app.py``) imports trcc at module
top, so we install minimal fake trcc modules before importing it. These tests cover the
region geometry and that ``CompositeDashboardRenderer`` produces a correct RGB frame.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

MODULE_ROOT = Path(__file__).resolve().parents[1] / "dev" / "trcc-module-dashboard"


def _install_fake_trcc() -> None:
    """Minimal trcc shims so the GPL module can be imported for verification."""

    def mk(name: str) -> types.ModuleType:
        module = types.ModuleType(name)
        sys.modules[name] = module
        return module

    for name in (
        "trcc",
        "trcc.core",
        "trcc.core.models",
        "trcc.core.ports",
        "trcc.adapters",
        "trcc.adapters.modules",
        "trcc.adapters.modules._base",
        "manager",
        "manager.store",
    ):
        mk(name)

    class RawFrame:  # noqa: D401 - shim
        def __init__(self, data, width, height):
            self.data = data
            self.width = width
            self.height = height

    class FrameSource:  # noqa: D401 - shim
        pass

    class BaseModule:  # noqa: D401 - shim
        def __init_subclass__(cls, *, key=None, **kwargs):
            cls._key = key
            super().__init_subclass__(**kwargs)

    class AtomicStateFile:  # noqa: D401 - shim
        def __init__(self, path):
            self.path = path

    sys.modules["trcc.core.models"].RawFrame = RawFrame
    sys.modules["trcc.core.ports"].FrameSource = FrameSource
    sys.modules["trcc.adapters.modules._base"].BaseModule = BaseModule
    sys.modules["manager.store"].AtomicStateFile = AtomicStateFile
    sys.modules["trcc"].core = sys.modules["trcc.core"]
    sys.modules["trcc.core"].models = sys.modules["trcc.core.models"]
    sys.modules["trcc.core"].ports = sys.modules["trcc.core.ports"]
    sys.modules["trcc"].adapters = sys.modules["trcc.adapters"]
    sys.modules["trcc.adapters"].modules = sys.modules["trcc.adapters.modules"]
    sys.modules["trcc.adapters.modules"]._base = sys.modules["trcc.adapters.modules._base"]
    sys.modules["manager"].store = sys.modules["manager.store"]


@pytest.fixture()
def composite(monkeypatch):
    """Import the module symbols with fake trcc and a clean module path."""
    for name in ("dashboard", "dashboard.app"):
        sys.modules.pop(name, None)
    original_path = list(sys.path)
    sys.path.insert(0, str(MODULE_ROOT))
    _install_fake_trcc()
    try:
        from dashboard.app import CompositeDashboardRenderer, _composite_regions  # noqa: PLC0415

        yield CompositeDashboardRenderer, _composite_regions
    finally:
        sys.path[:] = original_path
        for name in list(sys.modules):
            if (
                name.startswith("dashboard")
                or name.startswith("trcc")
                or name.startswith("manager")
            ):
                sys.modules.pop(name, None)


def test_composite_regions_margins(composite):
    _, regions_fn = composite
    regions = regions_fn((1920, 440), 6.86)
    # Зона колец — почти весь экран (полная высота панели), прижата к правому краю.
    assert regions["rings_size"] == (440, 440)
    assert regions["rings_x"] == 1920 - 440
    assert regions["rings_y"] == 0
    assert regions["left_size"][1] == 440


def test_composite_uses_all_gauges_for_rings(composite, monkeypatch):
    import activityrings.rings as rings_mod  # noqa: PLC0415

    from hardwarescreen.adapter.linear_dashboard import LinearDashboardRenderer  # noqa: PLC0415

    CompositeDashboardRenderer, _ = composite
    captured: dict[str, int] = {}

    def spy(gauges, size, progress=None, **kwargs):
        from PIL import Image

        captured["count"] = len(gauges)
        return Image.new("RGBA", (int(size[0]), int(size[1])), (0, 0, 0, 0))

    monkeypatch.setattr(rings_mod, "render_gauge_rings", spy)

    size = (1920, 440)
    setup = [
        {"id": f"g{i}", "label": f"G{i}", "min": 0, "max": 100, "unit": "%"}
        for i in range(6)
    ]
    values = {g["id"]: 50 + i for i, g in enumerate(setup)}
    linear = LinearDashboardRenderer((1438, 440), background=None, fps=8.0, layout=None)
    renderer = CompositeDashboardRenderer(linear, size, px_per_mm=6.86, value_palette_on=True)
    renderer(setup, values, {}, size, None)
    # По умолчанию потолка нет: кольца (и левые столбцы) используют ВСЕ датчики (6).
    assert captured["count"] == 6


def test_composite_rings_limited_to_first_gauges(composite, monkeypatch):
    import activityrings.rings as rings_mod  # noqa: PLC0415

    from hardwarescreen.adapter.linear_dashboard import LinearDashboardRenderer  # noqa: PLC0415

    CompositeDashboardRenderer, _ = composite
    captured: dict[str, list[str]] = {}

    def spy(entries, size, progress=None, **kwargs):
        from PIL import Image

        # render_gauge_rings получает кортежи (ring_key, label, цвет, прогресс).
        captured["keys"] = [entry[0] for entry in entries]
        return Image.new("RGBA", (int(size[0]), int(size[1])), (0, 0, 0, 0))

    monkeypatch.setattr(rings_mod, "render_gauge_rings", spy)

    size = (1920, 440)
    setup = [
        {"id": f"g{i}", "label": f"G{i}", "min": 0, "max": 100, "unit": "%"}
        for i in range(6)
    ]
    values = {g["id"]: 50 + i for i, g in enumerate(setup)}
    linear = LinearDashboardRenderer((1438, 440), background=None, fps=8.0, layout=None)
    renderer = CompositeDashboardRenderer(
        linear, size, px_per_mm=6.86, value_palette_on=True, ring_max_gauges=4
    )
    renderer(setup, values, {}, size, None)
    # Кольца показывают только первые 4 датчика (левый индикатор — все 6).
    assert sorted(captured["keys"]) == ["g0", "g1", "g2", "g3"]


def test_composite_left_includes_sensors_missing_from_setup(composite):
    from hardwarescreen.adapter.linear_dashboard import LinearDashboardRenderer  # noqa: PLC0415

    CompositeDashboardRenderer, _ = composite
    size = (1920, 440)
    setup = [
        {"id": "cpu", "label": "CPU", "min": 0, "max": 100, "unit": "%"},
        {"id": "gpu", "label": "GPU", "min": 0, "max": 100, "unit": "%"},
        {"id": "ram", "label": "RAM", "min": 0, "max": 100, "unit": "%"},
        {"id": "temp", "label": "TEMP", "min": 0, "max": 100, "unit": "C"},
    ]
    # Сенсоры поставляют 6 датчиков, а настроено только 4: левый ряд должен показать все 6.
    values = {"cpu": 1.0, "gpu": 2.0, "ram": 3.0, "temp": 4.0, "disk": 5.0, "fps": 6.0}
    linear = LinearDashboardRenderer((1438, 440), background=None, fps=8.0, layout=None)
    renderer = CompositeDashboardRenderer(
        linear, size, px_per_mm=6.86, value_palette_on=True, ring_max_gauges=4
    )
    left_gauges = renderer._left_gauges(setup, values)
    assert [g["id"] for g in left_gauges] == ["cpu", "gpu", "ram", "temp", "disk", "fps"]


def test_composite_renderer_emits_transparent_overlay(composite):
    CompositeDashboardRenderer, _ = composite
    from hardwarescreen.adapter.linear_dashboard import LinearDashboardRenderer  # noqa: PLC0415

    size = (1920, 440)
    setup = [
        {"id": "cpu", "label": "CPU", "min": 0, "max": 100, "unit": "%"},
        {"id": "gpu", "label": "GPU", "min": 0, "max": 100, "unit": "%"},
        {"id": "ram", "label": "RAM", "min": 0, "max": 200, "unit": "GB"},
    ]
    values = {"cpu": 62, "gpu": 88, "ram": 82}
    palette = {
        "background": "#101318",
        "panel": "#181a20",
        "accent": "#38b5f0",
        "text": "#e6e8ec",
        "muted": "#484c54",
    }
    linear = LinearDashboardRenderer(
        (1438, 440), background=None, fps=8.0,
        fill_direction="left_to_right", layout=None, value_palette_on=True,
    )
    renderer = CompositeDashboardRenderer(linear, size, px_per_mm=6.86, value_palette_on=True)
    frame = b""
    for _ in range(5):
        frame = renderer(setup, values, palette, size, None)
    assert isinstance(frame, bytes)
    assert len(frame) == size[0] * size[1] * 4  # RGBA-оверлей (прозрачный фон)
    assert frame != b"\x00" * len(frame)  # хоть какое-то содержимое


def test_composite_renderer_empty_setup_is_safe(composite):
    CompositeDashboardRenderer, _ = composite
    from hardwarescreen.adapter.linear_dashboard import LinearDashboardRenderer  # noqa: PLC0415

    size = (1920, 440)
    linear = LinearDashboardRenderer(
        (1438, 440), background=None, fps=8.0, layout=None
    )
    renderer = CompositeDashboardRenderer(linear, size, px_per_mm=6.86, value_palette_on=True)
    frame = renderer([], {}, {}, size, None)
    assert len(frame) == size[0] * size[1] * 4


def test_composite_rings_selection_driver(composite, monkeypatch):
    """Общее выделение: один драйвер выбирает 2 случайных датчика из набора колец и
    отдаёт ОДИН и тот же ``focus`` и линейному, и кольцам (одинаковые столбцы)."""
    import time  # noqa: PLC0415

    import activityrings.rings as rings_mod  # noqa: PLC0415

    from hardwarescreen.adapter.linear_dashboard import LinearDashboardRenderer  # noqa: PLC0415

    CompositeDashboardRenderer, _ = composite
    captured: dict[str, list[float]] = {}

    def spy_rings(entries, size, progress=None, focus=None, **kwargs):
        from PIL import Image

        captured["ring_focus"] = list(focus or [])
        return Image.new("RGBA", (int(size[0]), int(size[1])), (0, 0, 0, 0))

    monkeypatch.setattr(rings_mod, "render_gauge_rings", spy_rings)

    size = (1920, 440)
    setup = [
        {"id": f"g{i}", "label": f"G{i}", "min": 0, "max": 100, "unit": "%"}
        for i in range(6)
    ]
    values = {g["id"]: 50 + i for i, g in enumerate(setup)}
    linear = LinearDashboardRenderer((1438, 440), background=None, fps=8.0, layout=None)
    original_render = linear.render

    def spy_linear(*args, **kwargs):
        captured["linear_focus"] = list(kwargs.get("focus") or [])
        return original_render(*args, **kwargs)

    linear.render = spy_linear  # type: ignore[method-assign]

    renderer = CompositeDashboardRenderer(
        linear, size, px_per_mm=6.86, value_palette_on=True, ring_max_gauges=4
    )
    renderer(setup, values, {}, size, None)
    # Выбор идёт из множества, присутствующего на ОБОИХ графиках (первые 4 кольца).
    assert set(renderer._selection.active_indices) <= {0, 1, 2, 3}
    assert len(renderer._selection.active_indices) == 2
    # Линейный и кольца получают ОДИН и тот же фокус по каждому датчику (первые 4).
    assert captured["linear_focus"][:4] == list(reversed(captured["ring_focus"]))
    assert all(v == 0.0 for v in captured["linear_focus"][4:])
    # Когда переход оканчивается — ровно 2 кольца в фокусе (фокус ~1).
    later_focus = renderer._selection.focus(time.monotonic() + 1.0, 4)
    assert sum(1 for v in later_focus if v > 0.5) == 2
