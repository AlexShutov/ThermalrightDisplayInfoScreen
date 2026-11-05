"""Adapter driver: decide ``background.show`` vs ``background.end`` (pure).

Encapsulates the render-lifecycle rule — data present + panel attached ->
``show(key, source, fps)``; otherwise -> ``end(key)`` (theme video plays) — behind
the ``TrccBackground`` port, so it is testable with a fake. The attach-race guard
is built in: ``show`` is deferred until ``attached`` is True.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

from typing import Any

from hardwarescreen.ports import TrccBackground


def bridge_has_data(state: dict | None) -> bool:
    return bool(state and (state.get("setup") or state.get("values")))


class AdapterDriver:
    def __init__(
        self,
        background: TrccBackground,
        source: Any,
        *,
        key: str = "dashboard",
        fps: float = 15.0,
    ):
        self._bg = background
        self._source = source
        self._key = key
        self._fps = fps
        self._shown = False

    def update(self, state: dict | None, attached: bool) -> None:
        want = attached and bridge_has_data(state)
        if want and not self._shown:
            self._bg.show(self._key, self._source, self._fps)
            self._shown = True
        elif not want and self._shown:
            self._bg.end(self._key)
            self._shown = False

    @property
    def shown(self) -> bool:
        return self._shown
