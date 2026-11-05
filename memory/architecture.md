# architecture.md — Theme Manager decision record

## Goal / scope
A reusable **theme manager / SDK** on top of `trcc daemon` — NOT tied to one use (not only a car
dashboard): the same toolset can drive other dashboards/adapters, e.g. a work display that warns
about meetings. The JSON endpoints are deliberately generic so producers carry the domain, not the
manager.

## Relationship to existing `src/hardwarescreen/`
- `src/hardwarescreen/` (thin client `hardwarescreen run` + `supervise` + `canvas`) stays separate; it is the
  self-contained fallback (own LY transport, no trcc).
- The new manager + Qt module run on top of `trcc daemon`. **USB is exclusive:** the device is held
  by EITHER a thin client (`hardwarescreen run`) OR `trcc daemon` — never both. Running both -> trcc daemon
  gets `Access denied`. The manager assumes `trcc daemon` owns the panel.

## Structure
Two planes. **Display plane** (host owns USB + panel): `manager` (HTTP service) + `trcc daemon`
(plays the video theme) + a **Qt adapter module** drawing the dashboard *on top of* the theme.
**Data plane** (any host on LAN): producer apps/daemons `POST` JSON to the manager. Merge stays local
on the display host; a remote producer crashing never kills the dashboard (last state holds).

## Manager stack (choice)
- Python + **FastAPI + uvicorn** (single worker, `loop="asyncio"`), **pydantic v2** for validation.
- Rationale for 24/7: request-scoped state (no global accumulation -> no leak), one async worker
  (no per-request thread -> no race), bounded request-body middleware (-> 413 instead of OOM),
  timeouts on every outbound call, `systemd Restart=on-failure`.
- Only latest state is retained (one dict replaced whole) — no growing queues -> no overflow.

## Bridge manager -> Qt module
- Atomic state file with monotonic `seq` (temp + `os.replace`). Module polls, reacts to `seq` growth.
  No IPC library. Stale detection via `seq`/`updated_at`. Prevents torn reads + backpressure.

## API contract (subject to change)
- `POST /dashboard/setup` — `{ "gauges":[{"id","label","unit","min","max"}] }`
- `POST /dashboard/update` — `{ "values":{...} }` (full or partial)
- `POST /settings` — `{ "themes":{"day":id,"night":id}, "adapter":{"day":{palette},"night":{palette}} }`;
  manager computes **active palette from current mode** (color names: background, panel, accent, text, muted).
- `POST /theme` — `{ "mode":"day"|"night" }` (manual trigger), switches trcc theme + adapter style.
- `GET /health` — `{ "status","uptime_s","seq","mode" }`
- Bridge state object: `{ "seq","mode","active_theme","palette","setup","values" }`

## Render lifecycle rule
- No data -> `background.end(key)` (clean theme). Data present -> `background.show(key, source, fps)`.
- Render reactively (on change); keepalive for the panel (~1.5-2 s).

## Composite choice
- **Variant A: Qt adapter** — theme video plays as background, Qt draws overlay, frame handed to
  `background`. Static-JPEG fallback is not the target (loses live cloud themes).

## Composite mechanism (verified from trcc source)
- `host.background.show(key, FrameSource, fps)` makes the module's source the panel's background:
  the App dispatches `StopVideo` (theme video STOPS), saves the choice, opens the module source, and a
  `SourceDriver` (interval `1/fps`) runs `CaptureSourceFrame` -> `source.latest()` each tick,
  compositing the theme's mask + metrics on top, and sends. (`app.py:571-584`, `services/source_driver.py`)
- `host.background.end(key)` closes the source, stops the driver, and `PlayVideo(path)` resumes the
  theme's own video. (`app.py:586-602`)
- FrameSource port (`ports.py:2558`): `latest() -> RawFrame` ("CaptureNotReady" while starting,
  `OSError` on permanent failure), `close()`; module draws on its own thread, keeps only newest.
- Capabilities gate: module must declare a capability in `module.json` to reach `host.*`; undeclared -
  `ModuleScope._need` raises (e.g. "Stub asked for 'panels' without declaring it").
- Behaviors that matter for us: with `background.show` the module's source REPLACES the theme's video
  as the background (video stops); the App puts the theme's mask + metrics on top. So "live theme video
  under our overlay" is NOT what `background.show` gives by itself -- the module's FrameSource must
  draw the composite, optionally decoding the theme MP4 (ffmpeg) as its base.
- The supported lifecycle matches our plan: no data -> `background.end` (theme video plays); data -> 
  `background.show` (our Qt dashboard becomes the background; theme mask/metrics composited on top).
  If we want the theme video visible under the dashboard even in data-present mode, the module's
  FrameSource re-decodes the theme MP4 as its base and draws the Qt overlay on top.

## Adapter data / day-night style
- Adapter reads bridge state (setup + values + palette), draws gauges. Day/night palette arrives via
  `/settings` -> manager -> bridge; style switch without module restart.
- Bind `127.0.0.1` locally; LAN bind + optional `Authorization: Bearer <token>` is a config option
  (for future remote producers, e.g. Orange Pi 6 Plus POSTing over LAN).

## Stub data source
- On start: `POST /dashboard/setup`, then random `update`s and random `/theme` mode switches.
  Separate service; enough for development.

## Testing (reused from trcc)
Every phase is gated by its tests, run on a fake device — no hardware needed. Core logic is pure
Python behind injectable ports; frameworks (Qt/FastAPI/trcc) are adapters only at the edges. Full
strategy + concrete rules: `memory/testing.md`.

## Phase 0 — validated on hardware (macOS, Trofeo 11.3)
- Module FrameSource cycle PROVEN: probe module (key `probe`, in `dev/trcc-module-probe/`)
  `_enable` -> `host.background.show(key, source, 15.0)`. Verified in the daemon log:
  `show_source: 0416:5408 <- probe.pattern (probe, 15 fps)` -> `SourceDriver: every 67 ms`
  -> `probe: showing pattern on 0416:5408 (1920x440) at 15 fps`; `trcc status` -> `showing: the Probe module`.
- `host.background.end(key)` restores the theme: `end_source` -> `probe: ended -> theme restored`
  (`showing: the theme's background`). The probe is currently turned OFF (clean state).
- **Robustness gap found:** module `_enable` can run BEFORE the panel is attached (at daemon startup
  the device is still enumerating), so `host.panels.attached()` may be EMPTY and `background.show`
  cannot run; our naive probe just warns and returns. The real adapter MUST defer `background.show`
  until a panel is present -- subscribe to the panel-attach/`PanelChanged`-style event, or have the
  MANAGER (which knows when it asked trcc to load a theme for the panel) signal the module when to show.
- Composite (user chose option 1): theme video decodes (ffmpeg) as the module's base + Qt overlay on
  top, served via the FrameSource. The probe uses a plain RGB24 pattern to prove the cycle; the
  base-video decode + Qt overlay are the next refinement on this same seam.

## Open questions (NOT yet decided — a new session must treat these as open, not assumed)
- **File layout:** *solved in Phase 1* — the manager is a new package **`src/manager/`** (console script `hardwarescreen-manager`), sharing the ports in `src/hardwarescreen/ports.py`; `manager` -> `hardwarescreen.ports` is a one-way import (no cycle). setuptools `where=["src"]` discovers both. FastAPI/uvicorn/pydantic are now **base runtime deps** (manager is a first-class component), not an optional extra.
- **Manager -> trcc mechanism:** drive `trcc daemon` via its CLI triggers (`trcc display play d037`) or via trcc's own FastAPI REST API (which dispatches Commands on the App). Not chosen.
- **Qt render details:** QPainter widgets vs QML, DPI/devicePixelRatio for the 1920x440 canvas, where the Qt module lives (own GPL repo vs `dev/`). Not chosen.
- **How the module gets its composited frame into the panel while the theme plays** — exact FrameSource/background protocol to confirm in Phase 0. Investigate trcc internals, don't assume.

## Phase 1 — implemented (verified headless)
- `src/manager/` FastAPI + uvicorn (single async worker, `loop="asyncio"`), pydantic v2 models.
- Request-scoped/last-wins state under one `asyncio.Lock` (no global accumulation, no per-request threads).
- Endpoints implemented: `GET /health`, `POST /dashboard/setup`, `POST /dashboard/update`, `POST /settings`, `POST /theme`. pydantic `dict[Literal["day","night"], ...]` rejects unknown modes (422).
- Body cap: **stream-bounded** middleware closes the chunked/CL-less bypass (reject once over `body_limit_bytes`, memory bounded by the limit) and keeps the fast-path `content-length` rejection (413). Optional `Authorization: Bearer <token>` via `auth_token`.
- `switch_theme` is an injected `Callable[[str], None]` (default no-op); called **off-loop** via `asyncio.to_thread` bounded by `switch_timeout_s`, and non-fatal (a hung/bad trcc call logs + last state holds) — never freezes the worker.
- Bridge: `src/manager/state.py` pure `bridge_dict(state)` (data-plane payload without seq) -> `src/manager/store.py` `AtomicStateFile` (temp + `fsync` + `os.replace`, monotonic `seq` recovered across restarts). `/health` seeds `last_seq` from `store.read()` so it is never stale after a restart.
- Palette/theme derivation: `active_palette` / `active_theme` from the current mode; `/settings` recomputes for the current mode; `/theme` commits state then switches.
- Tests (`test_manager_state.py`, `test_manager_store.py`, `test_manager_api.py`) cover the pure model, atomic seq, and the full HTTP contract via `TestClient` + fakes (33 tests total green; `ruff`/`mypy` clean).