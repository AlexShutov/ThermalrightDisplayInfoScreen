"""Reactive renderer: dirty-check + keepalive + frame budget + reconnect.

- Renders/sends only when a *meaningfully* displayed value changes (quantized
  dirty-check), or when the keepalive timer is due (the panel reverts to its
  logo ~2-3 s after the last frame).
- Always encodes the JPEG within `max_frame_bytes` (quality/scale shrink loop).
- On a send failure (USB drop) it reconnects with backoff and resumes.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import io
import logging
import signal
import sys
import time

from PIL import Image

from . import sensors
from .canvas import compose
from .config import Config
from .ly import LyDisplay
from .protocol import MAX_FRAME_BYTES, parse_handshake

LOG = logging.getLogger("trofeo.render")

QUALITY_LADDER = (85, 75, 65, 55, 45)


def quantize(metrics: dict) -> dict:
    """Round values so tiny sensor jitter does not trigger a resend."""
    out = {}
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
    def __init__(self, cfg: Config, ly: LyDisplay | None = None):
        self.cfg = cfg
        self.ly = ly or LyDisplay()
        self._last = None
        self._last_jpeg: bytes | None = None
        self._last_sent = 0.0
        self._stop = False

    # -- main loop ------------------------------------------------------
    def run(self) -> None:
        self._connect()
        poll = self.cfg.sensor_poll_s
        while not self._stop:
            metrics = quantize(sensors.collect())
            now = time.monotonic()
            changed = metrics != self._last
            keepalive = (now - self._last_sent) >= self.cfg.keepalive_s
            min_interval = 1.0 / max(self.cfg.active_fps, 1.0)
            if (changed or keepalive or self._last_jpeg is None) and (now - self._last_sent) >= min_interval:
                self._render_and_send(metrics)
                self._last = metrics
            time.sleep(poll)

    # -- render + send --------------------------------------------------
    def _render_and_send(self, metrics: dict) -> None:
        img = compose(metrics, self.cfg)
        jpeg = self._encode_within_budget(img)
        try:
            self.ly.send(jpeg)
            self._last_jpeg = jpeg
            self._last_sent = time.monotonic()
        except Exception as e:
            LOG.warning("send failed (%s); reconnecting", e)
            self._connect()

    def _encode_within_budget(self, img: Image.Image) -> bytes:
        """JPEG-encode, shrinking quality/scale until within the frame budget."""
        budget = self.cfg.max_frame_bytes or MAX_FRAME_BYTES
        for q in QUALITY_LADDER:
            jpeg = _jpeg(img, q)
            if len(jpeg) <= budget:
                return jpeg
        scale = img
        for _ in range(8):  # fall back to downscaling
            scale = scale.resize((int(scale.width * 0.85), int(scale.height * 0.85)))
            for q in (70, 60, 50):
                jpeg = _jpeg(scale, q)
                if len(jpeg) <= budget:
                    return jpeg
        raise RuntimeError("could not encode within frame budget")

    # -- connection -----------------------------------------------------
    def _connect(self) -> None:
        while not self._stop:
            try:
                self.ly.close()
                info = self.ly.connect(retries=20, delay_s=self.cfg.sensor_poll_s)
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
    ap = argparse.ArgumentParser(prog="trofeo preview")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out", default="preview.png")
    args = ap.parse_args(argv)
    cfg = Config.load(args.config)
    m = sensors.collect()
    img = compose(m, cfg)
    img.save(args.out)
    print(f"saved {args.out} {img.width}x{img.height}")
    return 0


def _jpeg(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="trofeo run")
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
    try:
        r.run()
    except KeyboardInterrupt:
        pass
    return 0