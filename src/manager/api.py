"""FastAPI HTTP service for the Theme Manager (single async worker).

Factory ``create_app`` wires the injectable ports (``StateStore`` for the bridge,
a ``switch_theme`` callable for the day/night trcc trigger) and a clock, so the
whole contract is testable headless with ``TestClient`` + fakes.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Literal, cast

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from trofeo.ports import StateStore

from .config import Config
from .state import Gauge, ManagerState, bridge_dict, merge_values

LOG = logging.getLogger("manager.api")

PaletteMode = Literal["day", "night"]


# -- pydantic request models ---------------------------------------------
class GaugeModel(BaseModel):
    id: str
    label: str
    unit: str = ""
    min: float = 0.0
    max: float = 100.0


class DashboardSetup(BaseModel):
    gauges: list[GaugeModel]


class DashboardUpdate(BaseModel):
    values: dict[str, float]


class PaletteModel(BaseModel):
    background: str = ""
    panel: str = ""
    accent: str = ""
    text: str = ""
    muted: str = ""


class SettingsModel(BaseModel):
    themes: dict[PaletteMode, str] = Field(default_factory=dict)
    adapter: dict[PaletteMode, PaletteModel] = Field(default_factory=dict)


class ThemeModel(BaseModel):
    mode: PaletteMode


def create_app(
    cfg: Config,
    store: StateStore,
    switch_theme: Callable[[str], None] | None = None,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    default_mode = cfg.default_mode if cfg.default_mode in ("day", "night") else "day"
    state = ManagerState(
        mode=cast("PaletteMode", default_mode),
        themes=dict(cfg.themes),
        adapter={m: dict(p) for m, p in cfg.adapter.items()},
    )
    lock = asyncio.Lock()
    started = clock()
    # Recover the current seq from the bridge so /health never reports a stale
    # seq after a restart (the store advances seq monotonically across runs).
    last_seq = int((store.read() or {}).get("seq", 0))
    switcher: Callable[[str], None] = switch_theme or (lambda mode: None)

    def _authorize(request: Request) -> None:
        if cfg.auth_token:
            header = request.headers.get("authorization")
            if header != f"Bearer {cfg.auth_token}":
                raise HTTPException(status_code=401, detail="unauthorized")

    async def _limit_body(request: Request, call_next):
        # Fast-path reject a CS on oversized content-length, then stream-count so
        # a chunked body cannot bypass the cap (memory stays bounded by the limit).
        cl = request.headers.get("content-length")
        if cl:
            try:
                if int(cl) > cfg.body_limit_bytes:
                    return JSONResponse({"detail": "payload too large"}, status_code=413)
            except ValueError:  # non-numeric CL: fall through to the stream guard
                pass
        n = 0
        chunks: list[bytes] = []
        async for chunk in request.stream():
            n += len(chunk)
            if n > cfg.body_limit_bytes:
                return JSONResponse({"detail": "payload too large"}, status_code=413)
            chunks.append(chunk)
        request._body = b"".join(chunks)
        return await call_next(request)

    def _bridge_and_write() -> int:
        return store.write(bridge_dict(state))

    async def _switch_with_timeout(mode: str) -> None:
        # Run the trcc trigger off the event loop with a timeout so a hung
        # daemon cannot freeze the single worker (AGENTS: timeouts on outbound).
        try:
            await asyncio.wait_for(
                asyncio.to_thread(switcher, mode), timeout=cfg.switch_timeout_s
            )
        except Exception as e:  # a bad trcc call must not kill the dashboard
            LOG.warning("theme switch failed (%s); last state holds", e)

    app = FastAPI(title="Trofeo Theme Manager", version="0.1.0")
    app.state.store = store
    app.middleware("http")(_limit_body)

    @app.get("/")
    def root():
        return {"service": "trofeo-manager", "docs": "/docs"}

    @app.get("/health", dependencies=[Depends(_authorize)])
    async def health():
        nonlocal last_seq
        async with lock:
            return {
                "status": "ok",
                "uptime_s": round(clock() - started, 3),
                "seq": last_seq,
                "mode": state.mode,
            }

    @app.post("/dashboard/setup", dependencies=[Depends(_authorize)])
    async def setup(payload: DashboardSetup):
        nonlocal last_seq
        async with lock:
            state.setup = [
                Gauge(id=g.id, label=g.label, unit=g.unit, min=g.min, max=g.max)
                for g in payload.gauges
            ]
            last_seq = _bridge_and_write()
        return {"seq": last_seq, "mode": state.mode}

    @app.post("/dashboard/update", dependencies=[Depends(_authorize)])
    async def update(payload: DashboardUpdate):
        nonlocal last_seq
        async with lock:
            state.values = merge_values(state.values, payload.values)
            last_seq = _bridge_and_write()
        return {"seq": last_seq, "mode": state.mode}

    @app.post("/settings", dependencies=[Depends(_authorize)])
    async def settings(payload: SettingsModel):
        nonlocal last_seq
        async with lock:
            state.themes = {k: v for k, v in payload.themes.items()}
            state.adapter = {m: p.model_dump() for m, p in payload.adapter.items()}
            last_seq = _bridge_and_write()
        return {"seq": last_seq, "mode": state.mode, "palette": state.adapter.get(state.mode)}

    @app.post("/theme", dependencies=[Depends(_authorize)])
    async def theme(payload: ThemeModel):
        nonlocal last_seq
        async with lock:
            state.mode = payload.mode
            last_seq = _bridge_and_write()
            mode = state.mode
            active = state.themes.get(state.mode)
        await _switch_with_timeout(payload.mode)
        # Snapshot inside the lock so the response is one consistent state even
        # under a concurrent /theme interleaving the (awaited) switch.
        return {"seq": last_seq, "mode": mode, "active_theme": active}

    return app
