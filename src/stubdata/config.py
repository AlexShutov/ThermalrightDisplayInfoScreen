"""Configuration for the stub data source (YAML-first)."""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
from dataclasses import dataclass

import yaml


@dataclass
class Config:
    manager_url: str = "http://127.0.0.1:8123"
    poll_s: float = 1.0
    seed: int | None = None
    num_gauges: int = 6
    switch_probability: float = 0.02  # per-poll chance to flip day/night
    value_min: float = 0.0
    value_max: float = 100.0

    @staticmethod
    def load(path: str | None) -> Config:
        cfg = Config()
        if path and os.path.exists(path):
            with open(path) as fh:
                data = yaml.safe_load(fh) or {}
            for key, value in data.items():
                if hasattr(cfg, key):
                    setattr(cfg, key, value)
        return cfg
