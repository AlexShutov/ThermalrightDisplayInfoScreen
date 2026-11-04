# SESSION.md — live handoff (read first)

Current run state. Durable facts live in the `MEMORY.md` index + topic files; this file only holds
"where we are and what's next" for THIS run. Wrap-up: archive this file to
`memory/sessions/YYYY-MM-DD-<slug>.md`, add a line to that folder's `README.md`, then rewrite it.

## Where we are
Task: build a **theme manager / SDK** on top of `trcc daemon` (local HTTP service + Qt adapter module
drawing the dashboard over the theme). Dev + runtime = **macOS** today; Pi 5 / Orange Pi 6 Plus future.

Memory system just rebuilt to match trcc's structure (SESSION / MEMORY-index + topic files /
sessions archive). This is the start of Phase 0.

## Plan (phase list)
- **Phase 0 — Validate composite on top of theme (macOS).** Qt module draws a translucent overlay on
  top of the theme background -> frame to panel. Proves "theme under Qt overlay -> frame". NEXT.
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

## Open questions (this run)
File layout for new code, manager->trcc mechanism (CLI trigger vs trcc API), Qt render specifics
(QPainter vs QML). Details in `architecture.md` -> "Open questions". Do NOT assume these are chosen.

## Next action
- Read trcc API for `FrameSource`/`background.show/end`/`host.frames.watch`, confirm the frame cycle
  (theme background + Qt overlay -> panel), then write the phase list into todowrite and begin Phase 0.

## Handoff notes
- USB access denied for trcc daemon -> a `trofeo run`/`supervise` client holds the device; stop it.
- Daemon can hang re-fetching a poisoned cache (e.g. `e005.mp4` as 404 HTML); retry recovers.
- Test device (no app): `python tools/probe_ly.py`, `python tools/probe_fps.py`.