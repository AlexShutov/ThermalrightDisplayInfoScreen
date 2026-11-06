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


def test_composite_regions_right_margin(composite):
    _, regions_fn = composite
    regions = regions_fn((1920, 440), 6.86)
    right_margin_px = 1920 - regions["rings_x"] - regions["rings_size"][0]
    assert 30 <= right_margin_px <= 40  # 5mm * ~6.86px/mm ≈ 34px
    assert regions["rings_size"] == (440, 440)
    assert regions["left_size"][1] == 440


def test_composite_renderer_emits_rgb_frame(composite):
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
    assert len(frame) == size[0] * size[1] * 3
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
    assert len(frame) == size[0] * size[1] * 3
