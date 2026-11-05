"""Hexagonal ports at the system boundaries.

Pure-Python protocols (duck-typed) so the core is testable behind fakes with no
USB, Qt, trcc or FastAPI required. The decision/render core imports only these
ports; composition roots (e.g. ``render.Renderer``) wire the real adapters as
constructor defaults. ``TrccBackground`` and ``StateStore`` are consumed from
Phase 1/3 (manager + Qt adapter) and are defined here so the whole foundation is
in one place.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class FrameSink(Protocol):
    """Writes one rendered frame to the panel. Real: ``ly.LyDisplay``."""

    def connect(self, retries: int = 5, delay_s: float = 1.0) -> dict:
        """Open + handshake; return the device info dict."""
        ...

    def send(self, payload: bytes) -> bytes:
        """Send one raw frame payload; return the ACK bytes."""
        ...

    def close(self) -> None:
        """Release the device for reconnect."""
        ...


@runtime_checkable
class SensorSource(Protocol):
    """Provides a metric snapshot each poll. Real: ``sensors.PsutilSensorSource``."""

    def collect(self) -> dict:
        """Return a display-metric snapshot (missing values are None/0)."""
        ...


@runtime_checkable
class TrccBackground(Protocol):
    """Controls the trcc ``background`` (theme video / module source) lifecycle."""

    def show(self, key: str, source: object, fps: float) -> None:
        """Make ``source`` the panel's background (stops the theme video first)."""
        ...

    def end(self, key: str) -> None:
        """Stop the module source and resume the theme's own video."""
        ...


@runtime_checkable
class StateStore(Protocol):
    """Durable bridge state (atomic file manager -> module, seq-guarded)."""

    def write(self, state: dict) -> int:
        """Persist ``state`` with a monotonic seq; return the new seq."""
        ...

    def read(self) -> dict | None:
        """Return the last persisted state, or None if never written."""
        ...

    def close(self) -> None:
        """Flush/close any resources."""
        ...
