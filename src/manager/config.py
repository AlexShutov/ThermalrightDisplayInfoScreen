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
class ThemeSettings:
    """Where the manager reads the user's last-selected theme from.

    The "theme settings app" (with the brightness slider) persists its current
    choice in a SQLite DB (``db_path``); the manager watches it and re-applies the
    theme to the panel.  ``db_path``/``trcc_json_path`` are explicit so relocating
    that app (or trcc's data dir) is a single config edit.
    """
    #: Path to the settings app's ``themes.db`` (or None to disable auto-apply).
    db_path: str | None = None
    #: The device key the theme is applied to (matches trcc's ``devices`` key).
    device_key: str = "0416:5408"
    #: Path to trcc's ``trcc.json`` for verification of what is actually applied
    #: (None -> resolve a platform default; a missing file disables verification).
    trcc_json_path: str | None = None
    #: Poll interval for DB/verification checks, in seconds.
    poll_s: float = 2.0
    #: Minimum interval between avoidable re-applies (debounce a down daemon).
    apply_cooldown_s: float = 5.0
    #: The trcc CLI command (executable + any leading args).
    trcc_cmd: list[str] = field(default_factory=lambda: ["trcc"])


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
    # Optional trcc switch trigger (CLI): e.g. ["trcc", "display", "play", "{theme}"].
    # "{theme}" is replaced with the mode's theme id from `themes`. When None, /theme
    # only persists state (no daemon call).
    theme_cmd: list[str] | None = None
    # Set to a token to require `Authorization: Bearer <token>` (LAN producers).
    auth_token: str | None = None
    # Source of the "current theme" the manager keeps applied: it reads the theme
    # settings app's own DB (db_path), watches it for changes and re-applies the
    # last user choice to the panel after a restart.  All paths live here so the
    # settings app can be relocated by editing one config value.
    theme_settings: ThemeSettings = field(default_factory=ThemeSettings)

    @staticmethod
    def load(path: str | None) -> Config:
        cfg = Config()
        if path and os.path.exists(path):
            with open(path) as fh:
                data = yaml.safe_load(fh) or {}
            for key, value in data.items():
                if not hasattr(cfg, key):
                    continue
                if key == "theme_settings" and isinstance(value, dict):
                    cfg.theme_settings = ThemeSettings(**value)
                else:
                    setattr(cfg, key, value)
        return cfg
