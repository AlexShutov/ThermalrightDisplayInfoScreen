"""Phase 3: theme-manager dashboard adapter module (GPL-3.0, imports trcc).

Reads the manager's atomic bridge state file (``HARDWARESCREEN_STATE_FILE``),
publishes gauges to the panel as a background ``FrameSource``, and defers
``background.show`` until a panel is attached (the Phase 0 attach-race guard).
The pure logic (poller/layout/source/driver) lives in the MIT ``hardwarescreen``
package; this module only wires it to ``host.background`` / ``host.panels``.
"""
# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import logging
import os
import threading
import time

from trcc.adapters.modules._base import BaseModule
from trcc.core.models import RawFrame
from trcc.core.ports import FrameSource

from hardwarescreen.adapter.driver import AdapterDriver
from hardwarescreen.adapter.poller import BridgePoller
from hardwarescreen.adapter.source import DashboardSource
from manager.store import AtomicStateFile

log = logging.getLogger("hardwarescreen.dashboard")

_KEY = "dashboard"


class _BridgeFrameSource(FrameSource):
    """Adapts our raw-RGB DashboardSource into a trcc ``RawFrame``."""

    name = "dashboard.source"

    def __init__(self, source: DashboardSource) -> None:
        self._src = source

    def latest(self) -> RawFrame:
        w, h = self._src.size
        return RawFrame(data=self._src.latest(), width=w, height=h)

    def close(self) -> None:
        self._src.close()


class DashboardModule(BaseModule, key=_KEY):
    """Bridge state -> dashboard gauges on the panel background."""

    title = "Dashboard"
    summary = "Theme-manager dashboard adapter"
    default_on = False

    def _enable(self, host) -> None:
        self._host = host
        state_file = os.environ.get("HARDWARESCREEN_STATE_FILE", "manager_state.json")
        keys = host.panels.attached()
        size = host.panels.size(keys[0]) if keys else (1920, 440)
        self._poller = BridgePoller(AtomicStateFile(state_file))
        self._source = DashboardSource(self._poller, size)
        self._frame_source = _BridgeFrameSource(self._source)
        self._driver = AdapterDriver(host.background, self._frame_source, key=_KEY)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info("dashboard: enabled (state_file=%s, size=%s)", state_file, (size,))

    def _loop(self) -> None:
        while not self._stop.is_set():
            # Keep the newest state across quiet ticks so a now-changed seq is not
            # mistaken for "no data" (which would end the dashboard every poll).
            state = self._poller.poll() or self._poller.last
            attached = bool(self._host.panels.attached())
            self._driver.update(state, attached)
            time.sleep(0.5)

    def _disable(self, host) -> None:
        self._stop.set()
        host.background.end(_KEY)
        log.info("dashboard: ended %s -> theme restored", _KEY)