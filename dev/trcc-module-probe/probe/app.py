"""Phase 0 probe: validate the module frame-source -> panel cycle.

A module that, when switched on, becomes the panel's background via
``host.background.show`` and drives a ``FrameSource`` ("latest() -> RawFrame"),
proving the cycle the theme-manager + Qt adapter will rely on. The frame is a
simple RGB24 pattern (no Qt yet); the base-video decode and Qt overlay are the
next refinement on this seam. GPL-3.0 -- imports trcc internals.
"""

# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import logging

from trcc.adapters.modules._base import BaseModule
from trcc.core.models import RawFrame
from trcc.core.ports import FrameSource

log = logging.getLogger("trofeo.probe")


class _Pattern(FrameSource):
    """Draws a static RGB24 test pattern; the newest picture each tick."""

    name = "probe.pattern"

    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self._closed = False

    def latest(self) -> RawFrame:
        return self._render()

    def close(self) -> None:
        self._closed = True

    def _render(self) -> RawFrame:
        w, h = self.width, self.height
        data = bytearray(w * h * 3)
        # Dark background.
        for i in range(0, len(data), 3):
            data[i] = 12
            data[i + 1] = 14
            data[i + 2] = 26
        # Accent band across the lower third.
        band_top = h * 2 // 3
        for y in range(band_top, h):
            base = y * w * 3
            for x in range(w):
                i = base + x * 3
                data[i] = 0
                data[i + 1] = 180
                data[i + 2] = 255
        # Bright square near the top-left.
        for y in range(60, 140):
            for x in range(60, 400):
                i = (y * w + x) * 3
                data[i] = 255
                data[i + 1] = 80
                data[i + 2] = 60
        return RawFrame(data=bytes(data), width=w, height=h)


class ProbeModule(BaseModule, key="probe"):
    """Becomes the panel background with a test pattern -- cycle validation."""

    title = "Probe"
    summary = "Phase 0 cycle probe: module FrameSource -> background.show"
    default_on = False

    def _enable(self, host) -> None:
        keys = host.panels.attached()
        if not keys:
            log.warning("probe: no panel attached; nothing to draw")
            return
        key = keys[0]
        size = host.panels.size(key) or (1920, 440)
        self._key = key
        self._source = _Pattern(*size)
        host.background.show(key, self._source, fps=15.0)
        log.info("probe: showing pattern on %s (%dx%d) at 15 fps", key, *size)

    def _disable(self, host) -> None:
        key = getattr(self, "_key", None)
        if key is not None:
            host.background.end(key)
            log.info("probe: ended %s -> theme restored", key)
