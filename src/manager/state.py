"""Pure manager state model + palette / bridge derivation (no frameworks).

Kept framework-free so it is testable headless and shared with the Qt adapter
(which reads the same bridge object). ``bridge_dict`` is the *data-plane*
payload; the ``StateStore`` attaches the monotonic ``seq`` + timestamp.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

Mode = Literal["day", "night"]

PALETTE_KEYS = ("background", "panel", "accent", "text", "muted")


@dataclass(frozen=True)
class Gauge:
    id: str
    label: str
    unit: str = ""
    min: float = 0.0
    max: float = 100.0


@dataclass
class ManagerState:
    mode: Mode = "day"
    themes: dict[str, str] = field(default_factory=dict)  # day/night -> theme id
    adapter: dict[str, dict] = field(default_factory=dict)  # day/night -> palette
    setup: list[Gauge] = field(default_factory=list)
    values: dict[str, float] = field(default_factory=dict)


def active_palette(state: ManagerState) -> dict:
    """Return the palette for the current mode (empty if unset)."""
    return dict(state.adapter.get(state.mode, {}))


def active_theme(state: ManagerState) -> str | None:
    """Return the theme id for the current mode (None if unset)."""
    return state.themes.get(state.mode)


def merge_values(current: dict[str, float], update: dict[str, float]) -> dict[str, float]:
    """Last-write-wins merge; keeps only the keys in ``current`` or ``update``."""
    merged = dict(current)
    merged.update(update)
    return merged


def bridge_dict(state: ManagerState) -> dict:
    """Serialize the bridge payload (seq/updated_at are added by the store)."""
    return {
        "mode": state.mode,
        "active_theme": active_theme(state),
        "palette": active_palette(state),
        "setup": [asdict(g) for g in state.setup],
        "values": dict(state.values),
    }
