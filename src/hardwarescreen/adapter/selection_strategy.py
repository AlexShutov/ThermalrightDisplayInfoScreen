"""Стратегии роста выбранных индикаторов в линейном Qt-дашборде (MIT).

Отрисовка бара в ``LinearDashboardRenderer`` получает список «выбранных» индикаторов —
фокусов ``0..1`` на каждый бар.  Рост бара вокруг его центра (по толщине: ширина для
вертикальных столбцов, высота для горизонтальных полос) зависит от того, сколько
индикаторов выбрано и стоят ли выбранные рядом.  Стратегии — внутренняя деталь
рендерера: они получают фокусы, геометрию раскладки, размер панели, поле, масштаб
выделения и признак вертикальности, и возвращают готовые прямоугольники ``QRectF``.

- ``SelectSingleIndicator`` — прежнее поведение: каждый выбранный бар растёт на
  ``selection_scale`` (с модуляцией фокусом), невыбранные остаются ``1.0``, рост
  клипится в поля панели.
- ``SelectMultipleIndicators`` — используется только когда выбранных больше одного.
  Если выбранные не являются соседними (нет пары ``i`` и ``i+1`` среди выбранных) —
  ведёт себя как ``SelectSingleIndicator``.  Если среди выбранных есть соседние — их
  рост урезается так, чтобы между увеличенными полосами остался зазор не менее
  ``min_gap_px`` (достаточный для свечения, чтобы свечения соседних выбранных не
  смешивались); если зазор и без роста уже меньше ``min_gap_px`` — рост этой пары
  нулевой.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

from collections.abc import Sequence

from .layout import BarGeometry

# ``QRectF`` импортируем лениво, чтобы импорт этого модуля (и ``hardwarescreen``) не
# требовал Qt; ``pySide6`` нужен только в момент реальной отрисовки.
_QRECTF = None


def _rect(x: float, y: float, width: float, height: float):
    """Создаёт ``QRectF`` (лениво) либо подменённый в тестах прямоугольник."""
    global _QRECTF
    if _QRECTF is None:
        from PySide6.QtCore import QRectF

        _QRECTF = QRectF
    return _QRECTF(x, y, width, height)


def count_selected(focus: Sequence[float]) -> int:
    """Сколько индикаторов сейчас «выбрано» (фокус строго больше нуля)."""
    return sum(1 for f in focus if f > FOCUS_ACTIVE_EPSILON)


def select_strategy(focus: Sequence[float], selection_on: bool):
    """Возвращает стратегию по числу выбранных индикаторов.

    Больше одной выбранной — ``SelectMultipleIndicators``; одна (или ни одной, в том
    числе при выключенном выделении) — ``SelectSingleIndicator``.
    """
    if selection_on and count_selected(focus) > 1:
        return SelectMultipleIndicators()
    return SelectSingleIndicator()


class SelectionStrategy:
    """Базовый интерфейс стратегии роста выбранных индикаторов."""

    def compute(
        self,
        focus: Sequence[float],
        geometry: Sequence[BarGeometry],
        panel_w: float,
        panel_h: float,
        edge_margin: float,
        selection_scale: float,
        vertical: bool,
        min_gap_px: float,
    ) -> list:
        """Возвращает список ``QRectF`` для отрисовки всех баров раскладки.

        Каждый бар растёт вокруг своего центра на ``selection_scale``, модулированный
        фокусом; итоговый прямоугольник клипится в поля панели (как раньше).
        """
        scales = self._build_scales(focus, geometry, selection_scale, min_gap_px, vertical)
        rects = []
        for index, geo in enumerate(geometry):
            f = focus[index] if index < len(focus) else 0.0
            scale = 1.0 + (scales[index] - 1.0) * f
            rects.append(_grow_rect(geo, scale, panel_w, panel_h, edge_margin, vertical))
        return rects

    def _build_scales(
        self,
        focus: Sequence[float],
        geometry: Sequence[BarGeometry],
        selection_scale: float,
        min_gap_px: float,
        vertical: bool,
    ) -> list[float]:
        """Максимальный масштаб роста каждого бара при полном фокусе (1.0)."""
        raise NotImplementedError


class SelectSingleIndicator(SelectionStrategy):
    """Прежнее поведение: каждая выбранная растёт на ``selection_scale`` целиком."""

    def _build_scales(
        self,
        focus: Sequence[float],
        geometry: Sequence[BarGeometry],
        selection_scale: float,
        min_gap_px: float,
        vertical: bool,
    ) -> list[float]:
        return [selection_scale] * len(geometry)


class SelectMultipleIndicators(SelectionStrategy):
    """Рост нескольких выбранных с урезанием соседних пар до зазора ``min_gap_px``."""

    def _build_scales(
        self,
        focus: Sequence[float],
        geometry: Sequence[BarGeometry],
        selection_scale: float,
        min_gap_px: float,
        vertical: bool,
    ) -> list[float]:
        count = len(geometry)
        scales = [selection_scale] * count
        active = [
            i
            for i in range(count)
            if i < len(focus) and focus[i] > FOCUS_ACTIVE_EPSILON
        ]
        # Каждой СМЕЖНОЙ паре выбранных (i и i+1) ограничиваем общий масштаб, чтобы
        # после роста между краями остался зазор не меньше ``min_gap_px``.
        for prev, cur in zip(active, active[1:], strict=False):
            if cur != prev + 1:
                continue
            cap = _adjacent_pair_cap(
                geometry[prev], geometry[cur], selection_scale, min_gap_px, vertical
            )
            scales[prev] = min(scales[prev], cap)
            scales[cur] = min(scales[cur], cap)
        # Невыбранные бары не растут (фокус 0 → масштаб всё равно 1.0).
        for i in range(count):
            if i >= len(focus) or focus[i] <= FOCUS_ACTIVE_EPSILON:
                scales[i] = 1.0
        return scales


def _adjacent_pair_cap(
    geo_a: BarGeometry,
    geo_b: BarGeometry,
    selection_scale: float,
    min_gap_px: float,
    vertical: bool,
) -> float:
    """Общий потолок масштаба для пары выбранных; ``>= 1.0``.

    Ограничивает рост так, чтобы при росте обоих баров на этот масштаб зазор между их
    краями по оси роста остался не меньше ``min_gap_px``.  Если зазора нет и при
    масштабе ``1.0`` (меньше ``min_gap_px``) — возвращает ``1.0`` (рост нулевой:
    пару не разводим шире, чем она уже есть).
    """
    center_a = geo_a.x + geo_a.width / 2.0 if vertical else geo_a.center_y
    center_b = geo_b.x + geo_b.width / 2.0 if vertical else geo_b.center_y
    center_dist = abs(center_b - center_a)
    base_a = geo_a.width if vertical else geo_a.height
    base_b = geo_b.width if vertical else geo_b.height
    base = max(1.0, (base_a + base_b) / 2.0)
    gap_before = center_dist - base
    delta = max(0.0, (gap_before - min_gap_px) / base)
    return 1.0 + min(selection_scale - 1.0, delta)


def _grow_rect(
    geo: BarGeometry,
    scale: float,
    panel_w: float,
    panel_h: float,
    edge_margin: float,
    vertical: bool,
):
    """Растит один бара вокруг центра и клипит его в поля панели.

    Для вертикальных столбцов растёт ширина (вдоль X), для горизонтальных полос —
    высота (вдоль Y); центр бара сохраняется, поэтому рост симметричен в обе стороны.
    """
    if vertical:
        grown = geo.width * scale
        cx = geo.x + geo.width / 2.0
        left = max(float(edge_margin), min(float(panel_w) - float(edge_margin), cx - grown / 2.0))
        right = max(left, min(float(panel_w) - float(edge_margin), cx + grown / 2.0))
        return _rect(left, geo.y, max(0.0, right - left), geo.height)
    grown = geo.height * scale
    cy = geo.center_y
    top = max(float(edge_margin), min(float(panel_h) - float(edge_margin), cy - grown / 2.0))
    bottom = max(top, min(float(panel_h) - float(edge_margin), cy + grown / 2.0))
    return _rect(geo.x, top, geo.width, max(0.0, bottom - top))


# --- Константы ---
# Порог «индикатор выбран»: строго больший нуля, но с крошечным допуском, чтобы
# численный ноль (0.0) или остатки после плавного затухания не считались выбором.
FOCUS_ACTIVE_EPSILON = 1e-9
