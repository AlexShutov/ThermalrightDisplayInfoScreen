"""YAML-first configuration."""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml

try:  # defaults for panel size (11.3) — no hardcode in the renderer
    DEFAULT_SIZE = (1920, 440)
except Exception:  # pragma: no cover
    DEFAULT_SIZE = (1920, 440)


@dataclass
class Config:
    width: int = 1920
    height: int = 440
    keepalive_s: float = 2.0
    max_frame_bytes: int = 450_000
    idle_fps: float = 2.0
    active_fps: float = 15.0
    sensor_poll_s: float = 1.0
    jpeg_quality: int = 80
    # Плотность «dp → px» для дизайн-размеров (Android-стиль): сколько физических
    # пикселей панели приходится на одну dp.  По умолчанию 1.0 — dp == px.
    density: float = 1.0
    # Семейство шрифта для надписей дашборда (Spacetron — стилевой киберпанк-шрифт,
    # поставляется в ``DashboardWidgets/src/common/fonts/``).  Переопределяется в
    # ``config.yaml`` ключом ``font_family`` и/или env ``HARDWARESCREEN_FONT``.
    font_family: str = "Spacetron"
    # Семейство для ВЕРХНИХ подписей (букв) отдельно от значений-процентов.
    # ``None`` → подписи тем же шрифтом, что и значения (``font_family`` = Spacetron).
    # Значения всегда в ``font_family`` (Spacetron).
    label_font_family: str | None = None
    # Жирный текст надписей: у Bold-актива у стилевого шрифта нет, поэтому жирность
    # имитируется утолщающей обводкой глифа (stroke).  По умолчанию — включено.
    text_bold: bool = True
    # Межсимвольный интервал надписей как доля кегля (трекинг): стилевой шрифт
    # плотный, без интервала символы слипаются.
    text_letter_spacing: float = 0.12
    # Жирность ВЕРХНИХ подписей. Для Spacetron (тот же шрифт, что у значений) утолщающая
    # обводка даёт тот же жирный вид, что у значений ниже — включено по умолчанию.
    # Для широкого киберпанк-шрифта её стоило бы выключить (заливает просветы).
    label_text_bold: bool = True
    # Доля жирности значений для подписей (кегль подписей больше, поэтому без
    # масштаба обводка выходит жирнее значений).  0.6 — чуть легче значений.
    label_text_bold_scale: float = 0.6
    theme: str = "dark"
    sensors: list[str] = field(default_factory=lambda: ["cpu", "ram", "temp", "disk", "net"])
    background: dict | None = None  # {"type": "solid"|"image", "value": ...}

    @staticmethod
    def load(path: str) -> Config:
        cfg = Config()
        if path and os.path.exists(path):
            with open(path) as fh:
                data = yaml.safe_load(fh) or {}
            for key, value in data.items():
                if hasattr(cfg, key):
                    setattr(cfg, key, value)
        if isinstance(cfg.width, str) and "x" in str(cfg.width):
            w, h = cfg.width.lower().split("x")
            cfg.width, cfg.height = int(w), int(h)
        return cfg
