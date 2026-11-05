"""Reactive renderer: dirty-check + keepalive + frame budget + reconnect.

- Renders/sends only when a *meaningfully* displayed value changes (quantized
  dirty-check), or when the keepalive timer is due (the panel reverts to its
  logo ~2-3 s after the last frame).
- Always encodes the JPEG within `max_frame_bytes` (quality/scale shrink loop).
- On a send failure (USB drop) it reconnects with backoff and resumes.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import contextlib
import logging
import signal
import time

from PIL import Image

from . import sensors
from .canvas import compose
from .config import Config
from .imageutil import encode_within_budget
from .ly import LyDisplay
from .ports import FrameSink, SensorSource
from .sensors import PsutilSensorSource

LOG = logging.getLogger("hardwarescreen.render")


def should_render(
    *,
    metrics: dict,
    last: dict | None,
    last_sent: float,
    now: float,
    keepalive_s: float,
    active_fps: float,
    last_jpeg: bytes | None,
) -> bool:
    """Decide whether a new frame must be rendered+sent (dirty or keepalive gate).

    Pure decision so the reactive render loop is testable without a device.
    """
    changed = metrics != last
    keepalive = (now - last_sent) >= keepalive_s
    min_interval = 1.0 / max(active_fps, 1.0)
    return (changed or keepalive or last_jpeg is None) and (now - last_sent) >= min_interval


def quantize(metrics: dict) -> dict:
    """Round values so tiny sensor jitter does not trigger a resend."""
    out: dict[str, object] = {}
    for k, v in metrics.items():
        if v is None:
            out[k] = None
        elif k == "net":
            out[k] = int(v // 1024)  # 1 KiB bins
        elif isinstance(v, (int, float)):
            out[k] = round(float(v))
        else:
            out[k] = v
    return out


class Renderer:
    def __init__(
        self,
        cfg: Config,
        sink: FrameSink | None = None,
        source: SensorSource | None = None,
    ):
        self.cfg = cfg
        self.sink: FrameSink = sink or LyDisplay()
        self.source: SensorSource = source or PsutilSensorSource()
        self._last: dict | None = None
        self._last_jpeg: bytes | None = None
        self._last_sent = 0.0
        self._stop = False

    # -- main loop ------------------------------------------------------
    def run(self) -> None:
        self._connect()
        poll = self.cfg.sensor_poll_s
        while not self._stop:
            metrics = quantize(self.source.collect())
            now = time.monotonic()
            if should_render(
                metrics=metrics,
                last=self._last,
                last_sent=self._last_sent,
                now=now,
                keepalive_s=self.cfg.keepalive_s,
                active_fps=self.cfg.active_fps,
                last_jpeg=self._last_jpeg,
            ):
                self._render_and_send(metrics)
                self._last = metrics
            time.sleep(poll)

    # -- render + send --------------------------------------------------
    def _render_and_send(self, metrics: dict) -> None:
        img = compose(metrics, self.cfg)
        jpeg = self._encode_within_budget(img)
        try:
            self.sink.send(jpeg)
            self._last_jpeg = jpeg
            self._last_sent = time.monotonic()
        except Exception as e:
            LOG.warning("send failed (%s); reconnecting", e)
            self._connect()

    def _encode_within_budget(self, img: Image.Image) -> bytes:
        """JPEG-encode, shrinking quality/scale until within the frame budget."""
        return encode_within_budget(img, self.cfg.max_frame_bytes)

    # -- connection -----------------------------------------------------
    def _connect(self) -> None:
        while not self._stop:
            try:
                self.sink.close()
                info = self.sink.connect(retries=20, delay_s=self.cfg.sensor_poll_s)
                LOG.info("connected: PM=%d SUB=%d size=%dx%d", info["pm"], info["sub"],
                         info["width"], info["height"])
                self.cfg.width = info["width"]
                self.cfg.height = info["height"]
                return
            except Exception as e:
                LOG.warning("reconnect failed: %s; retrying", e)
                time.sleep(self.cfg.keepalive_s)

    def stop(self, *_) -> None:
        self._stop = True


def preview(argv=None) -> int:
    """Compose a dashboard frame and save it — no USB, no device needed."""
    import argparse
    ap = argparse.ArgumentParser(prog="hardwarescreen preview")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out", default="preview.png")
    args = ap.parse_args(argv)
    cfg = Config.load(args.config)
    m = sensors.collect()
    img = compose(m, cfg)
    img.save(args.out)
    print(f"saved {args.out} {img.width}x{img.height}")
    return 0


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="hardwarescreen run")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--max-frame-bytes", type=int, default=None)
    ap.add_argument("--keepalive", type=float, default=None)
    ap.add_argument("--log", default="INFO")
    args = ap.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    cfg = Config.load(args.config)
    if args.max_frame_bytes:
        cfg.max_frame_bytes = args.max_frame_bytes
    if args.keepalive:
        cfg.keepalive_s = args.keepalive
    r = Renderer(cfg)
    signal.signal(signal.SIGINT, r.stop)
    signal.signal(signal.SIGTERM, r.stop)
    with contextlib.suppress(KeyboardInterrupt):
        r.run()
    return 0
