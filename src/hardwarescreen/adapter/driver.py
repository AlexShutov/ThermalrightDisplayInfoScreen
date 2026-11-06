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
        self._shown_key: str | None = None

    def update(self, state: dict | None, attached: bool,
               panel_key: str | None = None) -> None:
        """React to a bridge state + attach change.

        ``panel_key`` is the panel (device) key the background belongs to — the
        key ``background.show``/``end`` expect. It defaults to the constructor
        ``key`` so a single-key setup needs no call-site change; a module that
        learns the panel key only once a panel attaches passes it here, so an
        attach race does not target a stray key.
        """
        key = panel_key or self._key
        want = attached and bridge_has_data(state)
        if want:
            if not self._shown:
                self._bg.show(key, self._source, self._fps)
                self._shown = True
                self._shown_key = key
            elif self._shown_key != key:
                # The panel (device) key changed while we stayed shown: hand the
                # old panel back to its theme and take the new one.
                assert self._shown_key is not None
                self._bg.end(self._shown_key)
                self._bg.show(key, self._source, self._fps)
                self._shown_key = key
        elif self._shown:
            assert self._shown_key is not None
            self._bg.end(self._shown_key)
            self._shown = False
            self._shown_key = None

    @property
    def shown(self) -> bool:
        return self._shown

    @property
    def shown_key(self) -> str | None:
        """The panel key currently shown, or ``None`` when nothing is shown."""
        return self._shown_key
