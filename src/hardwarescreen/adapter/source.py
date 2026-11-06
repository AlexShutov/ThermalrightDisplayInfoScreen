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
        transition_s: float = 0.6,
    ):
        self._poller = poller
        self._w, self._h = size
        self._background = background
        self._renderer = frame_renderer
        self._transition_s = transition_s
        self._state: dict | None = None
        self._frame: bytes | None = None
        self._anim_due_until: float | None = None
        self._last_sig: object | None = None
        self._changed = False
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

        REACTIVE: the frame is re-rendered only when the displayed content
        (setup/values/palette) actually changed, while the renderer's eased
        transition is still settling, or before any frame exists.  Between those
        the cached frame is returned unchanged, so the ``SourceDriver`` (which
        samples every tick) keeps re-sending identical bytes instead of a
        churning frame — that holds the panel alive (keepalive) without flooding
        its ~18 fps USB budget.  A ``seq`` bump with unchanged content is ignored,
        so the dashboard never churns on a quiet heartbeat.
        """
        state = self._poller.poll()
        now = time.monotonic()
        if state is not None:
            sig = self._content_sig(state)
            if sig != self._last_sig:
                self._state = state
                self._last_sig = sig
                self._anim_due_until = now + self._transition_s
                self._changed = True
        settling = self._anim_due_until is not None and now < self._anim_due_until
        if self._frame is None or self._changed or settling:
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
        return self._frame

    def close(self) -> None:
        self._closed = True

    @property
    def size(self) -> tuple[int, int]:
        return (self._w, self._h)
