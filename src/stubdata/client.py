"""Manager client port (stdlib HTTP; fake injectable for tests)."""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import urllib.request
from typing import Protocol


class ManagerClient(Protocol):
    def setup(self, gauges: list[dict]) -> None: ...
    def update(self, values: dict[str, float]) -> None: ...
    def set_theme(self, mode: str) -> None: ...


class HttpManagerClient:
    """POSTs the stub's data to the manager over stdlib ``urllib`` (no extra dep)."""

    def __init__(self, base_url: str, timeout_s: float = 5.0):
        self._url = base_url.rstrip("/")
        self._timeout = timeout_s

    def _post(self, path: str, payload: dict) -> None:
        req = urllib.request.Request(
            f"{self._url}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout):
            pass  # status is enough; the bridge is the source of truth

    def setup(self, gauges: list[dict]) -> None:
        self._post("/dashboard/setup", {"gauges": gauges})

    def update(self, values: dict[str, float]) -> None:
        self._post("/dashboard/update", {"values": values})

    def set_theme(self, mode: str) -> None:
        self._post("/theme", {"mode": mode})
