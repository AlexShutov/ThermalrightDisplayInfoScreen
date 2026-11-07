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
