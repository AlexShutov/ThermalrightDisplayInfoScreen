# SESSION.md — live handoff (read first)

Current run state. Durable facts live in the `MEMORY.md` index + topic files; this file only holds
"where we are and what's next" for THIS run. Wrap-up: archive this file to
`memory/sessions/YYYY-MM-DD-<slug>.md`, add a line to that folder's `README.md`, then rewrite it.

## Where we are
Task: build a **theme manager / SDK** on top of `trcc daemon` (local HTTP service + Qt adapter module
drawing the dashboard over the theme). Dev + runtime = **macOS** today; Pi 5 / Orange Pi 6 Plus future.

Phase 0 core CYCLE is validated on hardware: a trcc module (key `probe`, `dev/trcc-module-probe/`)
-> `host.background.show(key, FrameSource, 15 fps)` -> SourceDriver drives `latest()` -> panel
(1920x440), and `background.end(key)` restores the theme. Probe is installed but currently OFF.
The user picked composite option 1 (theme video under Qt overlay): next is to add the theme-video base
(ffmpeg) + Qt overlay to the FrameSource, and handle the panel-attach race (enable can precede attach).

## Open questions (this run)

## Plan (phase list)
- **Cross-cutting: Test foundation (trcc style).** Injectable ports (FrameSink/SensorSource/
  TrccBackground/StateStore) + MockPanel + fake transport, pytest `tests/` + `dev/` smoke harness,
  coverage ratchet. Every phase is done only when its tests pass on the fake device. See
  `memory/testing.md`. Set up FIRST (alongside Phase 0).
- **Phase 0 — Validate composite on top of theme (macOS).** Qt module draws a translucent overlay on
  top of the theme background -> frame to panel. Proves "theme under Qt overlay -> frame". NEXT
  (stand up ports + MockPanel + pytest scaffolding first).
- **Phase 1 — Manager framework (24/7).** FastAPI+uvicorn (1 worker, body-size limit, timeouts),
  endpoints `/health, /dashboard/setup, /dashboard/update, /settings, /theme`, state manager, atomic
  state-file bridge with `seq`. Bind `127.0.0.1`; LAN bind + optional bearer token via config.
- **Phase 2 — Stub data source (separate service).** On start `POST /dashboard/setup`, then random
  `update`s and random `/theme` switches.
- **Phase 3 — Qt dashboard module.** Reads bridge state, paints gauges from `setup`+`values`+palette;
  no data -> `background.end(key)` (clean theme).
- **Phase 4 — Day/night style via /settings.** Contract `/settings` (themes + day/night palettes),
  active palette into bridge, style switch without restart.
- **Future — Hardware & deploy.** Pi 5 / Orange Pi 6 Plus: verify trcc aarch64, udev, systemd,
  deploy script, docs.

## Decisions made (this run)
- Data source = own local HTTP service; producers POST JSON. Day/night = manual `POST /theme`.
- Composite = variant A (Qt adapter over video theme). Stack = FastAPI+uvicorn; bridge = state file+seq.
- Hardware: dev+runtime macOS; Pi 5 present (use it), Pi 3 Model B around, Pi Zero/Zero 2 W out,
  Orange Pi 6 Plus future. Platform specifics behind `config.py`.

## Design borrowings to follow (trcc, adaptation only)
Applied during development per `memory/trcc_borrowed.md`: staged atomic downloads + safe fetch,
errno-classified reconnect/backoff, last-command-wins single-slot sender, fsync'd + schema-versioned
persistence, one multi-process rotating log (RenderOnce + per_frame + ClassContextFilter), health-probe
registry + whole-log report, daemon/IPC lifecycle (AppLock, ensure_daemon never-downgrade, "handler
must not kill the owner"), module install trust chain (consent-match + atomic swap + rollback gate),
sensor self-guard + unsupported() withholding, safe JSON/path helpers, projection/constraint pydantic
models, single-broadcast merge, Blob/per_frame/recurring_warning utils.

## Open questions (this run)
File layout for new code, manager->trcc mechanism (CLI trigger vs trcc API), Qt render specifics
(QPainter vs QML). Details in `architecture.md` -> "Open questions". Do NOT assume these are chosen.

## Next action
- Stand up the **test foundation first**: introduce the injectable ports (FrameSink/SensorSource/
  TrccBackground/StateStore), a `MockPanel` + fake transport, pytest scaffolding (`tests/`),
  and the fastapi TestClient + fakes — then begin Phase 0.
- Read trcc API for `FrameSource`/`background.show/end`/`host.frames.watch`, confirm the frame cycle
  (theme background + Qt overlay -> panel), then write the phase list into todowrite and begin.

## Handoff notes
- USB access denied for trcc daemon -> a `hardwarescreen run`/`supervise` client holds the device; stop it.
- Daemon can hang re-fetching a poisoned cache (e.g. `e005.mp4` as 404 HTML); retry recovers.
- Test device (no app): `python tools/probe_ly.py`, `python tools/probe_fps.py`.