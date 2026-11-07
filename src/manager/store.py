"""Atomic state-file ``StateStore`` (seq monotonic, temp + ``os.replace``).

The Qt adapter polls this file and reacts to ``seq`` growth, so a torn write or
backpressure is impossible (the platform guarantees ``os.replace`` is atomic on
the same filesystem). ``seq`` survives restarts: it is recovered from the last
file so a consumer never sees "older than before".
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import os
import time


class AtomicStateFile:
    """Persist the bridge state atomically; owns the monotonic ``seq``."""

    def __init__(self, path: str):
        self._path = path
        self._seq = self._read_seq()

    def _read_seq(self) -> int:
        try:
            with open(self._path) as fh:
                return int(json.load(fh).get("seq", 0))
        except Exception:
            return 0

    def write(self, state: dict) -> int:
        self._seq += 1
        payload = dict(state)
        payload["seq"] = self._seq
        payload["updated_at"] = time.time()
        temp_path = f"{self._path}.tmp"
        with open(temp_path, "w") as fh:
            json.dump(payload, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_path, self._path)
        return self._seq

    def read(self) -> dict | None:
        try:
            with open(self._path) as fh:
                return json.load(fh)
        except Exception:
            return None

    def close(self) -> None:
        return None
