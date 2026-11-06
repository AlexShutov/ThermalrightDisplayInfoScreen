"""Gauge layout + dashboard frame composition from bridge state (pure, Pillow).

Renders gauges for a 1920x440 canvas from ``setup`` (list of dicts with
id/label/unit/min/max), ``values`` (id -> number) and ``palette`` (color names),
returning a raw RGB PIL image. The trcc module (GPL) hands this to the panel as a
``RawFrame``; trcc composites it (with the theme mask) and encodes to JPEG.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw, ImageOps

DEFAULT_SIZE = (1920, 440)


@dataclass(frozen=True)
class GaugeSpec:
    id: str
    label: str
    unit: str
    x: int
    y: int
    w: int
    h: int
    text: str
    pct: float
    color: tuple[int, int, int]


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = (value or "").lstrip("#")
    if len(v) != 6:
        return (80, 80, 90)
    try:
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    except ValueError:
        return (80, 80, 90)


def _palette(palette: dict) -> dict[str, tuple[int, int, int]]:
    p = palette or {}
    return {
        "background": hex_to_rgb(p.get("background", "#111318")),
        "panel": hex_to_rgb(p.get("panel", "#1b1e27")),
        "accent": hex_to_rgb(p.get("accent", "#4f9cf9")),
        "text": hex_to_rgb(p.get("text", "#eef2f8")),
        "muted": hex_to_rgb(p.get("muted", "#8a93a6")),
    }


def _labelled_value(values: dict, gauge: dict) -> tuple[float, str]:
    gid = gauge.get("id", "")
    vmin = float(gauge.get("min", 0))
    vmax = float(gauge.get("max", 100))
    unit = gauge.get("unit", "")
    raw = values.get(gid)
    if not isinstance(raw, (int, float)):
        return 0.0, "n/a"
    denom = (vmax - vmin) if vmax > vmin else 0
    pct = ((raw - vmin) / denom * 100.0) if denom else 0.0
    return max(0.0, min(100.0, pct)), f"{raw:.0f}{unit}"


def gauge_layout(
    setup: Iterable[dict],
    values: dict,
    palette: dict,
    size: tuple[int, int] = DEFAULT_SIZE,
    *,
    top: int = 96,
    gap: int = 16,
    margin: int = 40,
    footer: int = 40,
) -> tuple[dict, list[GaugeSpec]]:
    """Compute per-gauge draw rects + percent + displayed text (pure, testable)."""
    w, h = size
    c = _palette(palette)
    gauges = list(setup)
    n = len(gauges)
    bar_h = max(0, (h - top - footer - gap * (n - 1)) // n) if n else 0
    out: list[GaugeSpec] = []
    y = top
    for g in gauges:
        gid = g.get("id", "")
        pct, text = _labelled_value(values, g)
        out.append(
            GaugeSpec(
                id=gid,
                label=g.get("label", gid),
                unit=g.get("unit", ""),
                x=margin,
                y=y,
                w=w - 2 * margin,
                h=bar_h,
                text=text,
                pct=pct,
                color=c["accent"],
            )
        )
        y += bar_h + gap
    return c, out


def compose_dashboard(
    setup: Iterable[dict],
    values: dict[str, Any],
    palette: dict,
    size: tuple[int, int] = DEFAULT_SIZE,
    background: Image.Image | None = None,
) -> Image.Image:
    """Compose gauges over *background* (a theme frame) when given, else a solid
    palette fill. *background* is fit cover to *size* so the theme photo/video
    frame shows through behind the gauges (the overlay composite)."""
    w, h = size
    c, specs = gauge_layout(setup, values, palette, size)
    img = Image.new("RGB", size, c["background"])
    if background is not None:
        bg = background.convert("RGB")
        if bg.size != size:
            bg = ImageOps.fit(bg, size)
        img = bg.copy()
    d = ImageDraw.Draw(img)
    if not specs:
        _text(d, "no data", w // 2 - 60, h // 2 - 16, size=32, fill=c["muted"])
        return img
    for s in specs:
        if s.h >= 6:
            d.rounded_rectangle(
                [s.x, s.y, s.x + s.w, s.y + s.h], radius=12, outline=c["panel"], width=3
            )
            fill_w = int((s.w - 6) * s.pct / 100.0)
            if fill_w > 0 and s.h - 3 >= 3:
                d.rounded_rectangle(
                    [s.x + 3, s.y + 3, s.x + 3 + fill_w, s.y + s.h - 3],
                    radius=10,
                    fill=s.color,
                )
        _text(d, s.label, s.x + 12, s.y + (s.h - 22) // 2, size=26, fill=c["text"])
        _text(d, s.text, s.x + s.w - 160, s.y + (s.h - 22) // 2, size=26, fill=c["text"])
    return img


def _text(d: ImageDraw.ImageDraw, s: str, x: int, y: int, size: int = 28,
          fill: tuple[int, int, int] = (255, 255, 255)) -> None:
    try:
        from PIL import ImageFont

        font = ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:
        font = None
    d.text((x, y), s, fill=fill, font=font)
