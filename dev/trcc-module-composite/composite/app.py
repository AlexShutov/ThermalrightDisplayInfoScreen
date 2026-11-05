"""Phase 0 composite probe: theme video (ffmpeg) as a background base + Qt overlay.

A FrameSource that decodes the active theme's video (the manager's ``background``
layer) to RGB24 via ffmpeg, then composites a dashboard overlay on top and hands
the result to ``host.background.show``. This is the active composite the
theme-manager + Qt adapter will rely on (variant 1): the theme keeps playing as
the base, the dashboard draws over it, all in one frame sent to the panel.

Overlay / composite split (the "obvious" design):
  * The overlay is rendered ONCE at enable into a persistent RGBA buffer
    (``h,w,4``). The real Qt adapter paints its widgets into exactly this buffer
    via Qt; here it is a numpy rectangle stand-in.
  * Each tick ``latest()`` just composites the already-decoded theme frame with
    the fixed overlay buffer using numpy on reused buffers -- no Qt, no fresh
    2.4 MB copies per tick, no hot-path cost. Translucency is constant, so the
    overlay never "becomes opaque" with a changing base.

The video path is a stand-in for the manager's bridge state: the real adapter
gets the active theme path (and palette) from the manager's atomic state file.
Here it defaults to ``$HARDWARESCREEN_THEME_VIDEO`` or the d029 night theme.

GPL-3.0 -- imports trcc internals (like the probe). Never copied into MIT sources.
"""

# SPDX-License-Identifier: GPL-3.0-only
import contextlib
import logging
import os
import shutil
import subprocess
import time

import numpy as np
from trcc.adapters.modules._base import BaseModule
from trcc.core.models import RawFrame
from trcc.core.ports import FrameSource

log = logging.getLogger("hardwarescreen.composite")

_DEFAULT_THEME_VIDEO = os.path.expanduser(
    "~/Library/Application Support/trcc/data/web/1920440/d029.mp4"
)


def _read_exact(stream, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _overlay_buffer(w: int, h: int) -> np.ndarray:
    """Build the dashboard overlay as straight-RGBA (reused every tick)."""
    ovr = np.zeros((h, w, 4), dtype=np.uint8)  # fully transparent -> theme shows
    pw = int(w * 0.28)
    px = w - pw
    # Right-side glass panel (a subtle translucent pane over the theme).
    ovr[:, px:, :3] = (8, 10, 18)
    ovr[:, px:, 3] = 110
    # Accent header strip along the panel's top.
    bar = max(24, int(h * 0.14))
    ovr[0:bar, px:, :3] = (0, 180, 255)
    ovr[0:bar, px:, 3] = 210
    # Gauge placeholder blocks (three rows) with an accent fill.
    gh = max(24, int(h * 0.16))
    gap = max(8, int(h * 0.05))
    pad = max(12, pw // 12)
    gx, gw = px + pad, pw - 2 * pad
    for i in range(3):
        top = int(h * 0.24) + i * (gh + gap)
        ovr[top : top + gh, gx : gx + gw, :3] = (240, 244, 250)
        ovr[top : top + gh, gx : gx + gw, 3] = 70
        accent = (40, 220, 90) if i == 0 else (120, 160, 30)
        bx = gx + int(gw * 0.55)
        bw = int(gw * 0.4)
        by = top + gh // 4
        ovr[by : by + gh // 2, bx : bx + bw, :3] = accent
        ovr[by : by + gh // 2, bx : bx + bw, 3] = 200
    # Top-left status chip.
    ch_x, ch_y = 24, 20
    ch_w = int(w * 0.16)
    ch_h = max(16, int(h * 0.10))
    ovr[ch_y : ch_y + ch_h, ch_x : ch_x + ch_w, :3] = (0, 180, 255)
    ovr[ch_y : ch_y + ch_h, ch_x : ch_x + ch_w, 3] = 200
    return ovr


class _ThemeVideo(FrameSource):
    """Decode the theme once; each tick composite the fixed overlay over it."""

    name = "composite.theme_video"

    def __init__(self, video: str, width: int, height: int, fps: float = 15.0) -> None:
        self._video = video
        self._w, self._h = width, height
        self._fps = fps
        self._frame_size = width * height * 3
        self._closed = False
        self._frames: list[bytes] = []
        self._start = time.monotonic()
        self._decode(video, width, height)

        overlay = _overlay_buffer(width, height)
        self._alpha = overlay[:, :, 3:4]                       # (h,w,1) uint8
        self._inv_a = (255 - self._alpha).astype(np.int32)     # (h,w,1) int32
        self._ovr_a = overlay[:, :, :3].astype(np.int32) * self._alpha  # rgb*a, int32
        self._t32 = np.zeros((height, width, 3), np.int32)     # theme*(255-a)
        self._out = np.zeros((height, width, 3), np.uint8)     # final RGB24

        log.info(
            "composite: decoded %s -> %d frame(s) %dx%d rgb24",
            video, len(self._frames), width, height,
        )

    def _decode(self, video: str, width: int, height: int) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            log.warning("composite: ffmpeg not found; falling back to a flat base")
            return
        cmd = [
            ffmpeg, "-i", video,
            "-vf", f"scale={width}:{height},setsar=1,fps={self._fps:g}",
            "-an", "-sn", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError as exc:
            log.warning("composite: failed to start ffmpeg (%s)", exc)
            return
        try:
            assert proc.stdout is not None
            while True:
                frame = _read_exact(proc.stdout, self._frame_size)
                if frame is None:
                    break
                self._frames.append(frame)
        finally:
            with contextlib.suppress(Exception):
                proc.stdout.close()
            proc.wait()

    def latest(self) -> RawFrame:
        if not self._frames:
            base = np.zeros((self._h, self._w, 3), np.uint8)
        else:
            idx = int((time.monotonic() - self._start) * self._fps) % len(self._frames)
            base = np.frombuffer(self._frames[idx], np.uint8).reshape(self._h, self._w, 3)
        # out = (theme*(255-a) + overlay*a + 127) >> 8, clamped, in place.
        np.multiply(base, self._inv_a, out=self._t32)
        np.add(self._t32, self._ovr_a, out=self._t32)
        np.add(self._t32, 127, out=self._t32)
        np.right_shift(self._t32, 8, out=self._t32)
        np.clip(self._t32, 0, 255, out=self._t32)
        np.copyto(self._out, self._t32, casting="unsafe")
        return RawFrame(data=self._out.tobytes(), width=self._w, height=self._h)

    def close(self) -> None:
        self._closed = True


class CompositeModule(BaseModule, key="composite"):
    """Theme video as the background base, with a Qt overlay baked into the frame."""

    title = "Composite"
    summary = "Theme video (ffmpeg) base + Qt overlay via FrameSource"
    default_on = False

    def _enable(self, host) -> None:
        keys = host.panels.attached()
        if not keys:
            log.warning("composite: no panel attached; nothing to draw")
            return
        key = keys[0]
        size = host.panels.size(key) or (1920, 440)
        video = os.environ.get("HARDWARESCREEN_THEME_VIDEO", _DEFAULT_THEME_VIDEO)
        self._key = key
        self._source = _ThemeVideo(video, *size)
        host.background.show(key, self._source, fps=15.0)
        log.info("composite: showing theme+overlay on %s (%dx%d) at 15 fps", key, *size)

    def _disable(self, host) -> None:
        key = getattr(self, "_key", None)
        if key is not None:
            host.background.end(key)
            log.info("composite: ended %s -> theme restored", key)
