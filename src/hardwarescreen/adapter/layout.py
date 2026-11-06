"""Layout manager: computes the geometry of the dashboard's bar widgets.

Mirrors the Android SDK ``LinearLayoutManager`` idea: a dedicated object measures
item positions inside a container, so the renderer only needs to draw at the
returned rects.  Layout is decoupled from drawing/theme/focus code, so it is
trivially testable and reusable for future panel sizes.

All bars are full-width horizontal widgets stacked top-to-bottom with a fixed gap.
Heights are computed to fill the available height exactly (container minus the
top/bottom padding), so the stack always fits inside the panel with a small margin
and never overflows.  The layout is static: the focus (selection) is a draw-time
concern, so selecting one item never resizes or displaces the others.
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


class DashboardLayoutManager:
    """Linear vertical layout of full-width horizontal bar widgets.

    Parameters mirror a small ``LinearLayoutManager``: the container paddings set
    the panel margins (the default top/bottom roughly equal 0.5 cm on the
    1920x440 panel, ~68 px/cm), ``gap`` is the space between bars, and
    ``min_height`` is the smallest bar allowed before the gap is compressed.
    """

    def __init__(
        self,
        *,
        padding_x: float = 48.0,
        padding_top: float = 34.0,
        padding_bottom: float = 34.0,
        gap: float = 16.0,
        min_height: float = 8.0,
    ) -> None:
        self._padding_x = padding_x
        self._padding_top = padding_top
        self._padding_bottom = padding_bottom
        self._gap = gap
        self._min_height = min_height

    @property
    def padding_x(self) -> float:
        return self._padding_x

    @property
    def padding_top(self) -> float:
        return self._padding_top

    @property
    def padding_bottom(self) -> float:
        return self._padding_bottom

    def measure(self, container_w: float, container_h: float, count: int) -> LayoutResult:
        """Measure ``count`` bars to fill the container height within the margins.

        The stack never exceeds the container: bar height is chosen so the last
        bar's bottom edge lands exactly on ``container_h - padding_bottom``.  If
        there is not enough room at ``min_height`` with the configured gap, the gap
        is collapsed to zero first; only an absurdly large ``count`` falls below
        ``min_height`` (and then bars get thinner rather than overflowing).
        """
        count = max(0, int(count))
        bar_width = max(1.0, container_w - 2.0 * self._padding_x)
        if count == 0:
            return LayoutResult((), bar_width)

        available = max(0.0, container_h - self._padding_top - self._padding_bottom)
        n = count
        gap = self._gap if n > 1 else 0.0
        height = (available - gap * (n - 1)) / n if n else 0.0
        if height < self._min_height and n > 1:
            gap = 0.0
            height = available / n
        height = max(0.0, height)

        geometry = tuple(
            BarGeometry(
                x=self._padding_x,
                y=self._padding_top + i * (height + gap),
                width=bar_width,
                height=height,
            )
            for i in range(n)
        )
        return LayoutResult(geometry, bar_width)
