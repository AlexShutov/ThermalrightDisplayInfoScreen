"""Stub module — verifies the trcc module plumbing.

Registers under the key ``stub`` and switches on, but draws nothing yet.
Prints to the trcc daemon's stdout so we can confirm it is really called and
that it can see the attached panels. Later this is where the car-style
indicator dashboard (FrameSource / background) will be wired in.
"""

# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

from trcc.adapters.modules._base import BaseModule


class StubModule(BaseModule, key="stub"):
    """Minimal stub: called by trcc, draws nothing."""

    title = "Stub"
    summary = "Loads and switches on, draws nothing yet."
    default_on = False

    def _enable(self, host):
        print(f"TROFEO_STUB: enabled host={type(host).__name__}", flush=True)
        try:
            print(f"TROFEO_STUB: panels={host.panels.attached()}", flush=True)
        except Exception as e:  # keep the stub robust
            print(f"TROFEO_STUB: panels query failed: {e}", flush=True)

    def _disable(self, host):
        print("TROFEO_STUB: disabled", flush=True)
