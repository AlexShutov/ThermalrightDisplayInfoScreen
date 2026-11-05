"""Producer loop: setup once, then random updates + occasional theme switches.

The RNG is seeded so a run is reproducible (good for dev and for tests). The
gate between the producer and the real manager is the ``ManagerClient`` port,
so the loop is testable with a recording fake.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any

from .client import ManagerClient
from .config import Config

GAUGE_TEMPLATES: list[dict[str, Any]] = [
    {"id": "cpu", "label": "CPU", "unit": "%"},
    {"id": "ram", "label": "RAM", "unit": "%"},
    {"id": "disk", "label": "DISK", "unit": "%"},
    {"id": "gpu", "label": "GPU", "unit": "%"},
    {"id": "fps", "label": "FPS", "unit": "", "max": 240},
    {"id": "temp", "label": "TEMP", "unit": "C", "max": 120},
]


def make_gauges(count: int) -> list[dict]:
    """Return the first ``count`` gauge descriptors for the dashboard setup."""
    return [{**g} for g in GAUGE_TEMPLATES[:count]]


class Producer:
    def __init__(self, cfg: Config, client: ManagerClient, rng: random.Random | None = None):
        self.cfg = cfg
        self.client = client
        self.rng = rng or random.Random(cfg.seed)
        self.mode = "day"

    def start(self) -> None:
        self.client.setup(make_gauges(self.cfg.num_gauges))

    def step(self) -> bool:
        """Emit one update snapshot; return True if the mode was switched."""
        values = {g["id"]: round(self.rng.uniform(self.cfg.value_min, self.cfg.value_max), 1)
                  for g in make_gauges(self.cfg.num_gauges)}
        self.client.update(values)
        if self.rng.random() < self.cfg.switch_probability:
            self.mode = "night" if self.mode == "day" else "day"
            self.client.set_theme(self.mode)
            return True
        return False

    def run(self, stop: Callable[[], bool] | None = None) -> None:
        self.start()
        while not (stop and stop()):
            self.step()
            time.sleep(self.cfg.poll_s)
