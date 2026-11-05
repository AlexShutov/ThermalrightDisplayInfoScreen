"""Display-plane adapter (Phase 3): render the manager's bridge as a dashboard.

This package is MIT and framework-free: it reads the atomic state-file bridge
(``StateStore``), lays out gauges from ``setup``+``values``+``palette``, renders a
raw RGB frame, and decides ``background.show``/``end`` behind the ``TrccBackground``
port. The trcc module wrapper (GPL) in ``dev/trcc-module-dashboard`` wires these to
``host.background`` and the panel ``FrameSource``.
"""
# SPDX-License-Identifier: MIT
