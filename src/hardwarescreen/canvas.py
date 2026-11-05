"""Compose the dashboard frame (Pillow)."""
# SPDX-License-Identifier: MIT
from __future__ import annotations

from PIL import Image, ImageDraw

from .config import Config


def compose(metrics: dict, cfg: Config) -> Image.Image:
    w, h = cfg.width, cfg.height
    img = Image.new("RGB", (w, h), (12, 14, 26))
    d = ImageDraw.Draw(img)

    _text(d, "TROFEO 11.3", 40, 24, size=36, fill=(90, 200, 255))

    top = 96
    rows = [
        ("cpu", "CPU"),
        ("ram", "RAM"),
        ("disk", "DISK"),
    ]
    gap = 20
    bar_h = (h - top - gap * (len(rows) - 1) - 60) // len(rows)
    colors = [(0, 180, 255), (60, 220, 120), (255, 180, 60)]
    y = top
    for (key, label), color in zip(rows, colors, strict=False):
        _bar(d, 40, y, w - 80, bar_h, _pct(metrics, key), color, label)
        y += bar_h + gap

    y = h - 44
    _text(d, _fmt(metrics.get("temp"), "temp", "{:.0f}C "), 40, y, size=28, fill=(255, 255, 255))
    _text(d, _fmt(metrics.get("net"), "net", "{:.0f}B/s"), w - 560, y, size=28,
      fill=(180, 180, 200))

    for i in range(len(cfg.sensors)):
        color = ((i * 60) % 256, (i * 90) % 256, (i * 120) % 256)
        d.rectangle([w - 60 - i * 0, h - 40, w - 40 - i * 0, h - 20], fill=color)
    return img


def _bar(d, x, y, w, h, pct, color, label):
    pct = max(0.0, min(100.0, pct))
    d.rounded_rectangle([x, y, x + w, y + h], radius=12, outline=(40, 44, 66), width=3)
    fill_w = int((w - 6) * pct / 100.0)
    if fill_w > 0:
        d.rounded_rectangle([x + 3, y + 3, x + 3 + fill_w, y + h - 3], radius=10, fill=color)
    _text(d, label, x + 12, y + (h - 22) // 2, size=26, fill=(255, 255, 255))
    _text(d, f"{pct:.0f}%", x + w - 120, y + (h - 22) // 2, size=26, fill=(255, 255, 255))


def _pct(metrics, key):
    try:
        return float(metrics.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def _fmt(value, name, fmt):
    if value is None:
        return f"{name}: n/a"
    try:
        return fmt.format(float(value))
    except (TypeError, ValueError):
        return f"{name}: n/a"


def _text(d, s, x, y, size=28, fill=(255, 255, 255)):
    try:
        from PIL import ImageFont
        font = ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:
        font = None
    d.text((x, y), s, fill=fill, font=font)
