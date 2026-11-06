"""Probe: собрать кадр композита «линейный слева + кольца справа» (dev-инструмент).

Заглушает ``trcc``-импорты (модуль GPL app.py импортирует trcc наверху), затем
рендерит ``CompositeDashboardRenderer`` offscreen и сохраняет PNG для визуальной
проверки. НЕ поставляется в пакете (dev-инструмент, как и остальные probes).
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _fake_module(name: str):
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


def _fake_trcc() -> None:
    _fake_module("trcc")
    _fake_module("trcc.core")
    _fake_module("trcc.core.models")
    _fake_module("trcc.core.ports")

    class RawFrame:  # noqa: D401 - заглушка
        def __init__(self, data, width, height):
            self.data = data
            self.width = width
            self.height = height

    class FrameSource:  # noqa: D401 - заглушка
        pass

    sys.modules["trcc"].core = sys.modules["trcc.core"]
    sys.modules["trcc.core"].models = sys.modules["trcc.core.models"]
    sys.modules["trcc.core"].ports = sys.modules["trcc.core.ports"]
    sys.modules["trcc.core.models"].RawFrame = RawFrame
    sys.modules["trcc.core.ports"].FrameSource = FrameSource

    _fake_module("trcc.adapters")
    _fake_module("trcc.adapters.modules")
    _fake_module("trcc.adapters.modules._base")

    class BaseModule:  # noqa: D401 - заглушка
        def __init__(self, host=None):
            self._host = host

        def __init_subclass__(cls, *, key=None, **kwargs):
            cls._key = key
            super().__init_subclass__(**kwargs)

    sys.modules["trcc"].adapters = sys.modules["trcc.adapters"]
    sys.modules["trcc.adapters"].modules = sys.modules["trcc.adapters.modules"]
    sys.modules["trcc.adapters.modules"]._base = sys.modules["trcc.adapters.modules._base"]
    sys.modules["trcc.adapters.modules._base"].BaseModule = BaseModule

    _fake_module("manager")
    _fake_module("manager.store")

    class AtomicStateFile:  # noqa: D401 - заглушка
        def __init__(self, path):
            self.path = path

    sys.modules["manager"].store = sys.modules["manager.store"]
    sys.modules["manager.store"].AtomicStateFile = AtomicStateFile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dev" / "trcc-module-dashboard"))
_fake_trcc()

from dashboard.app import (  # noqa: E402
    CompositeDashboardRenderer,
    _composite_regions,
)

from hardwarescreen.adapter.linear_dashboard import (  # noqa: E402
    LinearDashboardRenderer,
)

SIZE = (1920, 440)
PX_PER_MM = 6.86
regions = _composite_regions(SIZE, PX_PER_MM)
print("regions:", regions)

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
    regions["left_size"], background=None, fps=8.0,
    fill_direction="left_to_right", layout=None, value_palette_on=True,
)
renderer = CompositeDashboardRenderer(
    linear, SIZE, px_per_mm=PX_PER_MM, value_palette_on=True
)

from PIL import Image  # noqa: E402

# «Тема» — синтетический фон, поверх которого композит кладётся как прозрачный оверлей.
background = Image.new("RGB", SIZE)
for y in range(SIZE[1]):
    tone = 40 + int(28 * y / SIZE[1])
    background.paste((tone, tone + 4, tone + 8), (0, y, SIZE[0], y + 1))

frame = renderer(setup, values, palette, SIZE, background)
for _ in range(60):  # прогреем «добегающую» анимацию значений до установившихся
    frame = renderer(setup, values, palette, SIZE, background)
print("frame bytes:", len(frame), "expected:", SIZE[0] * SIZE[1] * 4)
overlay = Image.frombytes("RGBA", SIZE, frame)
combined = background.convert("RGBA").copy()
combined.alpha_composite(overlay)
combined.convert("RGB").save("/tmp/composite_probe.png")
print("saved /tmp/composite_probe.png")
