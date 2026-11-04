# architecture.md — Theme Manager decision record

## Goal / scope
A reusable **theme manager / SDK** on top of `trcc daemon` — NOT tied to one use (not only a car
dashboard): the same toolset can drive other dashboards/adapters, e.g. a work display that warns
about meetings. The JSON endpoints are deliberately generic so producers carry the domain, not the
manager.

## Relationship to existing `src/trofeo/`
- `src/trofeo/` (thin client `trofeo run` + `supervise` + `canvas`) stays separate; it is the
  self-contained fallback (own LY transport, no trcc).
- The new manager + Qt module run on top of `trcc daemon`. **USB is exclusive:** the device is held
  by EITHER a thin client (`trofeo run`) OR `trcc daemon` — never both. Running both -> trcc daemon
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

## Adapter data / day-night style
- Adapter reads bridge state (setup + values + palette), draws gauges. Day/night palette arrives via
  `/settings` -> manager -> bridge; style switch without module restart.
- Bind `127.0.0.1` locally; LAN bind + optional `Authorization: Bearer <token>` is a config option
  (for future remote producers, e.g. Orange Pi 6 Plus POSTing over LAN).

## Stub data source
- On start: `POST /dashboard/setup`, then random `update`s and random `/theme` mode switches.
  Separate service; enough for development.

## Open questions (NOT yet decided — a new session must treat these as open, not assumed)
- **File layout:** where the new code lives. Candidates: a new installable package (e.g.
  `src/manager/` + a separate console command), alongside the existing `src/trofeo/`. Not chosen.
- **Manager -> trcc mechanism:** drive `trcc daemon` via its CLI triggers (`trcc display play d037`)
  or via trcc's own FastAPI REST API (which dispatches Commands on the App). Not chosen.
- **Qt render details:** QPainter widgets vs QML, DPI/devicePixelRatio for the 1920x440 canvas, where
  the Qt module lives (own GPL repo vs `dev/`). Not chosen.
- **How the module gets its composited frame into the panel while the theme plays** — exact
  FrameSource/background protocol to confirm in Phase 0. Investigate trcc internals, don't assume.