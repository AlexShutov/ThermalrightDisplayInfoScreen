"""Layout managers: compute the geometry of the dashboard's bar widgets.

Mirrors the Android SDK layout-manager idea: a dedicated object measures item
positions inside a container, so the renderer only needs to draw at the returned
rects.  Layout is decoupled from drawing/theme/focus code, so it is trivially
testable and reusable for future panel sizes.

A base class holds the shared configuration (paddings, dynamic gap, selection
scale) and two subclasses implement the two modes selected by
``is_selection_enabled``:

- ``StaticLayoutManager`` (``is_selection_enabled=False``): every bar is the same
  height; the stack fills the container height within the margins.
- ``SelectionLayoutManager`` (``is_selection_enabled=True``): the focused bar grows
  in thickness (``base * selection_scale``) while the others keep their size.  The
  base height is sized so the stack (with one bar at full scale) still fits inside
  the container with a small edge margin, and the stack is centred — so when the
  topmost/bottommost bar is selected it keeps ~2 mm (``edge_margin``) to the panel
  edge, and nothing overflows or shifts (positions are stable, only heights ease).
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class BarGeometry:
    """A bar ``x``/``y`` (top-left) and ``width``/``height`` inside the panel."""

    x: float
    y: float
    width: float
    height: float

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2.0


@dataclass(frozen=True)
class LayoutResult:
    """Measured layout: per-item geometry plus the common bar width."""

    geometry: Sequence[BarGeometry]
    bar_width: float

    def __getitem__(self, index: int) -> BarGeometry:
        return self.geometry[index]

    def __len__(self) -> int:
        return len(self.geometry)


class BaseLayoutManager:
    """Shared config + dynamic gap; subclasses implement ``measure``."""

    is_selection_enabled: bool = False

    def __init__(
        self,
        *,
        padding_x: float = 48.0,
        edge_margin: float = 14.0,
        gap_min: float = 8.0,
        gap_max: float = 28.0,
        gap_ratio: float = 0.2,
        selection_scale: float = 1.22,
        min_slot: float = 8.0,
    ) -> None:
        self._padding_x = padding_x
        self._edge_margin = edge_margin
        self._gap_min = gap_min
        self._gap_max = gap_max
        self._gap_ratio = gap_ratio
        self._selection_scale = selection_scale
        self._min_slot = min_slot

    @property
    def padding_x(self) -> float:
        return self._padding_x

    @property
    def selection_scale(self) -> float:
        return self._selection_scale

    def gap_for(self, container_h: float, count: int) -> float:
        """Gap between bars, scaled from the slot height (count + screen size)."""
        if count <= 1:
            return 0.0
        slot = container_h / max(1, count)
        gap = slot * self._gap_ratio
        return min(self._gap_max, max(self._gap_min, gap))

    def _resolve_gap(self, container_h: float, count: int, base: float) -> float:
        """Collapse the gap to zero rather than let bars fall below ``min_slot``."""
        gap = self.gap_for(container_h, count)
        if base < self._min_slot and count > 1:
            gap = 0.0
        return gap

    def measure(
        self,
        container_w: float,
        container_h: float,
        count: int,
        focus: Sequence[float] | None = None,
    ) -> LayoutResult:
        raise NotImplementedError


class StaticLayoutManager(BaseLayoutManager):
    """Equal-height bars filling the container height within the margins."""

    is_selection_enabled = False

    def measure(self, container_w, container_h, count, focus=None) -> LayoutResult:
        n = max(0, int(count))
        bar_width = max(1.0, container_w - 2.0 * self._padding_x)
        if n == 0:
            return LayoutResult((), bar_width)
        gap = self.gap_for(container_h, n)
        base = (container_h - 2.0 * self._edge_margin - gap * (n - 1)) / n
        gap = self._resolve_gap(container_h, n, base)
        base = (container_h - 2.0 * self._edge_margin - gap * (n - 1)) / n
        base = max(self._min_slot, base)
        total = base * n + gap * (n - 1)
        start_y = (container_h - total) / 2.0
        geometry = tuple(
            BarGeometry(self._padding_x, start_y + i * (base + gap), bar_width, base)
            for i in range(n)
        )
        return LayoutResult(geometry, bar_width)


class SelectionLayoutManager(BaseLayoutManager):
    """The focused bar grows in thickness; the others keep their size.

    ``base`` is sized for the worst case (one bar at full ``selection_scale``) so
    the stack never overflows and the neighbours never shrink; the stack is centred
    so an extreme bar, when selected, keeps ``edge_margin`` (~2 mm) to the panel
    edge.  Heights ease with the per-item ``focus`` factor; positions stay fixed.
    """

    is_selection_enabled = True

    def measure(self, container_w, container_h, count, focus=None) -> LayoutResult:
        n = max(0, int(count))
        bar_width = max(1.0, container_w - 2.0 * self._padding_x)
        if n == 0:
            return LayoutResult((), bar_width)
        scale = self._selection_scale
        gap = self.gap_for(container_h, n)
        # Worst-case base: one bar at scale, n-1 at 1, within the margins.
        base = (container_h - 2.0 * self._edge_margin - gap * (n - 1)) / (n + scale - 1.0)
        gap = self._resolve_gap(container_h, n, base)
        base = (container_h - 2.0 * self._edge_margin - gap * (n - 1)) / (n + scale - 1.0)
        base = max(self._min_slot, base)
        total = base * n + gap * (n - 1)
        start_y = (container_h - total) / 2.0
        f = list(focus) if focus is not None else [0.0] * n
        geometry = []
        for i in range(n):
            fi = max(0.0, min(1.0, f[i])) if i < len(f) else 0.0
            height = base * (1.0 + (scale - 1.0) * fi)
            center = start_y + i * (base + gap) + base / 2.0
            geometry.append(
                BarGeometry(self._padding_x, center - height / 2.0, bar_width, height)
            )
        return LayoutResult(tuple(geometry), bar_width)
