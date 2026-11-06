"""Dashboard ``FrameSource``: render the latest bridge state as a raw RGB frame.

The trcc ``SourceDriver`` calls ``latest()`` every ``1/fps`` and hands the frame
to the panel; trcc composites and encodes it. We keep only the newest composite
(no queue) and return raw RGB bytes sized ``w*h*3``.

Rendering is delegated to ``frame_renderer`` when given (a stateful callable with the
signature ``render(setup, values, palette, size, background) -> bytes``, e.g. the Qt
``LinearDashboardRenderer``); otherwise it falls back to the Pillow
``compose_dashboard``. The default keeps this module Qt-free.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import time
from collections.abc import Callable

from PIL import Image

from .dashboard import DEFAULT_SIZE, compose_dashboard
from .poller import BridgePoller


class DashboardSource:
    def __init__(
        self,
        poller: BridgePoller,
        size: tuple[int, int] = DEFAULT_SIZE,
        background: Image.Image | None = None,
        frame_renderer: Callable | None = None,
        fps: float = 8.0,
    ):
        self._poller = poller
        self._w, self._h = size
        self._background = background
        self._renderer = frame_renderer
        self._state: dict | None = None
        self._frame: bytes | None = None
        self._last_render_at: float | None = None
        self._last_sig: object | None = None
        self._changed = False
        self._frame_interval = 1.0 / max(1.0, fps)
        self._closed = False

    @staticmethod
    def _content_sig(state: dict) -> object:
        """A hashable fingerprint of everything the renderer displays."""
        return (
            tuple((g.get("id"), g.get("label"), g.get("unit"),
                   g.get("min"), g.get("max")) for g in (state.get("setup") or [])),
            tuple(sorted((state.get("values") or {}).items())),
            tuple(sorted((state.get("palette") or {}).items())),
        )

    def latest(self) -> bytes:
        """Return the newest raw RGB frame (``w*h*3`` bytes).

        Drives the renderer's eased animation at a capped, panel-safe cadence
        (``fps``) so the rings-style highlight keeps moving even while the sensor
        values are quiet (the panel would otherwise freeze on one frame).  A
        frame is also re-rendered immediately when the displayed content
        (setup/values/palette) actually changes, so fresh data reaches the panel
        without waiting for the next cadence tick.  Between those, the cached
        frame is returned unchanged — identical bytes are cheap for the USB
        transport (keepalive), and unlike a wall-clock animation they never
        overload the panel's ~18 fps budget into dropped-frame black flashes.

        If the store stops advancing ``seq`` (e.g. the producer pauses), the last
        known state is held so the dashboard keeps showing real values rather than
        collapsing to a "no data" frame.
        """
        state = self._poller.poll() or self._poller.last
        now = time.monotonic()
        if state is not None:
            sig = self._content_sig(state)
            if sig != self._last_sig:
                self._state = state
                self._last_sig = sig
                self._changed = True
        due = (
            self._frame is None
            or self._changed
            or self._last_render_at is None
            or (now - self._last_render_at) >= self._frame_interval
        )
        if due:
            self._changed = False
            st = self._state or {}
            setup = st.get("setup") or []
            values = st.get("values") or {}
            palette = st.get("palette") or {}
            if self._renderer is not None:
                self._frame = self._renderer(
                    setup, values, palette, (self._w, self._h), self._background
                )
            else:
                img = compose_dashboard(
                    setup,
                    values,
                    palette,
                    (self._w, self._h),
                    self._background,
                )
                self._frame = img.tobytes()
            self._last_render_at = now
        assert self._frame is not None
        return self._frame

    def close(self) -> None:
        self._closed = True

    @property
    def size(self) -> tuple[int, int]:
        return (self._w, self._h)
