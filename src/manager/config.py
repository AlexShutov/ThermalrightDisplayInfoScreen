"""Manager configuration (YAML-first; add options here before the code)."""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml


def _default_palettes() -> dict[str, dict]:
    return {
        "day": {
            "background": "#f2f3f7",
            "panel": "#ffffff",
            "accent": "#2e6fdb",
            "text": "#1a1a1e",
            "muted": "#6b7280",
        },
        "night": {
            "background": "#111318",
            "panel": "#1b1e27",
            "accent": "#4f9cf9",
            "text": "#eef2f8",
            "muted": "#8a93a6",
        },
    }


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8123
    state_file: str = "manager_state.json"
    body_limit_bytes: int = 1_000_000
    switch_timeout_s: float = 10.0
    default_mode: str = "day"
    themes: dict[str, str] = field(default_factory=lambda: {"day": "d037", "night": "d029"})
    adapter: dict[str, dict] = field(default_factory=_default_palettes)
    # Set to a token to require `Authorization: Bearer <token>` (LAN producers).
    auth_token: str | None = None

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
