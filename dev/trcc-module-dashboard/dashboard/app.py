"""Phase 3: theme-manager dashboard adapter module (GPL-3.0, imports trcc).

Reads the manager's atomic bridge state file (``HARDWARESCREEN_STATE_FILE``),
publishes gauges to the panel as an OVERLAY ``FrameSource`` (on top of the
theme, which keeps playing beneath it), and defers ``overlay.show`` until a
panel is attached (the Phase 0 attach-race guard). The pure logic
(poller/layout/source/driver) lives in the MIT ``hardwarescreen`` package; this
module only wires it to ``host.overlay`` / ``host.panels``.
"""
# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import logging
import os
import threading
import time

from PIL import Image
from trcc.adapters.modules._base import BaseModule
from trcc.core.models import RawFrame
from trcc.core.ports import FrameSource

from hardwarescreen.adapter.dashboard import _palette
from hardwarescreen.adapter.driver import AdapterDriver
from hardwarescreen.adapter.poller import BridgePoller
from hardwarescreen.adapter.source import DashboardSource
from hardwarescreen.adapter.ui_density import resolve_density
from manager.store import AtomicStateFile

log = logging.getLogger("hardwarescreen.dashboard")


# Вспомогательные функции чтения env-настроек композита.


_KEY = "dashboard"

# Физическая диагональ панели Trofeo Vision (маркетинговый «11.3") в дюймах — для
# пересчёта «мм → px» через px_per_mm (когда размеры задаются в мм).
DEVICE_DIAGONAL_INCHES = 11.3
MM_PER_INCH = 25.4


def _optional_float_env(name: str) -> float | None:
    """Float из env (``HARDWARESCREEN_*``) либо ``None``, если пусто/не число."""
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        log.warning("dashboard: env %s=%r is not a float; ignored", name, raw)
        return None


def _optional_int_env(name: str) -> int | None:
    """Int из env (``HARDWARESCREEN_*``) либо ``None``, если пусто/не число."""
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        log.warning("dashboard: env %s=%r is not an int; ignored", name, raw)
        return None


def _rings_max_gauges(default: int) -> int:
    """Сколько первых датчиков показывать кольцами (``HARDWARESCREEN_RINGS_MAX_GAUGES``).

    Пустое/не число/`<= 0` → ``default`` (иначе работали бы все кольца без лимита).
    """
    value = _optional_int_env("HARDWARESCREEN_RINGS_MAX_GAUGES")
    return default if value is None or value <= 0 else value


def _ring_corner_fraction(
    env_name: str, mirror_fraction: float | None, default: float
) -> float:
    """Доля скругления конца сектора колец с той же пропорцией, что у линейного.

    Приоритет: явное ``HARDWARESCREEN_RING_*_CORNER_FRACTION`` → та же доля, что
    у линейного (``mirror_fraction``) → ``default``.  Зажимается в [0, 0.5] — доля
    толщины кольца (как у линейного — доля толщины столбца).
    """
    override = _optional_float_env(env_name)
    if override is not None:
        return min(max(0.0, override), 0.5)
    if mirror_fraction is not None:
        return min(max(0.0, mirror_fraction), 0.5)
    return default


def _color_palette_enabled() -> bool:
    """Включена ли цветовая палитра столбцов (градиент «значение → цвет»).

    ``HARDWARESCREEN_VALUE_COLOR`` = ``1``/``true``/``on`` → включено (по
    умолчанию); ``0``/``false``/``off`` → выключено (столбцы общим акцентом).
    """
    raw = (os.environ.get("HARDWARESCREEN_VALUE_COLOR") or "").strip().lower()
    return raw not in ("0", "false", "off", "no", "disable", "disabled")


def _resolve_px_per_mm(size: tuple[int, int], env_value: float | None) -> float:
    """Сколько пикселей панели приходится на 1 мм.

    Если задан ``HARDWARESCREEN_PX_PER_MM`` — используется он; иначе выводим из
    физической диагонали (11.3") и пиксельного размера панели (квадратные пиксели):
    ``px_per_mm = diagonal_px / diagonal_mm``.
    """
    if env_value is not None and env_value > 0:
        return env_value
    width = max(1, int(size[0]))
    height = max(1, int(size[1]))
    diagonal_px = (width * width + height * height) ** 0.5
    diagonal_mm = DEVICE_DIAGONAL_INCHES * MM_PER_INCH
    return diagonal_px / diagonal_mm


# --- Константы композита «линейный индикатор слева + кольца датчиков справа» ---
# Стандартное число датчиков, показываемых КОЛЬЦАМИ: «первые 4» (чаще всего это
# конфигурируемые датчики, а весь ряд линейного слева — все сенсоры).  Переопределяется
# через ``HARDWARESCREEN_RINGS_MAX_GAUGES``.
COMPOSITE_RINGS_MAX_GAUGES = 4
# Отступ (маржа) от краёв панели до ЗОНЫ КОЛЕЦ, мм. Зона колец занимает почти весь
# экран (полную высоту панели), а отступ до края задаёт рендерер колец (edge_margin).
COMPOSITE_RINGS_MARGIN_MM = 2.0
# Зазор между краями соседних колец, мм.
RING_GAP_MM = 1.0
# Радиус внутренней границы самого внутреннего кольца, мм (маленький центр →
# кольца толстые, почти на весь круг).
RINGS_INNER_RADIUS_MM = 7.0
# Доля полу-стороны, до которой заполняются кольца: близко к 1, чтобы кольца были
# большими (вплоть до отступа ``COMPOSITE_RINGS_MARGIN_MM`` от краёв зоны).
COMPOSITE_RINGS_OUTER_FRACTION = 0.96
# Зазор между левым линейным регионом и правой зоной колец, px.
COMPOSITE_INNER_GAP_PX = 8

# Время нарастания/затухания выделения колец в начале окна данных (выбор меняется
# вместе со сменой данных и держится весь показ ~5 с); то же, что у линейного.
RING_SELECTION_TRANSITION_S = 0.6


def _composite_enabled() -> bool:
    """Включён ли композит «линейный слева + кольца справа».

    ``HARDWARESCREEN_COMPOSITE`` = ``rings_right`` → композит; любой другой
    (в т.ч. пустой) → только линейный индикатор (прежнее поведение).
    """
    return (os.environ.get("HARDWARESCREEN_COMPOSITE") or "").strip().lower() == "rings_right"


def _composite_regions(size: tuple[int, int], px_per_mm: float) -> dict:
    """Регионы кадра для композита.

    Возвращает ``left_size`` (линейный индикатор), ``rings_size`` (квадратная зона
    колец — почти весь экран: полная высота панели) и ``rings_x``/``rings_y``
    (верхний левый угол зоны колец, у правого края панели). Отступ ``2мм`` от
    краёв до самих колец задаёт рендерер (см. ``COMPOSITE_RINGS_MARGIN_MM``).
    """
    panel_w, panel_h = int(size[0]), int(size[1])
    rings_side = panel_h
    rings_x = max(0, panel_w - rings_side)
    rings_y = 0
    left_width = max(1, rings_x - COMPOSITE_INNER_GAP_PX)
    return {
        "left_size": (left_width, panel_h),
        "rings_size": (rings_side, rings_side),
        "rings_x": rings_x,
        "rings_y": rings_y,
    }


class _BridgeFrameSource(FrameSource):
    """Adapts our raw-RGB DashboardSource into a trcc ``RawFrame``."""

    name = "dashboard.source"

    def __init__(self, source: DashboardSource) -> None:
        self._src = source

    def latest(self) -> RawFrame:
        w, h = self._src.size
        return RawFrame(data=self._src.latest(), width=w, height=h)

    def close(self) -> None:
        self._src.close()


class CompositeDashboardRenderer:
    """Композитный ``frame_renderer``: линейный индикатор слева + кольца справа.

    Контракт ``DashboardSource.frame_renderer``: ``__call__(setup, values, palette,
    size, background) -> bytes`` (RGBA ``w*h*4`` — ПРОЗРАЧНЫЙ оверлей). Обёрнутый
    Qt-линeйный рендерер рисует ЛЕВЫЙ регион (RGBA-оверлей), а
    ``activityrings.render_gauge_rings`` — зону колец справа (кольцо на каждый датчик);
    всё компонуется на прозрачном слое и возвращается единым RGBA-оверлеем. trcc
    кладёт его ПОВЕРХ живой темы панели, поэтому фон НЕ закрашиваем — сквозь
    прозрачные места видна тема (то же поведение, что у линейного пути).

    Кольца повторяют данные линейного: для каждого датчика ``min/max`` → доля,
    цвет — ``value_color`` (при цветовой палитре) или общий акцент палитры.
    """

    def __init__(
        self,
        linear,
        size: tuple[int, int],
        *,
        px_per_mm: float,
        value_palette_on: bool,
        ring_start_corner_fraction: float = 0.5,
        ring_end_corner_fraction: float = 0.0,
        ring_max_gauges: int | None = None,
    ):
        self._linear = linear
        self._size = (int(size[0]), int(size[1]))
        regions = _composite_regions(self._size, px_per_mm)
        self._left_size = regions["left_size"]
        self._rings_size = regions["rings_size"]
        self._rings_x = regions["rings_x"]
        self._rings_y = regions["rings_y"]
        self._value_palette_on = value_palette_on
        self._px_per_mm = px_per_mm
        # Сколько первых датчиков показывать КОЛЬЦАМИ. В отличие от левого линейного
        # (там всегда все столбцы), кольца могут показывать только первые ``N`` датчиков.
        # ``None`` — все датчики.
        self._ring_max_gauges = ring_max_gauges
        # Скругление концов сектора колец: та же пропорция, что у линейного
        # (по умолчанию), с возможностью отдельно переопределить через env.
        self._ring_start_corner = ring_start_corner_fraction
        self._ring_end_corner = ring_end_corner_fraction
        # Общий драйвер выделения для колец И линейного (одинаковые случайные датчики
        # на обоих графиках).  Создаётся лениво при первом рендере (``common.selection``
        # импортируем позже — он headless, но живёт в сабмодуле).
        self._selection = None
        self._selection_signature: object | None = None

    def __call__(self, setup, values, palette, size, background=None) -> bytes:
        pal = _palette(palette or {})
        canvas = Image.new("RGBA", self._size, (0, 0, 0, 0))
        if (setup or []) and (values or {}):
            now = time.monotonic()
            # Левый ряд показывает ВСЕ сенсоры (из ``setup`` плюс те, что есть только в
            # ``values``); кольца справа — только первые ``N`` из них.  ВЫДЕЛЕНИЕ общее:
            # случайные датчики выбираются из набора КОЛЕЦ (первые ``N``), чтобы один и тот
            # же столбец можно было подсветить и на линейном, и на кольце.
            shared_gauges = self._left_gauges(setup, values)
            rings_gauges = self._rings_gauges(shared_gauges)
            ring_focus = self._compute_ring_focus(rings_gauges, values, now)
            linear_focus = self._pad_ring_focus(ring_focus, len(shared_gauges))
            raw_left = self._linear(
                shared_gauges, values, palette, self._left_size, None, focus=linear_focus
            )
            left_img = self._bytes_to_image(raw_left, self._left_size)
            if left_img is not None:
                canvas.alpha_composite(left_img, (0, 0))
            rings_img = self._render_rings(rings_gauges, values, pal, ring_focus)
            if rings_img is not None:
                canvas.alpha_composite(rings_img, (self._rings_x, self._rings_y))
        return canvas.tobytes()

    @staticmethod
    def _bytes_to_image(raw: bytes, size: tuple[int, int]) -> Image.Image | None:
        width, height = size
        if len(raw) >= width * height * 4:
            return Image.frombytes("RGBA", size, raw[: width * height * 4])
        if len(raw) == width * height * 3:
            return Image.frombytes("RGB", size, raw)
        return None

    @staticmethod
    def _ring_content_signature(setup, values):
        """Отпечаток данных колец — его смена открывает новое окно выделения."""
        return (
            tuple((g.get("id"), g.get("label"), g.get("min"), g.get("max"),
                   g.get("unit")) for g in (setup or [])),
            tuple(sorted((values or {}).items())),
        )

    def _left_gauges(self, setup, values):
        """Набор датчиков для ЛЕВОГО ряда (все столбцы).

        Берём датчики из ``setup`` (у них есть min/max/label/unit), плюс синтезируем
        столбец для каждого датчика, который есть только в ``values`` — так линейный
        ряд показывает ВСЕ сенсоры, даже если в настройках задано меньше (например,
        setup = 4 конфигурируемых, а сенсоры дают 6).
        """
        gauges = list(setup)
        known_ids = {gauge.get("id") for gauge in gauges}
        for sensor_id in sorted((values or {}).keys()):
            if sensor_id not in known_ids:
                known_ids.add(sensor_id)
                gauges.append(
                    {
                        "id": sensor_id,
                        "label": str(sensor_id).upper(),
                        "unit": "",
                        "min": 0.0,
                        "max": 100.0,
                    }
                )
        return gauges

    def _rings_gauges(self, shared_gauges):
        """Набор датчиков для колец: все, либо только первые ``N`` (``ring_max_gauges``)."""
        if self._ring_max_gauges is not None and self._ring_max_gauges > 0:
            return list(shared_gauges[: self._ring_max_gauges])
        return list(shared_gauges)

    def _compute_ring_focus(self, rings_gauges, values, now):
        """Общий фокус выделения (2 случайных датчика) для колец И линейного.

        Выбираем случайные датчики из набора КОЛЕЦ (первые ``N``) — так один и тот же
        столбец всегда можно подсветить и на линейном, и на кольце (одинаковое
        выделение на обоих графиках).  Новое окно открывается при смене состава
        датчиков или их значений.
        """
        from common.selection import SelectionController  # noqa: PLC0415

        count = len(rings_gauges)
        if count <= 0:
            return []
        if self._selection is None:
            self._selection = SelectionController(
                transition_s=RING_SELECTION_TRANSITION_S, select_count=2
            )
        sig = self._ring_content_signature(rings_gauges, values)
        if sig != self._selection_signature:
            self._selection_signature = sig
            self._selection.enabled = True
            self._selection.advance(now, count)
        return self._selection.focus(now, count)

    def _pad_ring_focus(self, ring_focus, total):
        """Дополняет фокус колец нулями до числа столбцов линейного (индексы совпадают)."""
        return list(ring_focus) + [0.0] * max(0, total - len(ring_focus))

    def _render_rings(self, setup, values, pal, focus) -> Image.Image | None:
        try:
            from activityrings.rings import render_gauge_rings

            from hardwarescreen.adapter.dashboard import value_color
        except Exception as exc:  # noqa: BLE001 - кольца опциональны
            log.warning("dashboard: composite rings unavailable: %s", exc)
            return None
        entries: list[tuple[str, str, tuple[int, int, int], float]] = []
        progress: dict[str, float] = {}
        for gauge in setup:
            gid = gauge.get("id", "")
            ring_key = str(gid)
            vmin = float(gauge.get("min", 0))
            vmax = float(gauge.get("max", 100))
            raw = values.get(gid)
            denominator = (vmax - vmin) if vmax > vmin else 0
            if isinstance(raw, (int, float)) and denominator:
                fraction = (raw - vmin) / denominator
            else:
                fraction = 0.0
            fraction = max(0.0, min(1.0, fraction))
            if self._value_palette_on:
                color = tuple(value_color(fraction))
            else:
                color = tuple(pal.get("accent") or (250, 250, 250))
            entries.append((ring_key, gauge.get("label", ""), color, 1.0))
            progress[ring_key] = fraction
        if not entries:
            return None
        count = len(entries)
        # Фокус приходит из общего драйвера (см. ``_compute_ring_focus``); подгоняем
        # под число колец на случай рассинхрона.
        focus = list(focus[:count]) + [0.0] * max(0, count - len(focus))
        # Порядок колец «из центра наружу»: первый датчик линейного (слева) — центральное
        # (внутреннее) кольцо, последний — внешнее.  Раскладчик колец делает первым
        # внешнее кольцо, поэтому разворачиваем и порядок колец, и фокус.
        entries_for_rings = list(reversed(entries))
        focus_for_rings = list(reversed(focus))
        margin_px = round(COMPOSITE_RINGS_MARGIN_MM * self._px_per_mm)
        gap_mm = _optional_float_env("HARDWARESCREEN_RINGS_GAP_MM")
        if gap_mm is None:
            gap_mm = RING_GAP_MM
        gap_px = round(gap_mm * self._px_per_mm)
        inner_radius_mm = _optional_float_env("HARDWARESCREEN_RINGS_INNER_RADIUS_MM")
        if inner_radius_mm is None:
            inner_radius_mm = RINGS_INNER_RADIUS_MM
        inner_radius_px = round(inner_radius_mm * self._px_per_mm)
        try:
            return render_gauge_rings(
                entries_for_rings,
                self._rings_size,
                progress=progress,
                focus=focus_for_rings,
                outer_fraction=COMPOSITE_RINGS_OUTER_FRACTION,
                edge_margin=float(margin_px),
                gap_px=float(gap_px),
                inner_radius_px=float(inner_radius_px),
                track_color=tuple(pal.get("muted") or (0, 0, 0)),
                start_corner_fraction=self._ring_start_corner,
                end_corner_fraction=self._ring_end_corner,
                full_ring_glow=self._value_palette_on,
            )
        except Exception as exc:  # noqa: BLE001 - слишком много датчиков и т.п.
            log.warning("dashboard: cannot render gauge rings: %s", exc)
            return None


class DashboardModule(BaseModule, key=_KEY):
    """Bridge state -> dashboard gauges on the panel background."""

    title = "Dashboard"
    summary = "Theme-manager dashboard adapter"
    default_on = False

    def _enable(self, host) -> None:
        self._host = host
        state_file = os.environ.get("HARDWARESCREEN_STATE_FILE", "manager_state.json")
        keys = host.panels.attached()
        size = host.panels.size(keys[0]) if keys else (1920, 440)
        self._panel_key = keys[0] if keys else None
        self._poller = BridgePoller(AtomicStateFile(state_file))
        # Optional theme frame to composite the gauges over (the overlay's
        # backdrop beneath the live data). Populated via an env var in the
        # trcc daemon; absent -> solid palette background.
        bg_path = os.environ.get("HARDWARESCREEN_BACKGROUND_IMAGE")
        bg_image = self._load_background(bg_path)
        render_fps = float(os.environ.get("HARDWARESCREEN_RENDER_FPS", "8.0"))
        renderer = self._make_renderer(size, bg_image, render_fps)
        self._source = DashboardSource(
            self._poller, size, bg_image, frame_renderer=renderer, fps=render_fps
        )
        self._frame_source = _BridgeFrameSource(self._source)
        self._driver = AdapterDriver(
            host.overlay, self._frame_source, key=_KEY, fps=render_fps
        )
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info("dashboard: enabled (state_file=%s, size=%s)", state_file, (size,))

    def _loop(self) -> None:
        while not self._stop.is_set():
            # Keep the newest state across quiet ticks so a now-changed seq is not
            # mistaken for "no data" (which would end the dashboard every poll).
            state = self._poller.poll() or self._poller.last
            # The overlay belongs to a PANEL (device), not this module: trcc's
            # ``overlay.show``/``end`` take the panel key, so resolve it from
            # the attached panels (empty → module not yet attached: defer show).
            keys = self._host.panels.attached()
            self._panel_key = keys[0] if keys else self._panel_key
            self._driver.update(state, bool(keys), self._panel_key)
            time.sleep(0.5)

    def _make_renderer(self, size, bg_image, fps):
        """Qt (PySide6) linear-indicator renderer by default; ``pillow`` for fallback.

        The Qt renderer is injected as the ``DashboardSource`` frame_renderer (a stateful
        callable). ``activity_linear_indicator`` (Widgets submodule) + PySide6 are lazy
        imports, so enabling Qt only needs them present in the trcc interpreter.
        """
        mode = os.environ.get("HARDWARESCREEN_RENDERER", "qt").strip().lower()
        if mode == "qt":
            from hardwarescreen.adapter.linear_dashboard import LinearDashboardRenderer

            fill_direction = os.environ.get(
                "HARDWARESCREEN_FILL_DIRECTION", "left_to_right"
            ).strip().lower()
            px_per_mm = _resolve_px_per_mm(
                size, _optional_float_env("HARDWARESCREEN_PX_PER_MM")
            )
            density = resolve_density(
                os.environ.get("HARDWARESCREEN_DENSITY"), 1.0
            )
            renderer_kwargs = {
                "fps": fps,
                "fill_direction": fill_direction,
                "layout": self._make_layout(px_per_mm, density),
                "bar_corner_fraction": _optional_float_env(
                    "HARDWARESCREEN_BAR_CORNER_FRACTION"
                ),
                "fill_corner_fraction": _optional_float_env(
                    "HARDWARESCREEN_FILL_CORNER_FRACTION"
                ),
                "value_palette_on": _color_palette_enabled(),
                "density": density,
            }
            if _composite_enabled():
                region = _composite_regions(size, px_per_mm)
                linear = LinearDashboardRenderer(
                    region["left_size"], background=None, **renderer_kwargs
                )
                log.info(
                    "dashboard: composite renderer (linear-left %s + gauge rings right)",
                    (region["left_size"],),
                )
                return CompositeDashboardRenderer(
                    linear, size, px_per_mm=px_per_mm,
                    value_palette_on=_color_palette_enabled(),
                    ring_start_corner_fraction=_ring_corner_fraction(
                        "HARDWARESCREEN_RING_START_CORNER_FRACTION",
                        renderer_kwargs["bar_corner_fraction"], 0.5,
                    ),
                    ring_end_corner_fraction=_ring_corner_fraction(
                        "HARDWARESCREEN_RING_END_CORNER_FRACTION",
                        renderer_kwargs["fill_corner_fraction"], 0.0,
                    ),
                    ring_max_gauges=_rings_max_gauges(COMPOSITE_RINGS_MAX_GAUGES),
                )
            return LinearDashboardRenderer(size, background=bg_image, **renderer_kwargs)
        log.info("dashboard: using Pillow renderer (HARDWARESCREEN_RENDERER=%s)", mode)
        return None

    @staticmethod
    def _make_layout(px_per_mm: float, density: float = 1.0):
        """Velocity layout manager from the ``layout`` env / explicit geometry.

        ``HARDWARESCREEN_LAYOUT`` selects the way the columns are laid out:
        ``auto`` (default, fraction-derived size + dynamic gap), ``auto_gap`` (an
        explicit bar size with an auto-derived gap, way 1), or ``explicit_gap`` (an
        explicit bar size + explicit gap, way 2).  The size/gap come from the px env
        (``HARDWARESCREEN_BAR_SIZE`` / ``HARDWARESCREEN_GAP``) or, if set, from the
        mm env (``HARDWARESCREEN_BAR_SIZE_MM`` / ``HARDWARESCREEN_GAP_MM``) scaled by
        ``px_per_mm`` — так размеры можно задавать в миллиметрах независимо от
        разрешения панели.  Если заданы mm-размеры, раскладка считается явной.
        ``density`` — px-на-dp для зафиксированных дизайн-величин раскладки
        (отступ от края, клампы зазора, min толщина).
        """
        from hardwarescreen.adapter.layout import (
            AutoGapLayoutManager,
            ExplicitGapLayoutManager,
        )

        layout = os.environ.get("HARDWARESCREEN_LAYOUT", "auto").strip().lower()

        bar_size_mm = _optional_float_env("HARDWARESCREEN_BAR_SIZE_MM")
        gap_mm = _optional_float_env("HARDWARESCREEN_GAP_MM")
        padding_mm = _optional_float_env("HARDWARESCREEN_PADDING_MM")
        stack_align = os.environ.get(
            "HARDWARESCREEN_ALIGN", "center"
        ).strip().lower()
        bar_size = (
            round(bar_size_mm * px_per_mm)
            if bar_size_mm is not None
            else _optional_float_env("HARDWARESCREEN_BAR_SIZE")
        )
        gap = (
            round(gap_mm * px_per_mm)
            if gap_mm is not None
            else _optional_float_env("HARDWARESCREEN_GAP")
        )
        layout_kwargs = {"stack_align": stack_align, "density": density}
        if padding_mm is not None:
            layout_kwargs["padding_x"] = round(padding_mm * px_per_mm)
        if bar_size_mm is not None or gap_mm is not None:
            layout = "explicit_gap" if gap is not None else "auto_gap"

        if layout == "auto_gap":
            if bar_size is None:
                raise ValueError(
                    "auto_gap layout needs a bar size "
                    "(HARDWARESCREEN_BAR_SIZE or HARDWARESCREEN_BAR_SIZE_MM)"
                )
            return AutoGapLayoutManager(bar_size=bar_size, **layout_kwargs)
        if layout == "explicit_gap":
            if bar_size is None or gap is None:
                raise ValueError(
                    "explicit_gap layout needs a bar size and a gap"
                )
            return ExplicitGapLayoutManager(
                bar_size=bar_size, gap=gap, **layout_kwargs
            )
        if layout != "auto":
            raise ValueError(
                f"HARDWARESCREEN_LAYOUT must be 'auto', 'auto_gap' or 'explicit_gap', "
                f"got {layout!r}"
            )
        # "auto" — прежнее поведение (доля размера + динамический зазор): рендерер
        # сам подставляет SelectionLayoutManager, когда layout==None.
        return None

    def _load_background(self, path: str | None) -> Image.Image | None:
        if not path or not os.path.exists(path):
            return None
        try:
            img = Image.open(path)
            img.load()
            log.info("dashboard: overlaying gauges on theme frame %s (%s)", path, (img.size,))
            return img
        except Exception as exc:  # noqa: BLE001 - a bad theme frame must not kill the module
            log.warning("dashboard: cannot load background %s: %s", path, exc)
            return None

    def _disable(self, host) -> None:
        self._stop.set()
        key = self._driver.shown_key or self._panel_key or _KEY
        host.overlay.end(key)
        log.info("dashboard: ended overlay %s -> theme restored", key)

