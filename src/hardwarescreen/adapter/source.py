"""Dashboard ``FrameSource``: render the latest bridge state as a raw RGB frame.

The trcc ``SourceDriver`` calls ``latest()`` every ``1/fps`` and hands the frame
to the panel; trcc composites and encodes it. We keep only the newest composite
(no queue) and return raw RGB bytes sized ``w*h*3``.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

from PIL import Image

from .dashboard import DEFAULT_SIZE, compose_dashboard
from .poller import BridgePoller


class DashboardSource:
    def __init__(
        self,
        poller: BridgePoller,
        size: tuple[int, int] = DEFAULT_SIZE,
        background: Image.Image | None = None,
    ):
        self._poller = poller
        self._w, self._h = size
        self._background = background
        self._state: dict | None = None
        self._frame: bytes | None = None
        self._closed = False

    def latest(self) -> bytes:
        """Return the newest raw RGB frame (``w*h*3`` bytes).

        Renders from the latest bridge state and keeps it as the newest composite
        across calls; only a newer ``seq`` (from the poller) replaces it. Before any
        state is published it renders an empty ("no data") frame, so the trcc
        ``SourceDriver`` always gets a frame (never a "not ready" hole).
        """
        state = self._poller.poll()
        if state is not None:
            self._state = state
        st = self._state or {}
        img = compose_dashboard(
            st.get("setup") or [],
            st.get("values") or {},
            st.get("palette") or {},
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
