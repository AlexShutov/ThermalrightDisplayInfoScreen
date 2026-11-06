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

from hardwarescreen.adapter.driver import AdapterDriver
from hardwarescreen.adapter.poller import BridgePoller
from hardwarescreen.adapter.source import DashboardSource
from manager.store import AtomicStateFile

log = logging.getLogger("hardwarescreen.dashboard")

_KEY = "dashboard"


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
            return LinearDashboardRenderer(
                size, background=bg_image, fps=fps, fill_direction=fill_direction,
                layout=self._make_layout(),
            )
        log.info("dashboard: using Pillow renderer (HARDWARESCREEN_RENDERER=%s)", mode)
        return None

    @staticmethod
    def _make_layout():
        """Velocity layout manager from the ``layout`` env / explicit geometry.

        ``HARDWARESCREEN_LAYOUT`` selects the way the columns are laid out:
        ``auto`` (default, fraction-derived size + dynamic gap), ``auto_gap`` (an
        explicit bar size with an auto-derived gap, way 1), or ``explicit_gap`` (an
        explicit bar size + explicit gap, way 2).  The size/gap come from
        ``HARDWARESCREEN_BAR_SIZE`` / ``HARDWARESCREEN_GAP``.
        """
        from hardwarescreen.adapter.layout import (
            AutoGapLayoutManager,
            ExplicitGapLayoutManager,
        )

        layout = os.environ.get("HARDWARESCREEN_LAYOUT", "auto").strip().lower()

        def _float_env(name: str) -> float | None:
            raw = os.environ.get(name)
            if not raw:
                return None
            try:
                return float(raw)
            except ValueError:
                return None

        bar_size = _float_env("HARDWARESCREEN_BAR_SIZE")
        gap = _float_env("HARDWARESCREEN_GAP")
        if layout == "auto_gap":
            if bar_size is None:
                raise ValueError("auto_gap layout needs HARDWARESCREEN_BAR_SIZE")
            return AutoGapLayoutManager(bar_size=bar_size)
        if layout == "explicit_gap":
            if bar_size is None or gap is None:
                raise ValueError(
                    "explicit_gap layout needs HARDWARESCREEN_BAR_SIZE and HARDWARESCREEN_GAP"
                )
            return ExplicitGapLayoutManager(bar_size=bar_size, gap=gap)
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
