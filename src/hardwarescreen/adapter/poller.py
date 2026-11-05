"""Bridge poller: emit a state only when its monotonic ``seq`` grows."""
# SPDX-License-Identifier: MIT
from __future__ import annotations

from hardwarescreen.ports import StateStore


class BridgePoller:
    """Watches the state file and emits a newer state exactly once per change.

    ``seq`` is monotonic, so a torn write (rolled back) or a replayed/older file is
    ignored (``seq <= seen``) — the consumer never goes backwards.
    """

    def __init__(self, store: StateStore):
        self._store = store
        self._seen_seq = -1
        self._last: dict | None = None

    def poll(self) -> dict | None:
        state = self._store.read()
        if not isinstance(state, dict) or state.get("seq") is None:
            return None
        try:
            seq = int(state["seq"])
        except (TypeError, ValueError):
            return None
        if seq > self._seen_seq:
            self._seen_seq = seq
            self._last = dict(state)
            return self._last
        return None

    @property
    def last(self) -> dict | None:
        return dict(self._last) if self._last is not None else None

    @property
    def seen_seq(self) -> int:
        return self._seen_seq
