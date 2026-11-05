"""System metric collection (best-effort, via psutil)."""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import psutil


class PsutilSensorSource:
    """``SensorSource`` adapter over psutil (best-effort, no hardware needed)."""

    def collect(self) -> dict:
        return collect()


def collect() -> dict:
    """Return a snapshot of displayed metrics. Missing values are None."""
    m: dict = {}
    try:
        m["cpu"] = psutil.cpu_percent(interval=None) or 0.0
    except Exception:
        m["cpu"] = 0.0
    try:
        m["ram"] = psutil.virtual_memory().percent
    except Exception:
        m["ram"] = 0.0
    try:
        m["swap"] = psutil.swap_memory().percent
    except Exception:
        m["swap"] = 0.0
    try:
        m["disk"] = psutil.disk_usage("/").percent
    except Exception:
        m["disk"] = 0.0
    try:
        m["net"] = _net_rate()
    except Exception:
        m["net"] = 0.0
    try:  # best-effort CPU package temp; varies by platform
        t = psutil.sensors_temperatures()
        for name in ("coretemp", "k10temp", "cpu_thermal", "thermal_zone"):
            if name in t:
                m["temp"] = t[name][0].current
                break
    except Exception:
        pass
    return m


_last_net: tuple[int, int] | None = None


def _net_rate() -> float:
    global _last_net
    io = psutil.net_io_counters()
    if _last_net is None:
        _last_net = (io.bytes_recv, io.bytes_sent)
        return 0.0
    br, bs = io.bytes_recv, io.bytes_sent
    r, s = _last_net
    _last_net = (br, bs)
    return float((br - r) + (bs - s))  # bytes since last poll
