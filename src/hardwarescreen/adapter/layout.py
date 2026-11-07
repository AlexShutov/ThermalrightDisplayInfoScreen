"""Layout managers: compute the geometry of the dashboard's bar widgets.

Mirrors the Android SDK layout-manager idea: a dedicated object measures item
positions inside a container, so the renderer only needs to draw at the returned
rects.  Layout is decoupled from drawing/theme/focus code, so it is trivially
testable and reusable for future panel sizes.

A base class holds the shared configuration (paddings, dynamic gap, selection
scale) and the subclasses implement the layout modes.  Every subclass keeps the
``is_selection_enabled`` flag, which drives whether the renderer applies the
selection effect (dim the others, grow + glow the selected bar):

- ``StaticLayoutManager`` (``is_selection_enabled=False``): every bar is the same
  height; the stack fills the container height within the margins.
- ``SelectionLayoutManager`` (``is_selection_enabled=True``): the measured geometry is
  constant — every bar has the same height and the same uniform gap, exactly like the
  static layout.  Selection is expressed by the renderer on top of this constant
  geometry: the non-selected bars are dimmed, the selected bar grows in thickness,
  sits in the foreground (higher z-order) and gets a green glow.
- ``AutoGapLayoutManager`` (way 1): an explicit ``bar_size`` (column width for
  vertical / bar height for horizontal) plus the count; the gap is auto-derived and
  the stack is centered.
- ``ExplicitGapLayoutManager`` (way 2): the count, an explicit ``bar_size``, and an
  explicit ``gap`` between columns; the stack is centered.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .ui_density import dp


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
        stack_align: str = "center",
        density: float = 1.0,
    ) -> None:
        # Дизайн-величины (отступ от края, клампы зазора, min толщина) задаются в dp
        # и сразу приводятся к px через плотность (см. ``ui_density``).  ``padding_x``
        # остаётся авторингом: его может переопределять физический мм-размер
        # (``HARDWARESCREEN_PADDING_MM``), который уже переведён в px на входе.
        self._padding_x = padding_x
        self._edge_margin = dp(edge_margin, density)
        self._gap_min = dp(gap_min, density)
        self._gap_max = dp(gap_max, density)
        self._gap_ratio = gap_ratio
        self._selection_scale = selection_scale
        self._min_slot = dp(min_slot, density)
        # Выравнивание стека вдоль оси раскладки: "left" (для вертикальных столбцов —
        # к левому краю, для горизонтальных — к верхнему), "right"/"center".  Полезно,
        # когда индикатор занимает только часть панели (например, слева).
        self._stack_align = stack_align if stack_align in ("left", "center", "right") else "center"

    @property
    def padding_x(self) -> float:
        return self._padding_x

    @property
    def selection_scale(self) -> float:
        return self._selection_scale

    @property
    def edge_margin(self) -> float:
        return self._edge_margin

    def gap_for(self, container_size: float, count: int) -> float:
        """Gap between bars, scaled from the slot size along the stacking axis
        (screen height for horizontal bars, screen width for vertical columns)."""
        if count <= 1:
            return 0.0
        slot = container_size / max(1, count)
        gap = slot * self._gap_ratio
        return min(self._gap_max, max(self._gap_min, gap))

    def _resolve_gap(self, container_size: float, count: int, base: float) -> float:
        """Collapse the gap to zero rather than let bars fall below ``min_slot``."""
        gap = self.gap_for(container_size, count)
        if base < self._min_slot and count > 1:
            gap = 0.0
        return gap

    def _stack_start(self, container_size: float, total: float,
                     start_margin: float) -> float:
        """Начальная координата стека вдоль оси раскладки.

        ``stack_align="left"`` — прижать к началу оси (для вертикальных столбцов —
        отступ ``padding_x`` у левого края, для горизонтальных — ``edge_margin`` у
        верхнего); ``"right"`` — к концу оси; иначе центрировать в панели.
        """
        if self._stack_align == "left":
            return start_margin
        if self._stack_align == "right":
            return container_size - start_margin - total
        return (container_size - total) / 2.0

    def measure(
        self,
        container_w: float,
        container_h: float,
        count: int,
        focus: Sequence[float] | None = None,
        *,
        vertical: bool = False,
    ) -> LayoutResult:
        raise NotImplementedError

    def _measure_constant(
        self,
        container_w: float,
        container_h: float,
        count: int,
        *,
        vertical: bool = False,
    ) -> LayoutResult:
        """Constant geometry: equal-size bars with a uniform gap (no selection).

        ``vertical=True`` lays the bars out as columns side by side along X (each
        column is ``base`` wide and fills the container height within the margins);
        otherwise the bars are horizontal rows stacked along Y.
        """
        n = max(0, int(count))
        if vertical:
            # Столбцы «растягиваются по высоте»: их длина — вся высота панели, а
            # толщина — компактная доля этой высоты (как толщина полосы референса
            # activity_linear_indicator), а не равномерный раздел ширины экрана —
            # иначе при малом числе индикаторов столбцы становятся широкими.
            # Временное растягивание ряда по всей ширине: зазор заполняет остаток,
            # чтобы группа столбцов занимала панель от левого до правого края.
            column_height = max(1.0, container_h - 2.0 * self._edge_margin)
            if n == 0:
                return LayoutResult((), column_height)
            usable_width = container_w - 2.0 * self._padding_x
            column_width = max(
                self._min_slot, column_height * VERTICAL_COLUMN_WIDTH_FRACTION
            )
            if column_width * n > usable_width:
                # Не влезает — убираем зазор и сжимаем столбцы до доступной ширины.
                column_width = usable_width / n
                gap = 0.0
            elif n > 1:
                # Остаток ширины распределяем равномерно между столбцами.
                gap = (usable_width - column_width * n) / (n - 1)
            else:
                gap = 0.0
            total = column_width * n + gap * (n - 1)
            start_x = self._padding_x + (usable_width - total) / 2.0
            geometry = tuple(
                BarGeometry(
                    start_x + i * (column_width + gap),
                    self._edge_margin,
                    column_width,
                    column_height,
                )
                for i in range(n)
            )
            return LayoutResult(geometry, column_width)
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

    def _measure_explicit(
        self,
        container_w: float,
        container_h: float,
        count: int,
        *,
        vertical: bool = False,
        bar_size: float,
        gap: float | None,
    ) -> LayoutResult:
        """Explicit geometry: items of a fixed ``bar_size`` placed with a fixed ``gap``.

        ``vertical=True`` lays columns side by side along X (each ``bar_size`` wide,
        filling the container height within the margins); otherwise the items are
        horizontal rows stacked along Y (each ``bar_size`` tall, spanning the container
        width).  ``gap`` of None auto-derives the spacing from ``gap_for``; otherwise
        the given pixel value is used.  The stack is centered within the container.

        If the items exceed the available space, the gap collapses to zero and the
        ``bar_size`` is clamped to fit, so the stack never overflows the panel.
        """
        n = max(0, int(count))
        if vertical:
            column_height = max(1.0, container_h - 2.0 * self._edge_margin)
            if n == 0:
                return LayoutResult((), column_height)
            usable_width = container_w - 2.0 * self._padding_x
            column_width = max(1.0, float(bar_size))
            gap = self.gap_for(container_w, n) if gap is None else max(0.0, float(gap))
            total = column_width * n + gap * (n - 1)
            if total > usable_width:
                gap = 0.0
                total = column_width * n
                if total > usable_width:
                    column_width = usable_width / n
                    total = column_width * n
            start_x = self._stack_start(container_w, total, self._padding_x)
            geometry = tuple(
                BarGeometry(
                    start_x + i * (column_width + gap),
                    self._edge_margin,
                    column_width,
                    column_height,
                )
                for i in range(n)
            )
            return LayoutResult(geometry, column_width)
        bar_width = max(1.0, container_w - 2.0 * self._padding_x)
        if n == 0:
            return LayoutResult((), bar_width)
        usable_height = container_h - 2.0 * self._edge_margin
        bar_height = max(1.0, float(bar_size))
        gap = self.gap_for(container_h, n) if gap is None else max(0.0, float(gap))
        total = bar_height * n + gap * (n - 1)
        if total > usable_height:
            gap = 0.0
            total = bar_height * n
            if total > usable_height:
                bar_height = usable_height / n
                total = bar_height * n
        start_y = self._stack_start(container_h, total, self._edge_margin)
        geometry = tuple(
            BarGeometry(
                self._padding_x, start_y + i * (bar_height + gap), bar_width, bar_height
            )
            for i in range(n)
        )
        return LayoutResult(geometry, bar_width)


class StaticLayoutManager(BaseLayoutManager):
    """Equal-height bars filling the container height within the margins."""

    is_selection_enabled = False

    def measure(
        self, container_w, container_h, count, focus=None, *, vertical=False
    ) -> LayoutResult:
        return self._measure_constant(container_w, container_h, count, vertical=vertical)


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

    def measure(
        self, container_w, container_h, count, focus=None, *, vertical=False
    ) -> LayoutResult:
        return self._measure_constant(container_w, container_h, count, vertical=vertical)


class AutoGapLayoutManager(BaseLayoutManager):
    """Способ 1: явная ширина/высота бара, автоматический зазор и число столбцов.

    ``bar_size`` — ширина вертикального столбца (или высота горизонтального бара);
    зазор между элементами подбирается из ``gap_for`` (как в авто-раскладке), а стек
    центрируется в панели.  Выделение — эффект на стороне рендера (как в
    ``SelectionLayoutManager``): невыделенные бары затемняются, выделенный растёт и
    подсвечивается зелёным свечением.
    """

    is_selection_enabled = True

    def __init__(self, bar_size: float, **kwargs) -> None:
        super().__init__(**kwargs)
        self._bar_size = float(bar_size)

    def measure(
        self, container_w, container_h, count, focus=None, *, vertical=False
    ) -> LayoutResult:
        return self._measure_explicit(
            container_w, container_h, count, vertical=vertical,
            bar_size=self._bar_size, gap=None,
        )


class ExplicitGapLayoutManager(BaseLayoutManager):
    """Способ 2: число столбцов, ширина столбца и расстояние между столбцами.

    Вся геометрия задана явно (``bar_size`` + ``gap``), стек центрируется в панели.
    Выделение — эффект на стороне рендера (как в ``SelectionLayoutManager``).
    """

    is_selection_enabled = True

    def __init__(self, bar_size: float, gap: float, **kwargs) -> None:
        super().__init__(**kwargs)
        self._bar_size = float(bar_size)
        self._gap = max(0.0, float(gap))

    def measure(
        self, container_w, container_h, count, focus=None, *, vertical=False
    ) -> LayoutResult:
        return self._measure_explicit(
            container_w, container_h, count, vertical=vertical,
            bar_size=self._bar_size, gap=self._gap,
        )


# --- Константы ---
# Толщина вертикального столбца как доля его высоты (по аналогии с толщиной полосы
# референса activity_linear_indicator): длина столбца — вся высота панели, а толщина
# компактная, чтобы при малом числе индикаторов столбцы не растягивались по ширине.
VERTICAL_COLUMN_WIDTH_FRACTION = 0.165
