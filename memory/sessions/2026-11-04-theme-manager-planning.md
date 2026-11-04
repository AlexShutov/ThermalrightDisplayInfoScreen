# 2026-11-04 — theme-manager-planning (archived handoff)

Snapshot of the outgoing `SESSION.md` for the theme-manager planning run. Immutable history.

## Plan (phase list, in order)
- Phase 0 — Validate composite on top of theme (macOS). Qt module draws a translucent overlay on top
  of the theme background -> frame to panel. Proves "theme under Qt overlay -> frame".
- Phase 1 — Manager framework (24/7). FastAPI+uvicorn (1 worker, body-size limit, timeouts),
  endpoints /health, /dashboard/setup, /dashboard/update, /settings, /theme, state manager,
  atomic state-file bridge with `seq`. Bind 127.0.0.1 locally; LAN bind + optional bearer token is a
  config option for future remote-producer case.
- Phase 2 — Stub data source (separate service). On start /dashboard/setup, then random updates and
  random /theme switches.
- Phase 3 — Qt dashboard module. Reads bridge state, paints gauges from setup+values+palette; no data
  -> background.end(key).
- Phase 4 — Day/night style via /settings. Contract /settings (themes + day/night palettes), active
  palette into bridge, style switch without restart.
- Future — Hardware & deploy. Pi 5 / Orange Pi 6 Plus: verify trcc aarch64, udev, systemd, deploy.

## Decisions made
- Source of data = own local HTTP service; producers POST JSON to it. Confirmed.
- Day/night = manual trigger via a separate endpoint (POST /theme). Confirmed.
- Composite = variant A: Qt adapter draws dashboard over the theme (video plays as background).
- Technology = FastAPI + uvicorn (1 worker, async), pydantic v2. Bridge = atomic state file + seq.
- Hardware: dev + runtime = macOS today; Pi 5 ("use Pi5"), Pi 3 Model B lying around, Pi Zero/Zero 2 W
  ruled out, Orange Pi 6 Plus not arrived. Future = Pi 5 / Orange Pi 6 Plus.
- Pi 3 Model B verdict: good golden middle (1 GB RAM, 4 USB, no OTG issue). Regular Pi Zero NOT supported.
- Remote producer on LAN POSTs to manager; render path stays local on the host.
- Porting: keep platform specifics behind config.py; no active port now.

## Current state at wrap-up
- trcc daemon ran in background (bgp_1a7cc3dba001G8jsuaHgcuh1S4), panel last showed d029 (night).
- Stub trcc module (`key stub`) installed and ON.
- Women: panel was showing a cloud theme (no dashboard overlay yet).

## Next action (carried into the fresh SESSION.md)
- Start Phase 0: read trcc API for FrameSource/background/frames.watch, confirm the frame cycle
  (theme background + Qt overlay -> panel), then write the list into todowrite and begin.
- After Phase 0, verify USB is not contended by the thin client (`trofeo run` holds the device;
  stop it so trcc daemon can connect).

## Handoff notes
- USB access denied for trcc daemon -> a trofeo run/supervise client holds the device; stop it.
- Daemon can hang briefly re-fetching a poisoned cache (e.g. e005.mp4 cached as 404 HTML); retry.
- Test device (no app): python tools/probe_ly.py, python tools/probe_fps.py.