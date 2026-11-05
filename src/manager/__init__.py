"""Theme Manager (display-plane HTTP service on top of ``trcc daemon``).

Phase 1: FastAPI + uvicorn service with the generic JSON contract. The manager
keeps only the latest state (last-wins), derives the active palette from the
current mode, and publishes each change to the bridge (atomic state file with a
monotonic ``seq``) so the Qt adapter can react without an IPC library.
"""
# SPDX-License-Identifier: MIT
