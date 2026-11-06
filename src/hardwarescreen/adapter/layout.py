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
- ``SelectionLayoutManager`` (``is_selection_enabled=True``): the measured geometry is
  constant — every bar has the same height and the same uniform gap, exactly like the
  static layout.  Selection is expressed by the renderer on top of this constant
  geometry: the non-selected bars are dimmed, the selected bar grows in thickness,
  sits in the foreground (higher z-order) and gets a green glow.
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
        # Доля слота как зазор между барами (~0.4 см при 6 барах на 1920x440, ~70 px/см).
        gap_ratio: float = 0.38,
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

    @property
    def edge_margin(self) -> float:
        return self._edge_margin

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

    def _measure_constant(self, container_w: float, container_h: float, count: int) -> LayoutResult:
        """Constant geometry: equal-height bars with a uniform gap (no selection)."""
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


class StaticLayoutManager(BaseLayoutManager):
    """Equal-height bars filling the container height within the margins."""

    is_selection_enabled = False

    def measure(self, container_w, container_h, count, focus=None) -> LayoutResult:
        return self._measure_constant(container_w, container_h, count)


class SelectionLayoutManager(BaseLayoutManager):
    """Constant geometry; the selection is purely a render-side effect.

    The bars always keep the same height and the same uniform gap between them
    (identical to ``StaticLayoutManager``), so the measured geometry never changes
    with focus.  The selected bar is instead expressed by the renderer as a visual
    effect on top of this constant layout: the other bars are dimmed, the selected
    bar grows in thickness and is pushed onto the foreground (higher z-order) with
    a green glow — see ``LinearDashboardRenderer``.  ``is_selection_enabled`` stays
    ``True`` so the renderer knows to apply that effect; the geometry itself is
    constant.
    """

    is_selection_enabled = True

    def measure(self, container_w, container_h, count, focus=None) -> LayoutResult:
        return self._measure_constant(container_w, container_h, count)
