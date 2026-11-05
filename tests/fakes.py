"""Fakes implementing the hardwarescreen ports (no USB / psutil / trcc / filesystem).

Used by the pytest suite so every critical behavior is verified without hardware.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

from hardwarescreen.protocol import MAX_FRAME_BYTES


class MockPanel:
    """``FrameSink`` fake: records every frame and enforces the frame budget.

    A budget violation raises so tests catch an oversized frame immediately
    (the encode path should shrink to fit before it reaches the sink).
    """

    def __init__(
        self,
        budget: int = MAX_FRAME_BYTES,
        handshake: dict | None = None,
    ):
        self.budget = budget
        self._handshake = handshake or {
            "pm": 69,
            "sub": 1,
            "width": 1920,
            "height": 440,
        }
        self.frames: list[bytes] = []
        self.connect_calls = 0
        self.close_calls = 0

    # -- FrameSink -----------------------------------------------------
    def connect(self, retries: int = 5, delay_s: float = 1.0) -> dict:
        self.connect_calls += 1
        return dict(self._handshake)

    def send(self, payload: bytes) -> bytes:
        if len(payload) > self.budget:
            raise AssertionError(f"frame {len(payload)} B exceeds budget {self.budget} B")
        self.frames.append(bytes(payload))
        return b"\x00" * 16  # fake ACK

    def close(self) -> None:
        self.close_calls += 1

    # -- helpers -------------------------------------------------------
    @property
    def send_count(self) -> int:
        return len(self.frames)

    def last_frame(self) -> bytes | None:
        return self.frames[-1] if self.frames else None


class FakeSource:
    """``SensorSource`` fake with a scripted sequence of metric snapshots.

    The last snapshot is repeated once the queue is exhausted (like a real
    steady-state source), so looping renderer tests see stable metrics.
    """

    def __init__(self, *snapshots: dict):
        self._queue = list(snapshots)
        self._last: dict = {}
        self.calls = 0

    def collect(self) -> dict:
        self.calls += 1
        if self._queue:
            self._last = self._queue.pop(0)
        return dict(self._last)


class FakeBackground:
    """``TrccBackground`` fake: records show/end calls and handed-off sources."""

    def __init__(self) -> None:
        self.shown: list[tuple[str, object, float]] = []
        self.ended: list[str] = []

    def show(self, key: str, source: object, fps: float) -> None:
        self.shown.append((key, source, fps))

    def end(self, key: str) -> None:
        self.ended.append(key)


class FakeStore:
    """``StateStore`` fake: in-memory write/read with a monotonic seq."""

    def __init__(self) -> None:
        self._state: dict | None = None
        self._seq = 0

    def write(self, state: dict) -> int:
        self._seq += 1
        merged = dict(state)
        merged["seq"] = self._seq
        self._state = merged
        return self._seq

    def read(self) -> dict | None:
        return dict(self._state) if self._state is not None else None

    def close(self) -> None:
        self._state = None
