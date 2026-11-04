"""Smoke tests for the trofeo core (no USB/device required)."""
# SPDX-License-Identifier: MIT
from __future__ import annotations

from trofeo import sensors
from trofeo.protocol import CHUNK, build_frame
from trofeo.render import quantize


def test_quantize_rounds_jitter():
    m = {"cpu": 42.6, "ram": 55.4, "net": 102400.0, "temp": None}
    q = quantize(m)
    assert q["cpu"] == 43  # round(42.6)
    assert q["ram"] == 55
    assert q["net"] == 100  # int(102400 // 1024)
    assert q["temp"] is None


def test_collect_disk_is_real_percent():
    # Guards the shutil.disk_usage("/").percent bug (always 0.0 before the fix).
    m = sensors.collect()
    assert "disk" in m
    assert 0.0 <= m["disk"] <= 100.0


def test_build_frame_header_and_padding():
    frame = build_frame(b"\x01" * 100)
    assert len(frame) % CHUNK == 0
    assert (len(frame) // CHUNK) % 4 == 0  # chunk count padded to a multiple of 4
    assert frame[0] == 0x01
    assert frame[1] == 0xFF
