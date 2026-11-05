# SESSION.md — live handoff (read first)

Current run state. Durable facts live in the `MEMORY.md` index + topic files; this file only holds
"where we are and what's next" for THIS run. Wrap-up: archive this file to
`memory/sessions/YYYY-MM-DD-<slug>.md`, add a line to that folder's `README.md`, then rewrite it.

## Where we are
Task: **theme manager / SDK** on top of `trcc daemon` (local HTTP service + adapter module drawing a
dashboard over the theme). Dev + runtime = **macOS** today; Pi 5 / Orange Pi 6 Plus future.

**Phases 1-3 are encoded and green headless** (this run): manager (Phase 1), stub producer (Phase 2),
gill adapter core + GPL trcc module + optional `TrccThemeSwitcher` (Phase 3). The panel is NOT
currently attached, so all available work is done; the remaining checks need the real device — see
`hardware_verification.md`.

## Done (this run / recent)
- **Package rename** `trofeo` -> `hardwarescreen` (git path, package dir, scripts, deploy, docs),
  remote unchanged. Branch `rename-hardwarescreen`. Commits up to `d6e042b` pushed.
- **Phase 1 manager** (`src/manager/`): FastAPI+uvicorn (1 async worker), pydantic v2, `AtomicStateFile`
  (temp+fsync+os.replace + monotonic `seq`), `/health /dashboard/setup /dashboard/update /settings
  /theme`, stream-bounded body-cap (413), optional Bearer `auth_token`, `switch_theme` off-loop
  (`asyncio.to_thread` + `switch_timeout_s`, non-fatal).
- **Phase 2 stub** (`src/stubdata/` + `stubdata.yaml` + script): `Producer` (seeded RNG, `start/step`),
  `ManagerClient` port + `HttpManagerClient` (stdlib urllib), `make_gauges`.
- **Phase 3 adapter** (`src/hardwarescreen/adapter/`, MIT): `BridgePoller` (emit only on `seq` growth),
  `gauge_layout`+`compose_dashboard`+`hex_to_rgb` (raw RGB PIL), `DashboardSource.latest()` (keeps
  newest, always returns `w*h*3` bytes), `AdapterDriver` (show/end + attach-race + idempotent).
- **GPL trcc module** `dev/trcc-module-dashboard/` (`dashboard/app.py` + `module.json`): wraps the MIT
  adapter into `BaseModule`/`FrameSource`/`background.show/end`; `_loop` uses `poll() or last`.
  Only `py_compile`-checked (never imported in tests).
- **`src/manager/trcc.py`** optional `TrccThemeSwitcher` (injectable subprocess runner, literal
  `{theme}` replace); `config.theme_cmd`; wired in `create_app` with a live `state.themes` resolver.
- `src/hardwarescreen/imageutil.py` (`encode_within_budget`), `render.py` refactor onto it.
- Bug-Analysis Gate run twice; found + fixed HIGH/MED/MED/LOW/LOW (GPL keep-newest, tiny-canvas crash,
  stale-vs-live resolver, malformed seq, brace placeholder). Re-verify: READY.

## Open questions (this run)
- manager->trcc switch is CLI `trcc display play <id>` (opt-in via `theme_cmd`); REST-API trigger
  alternative still open (`theme_cmd` injection covers it).

## Plan (phase list)
- **Phase 0** cycle validated on hardware (probe module; composite over theme). Done.
- **Phase 1** manager framework — Done (headless; `/theme` persists + optional CLI trigger).
- **Phase 2** stub data source — Done (separate service; random updates + day/night switches).
- **Phase 3** dashboard adapter module — Done (headless core + GPL trcc wrapper + switcher).
- **Phase 4** day/night style via `/settings` — contract + palettes exist; active-palette interaction
  with the adapter validated headless; on-device confirm via `hardware_verification.md` §E.
- **Future** hardware & deploy: Pi 5 / Orange Pi 6 Plus (trcc aarch64, udev, systemd, deploy, docs).

## Next action
1. Commit + push the accumulated Phase 2+3 + bugfix work on `rename-hardwarescreen`, then merge to
   `main` in order (`aeaae48 -> d460a79 -> d9d16fe -> fac4951 -> d6e042b -> <new>`).
2. When the panel is available: run `memory/hardware_verification.md` top-to-bottom and record pass/fail.

## Handoff notes
- USB access denied for trcc daemon -> a `hardwarescreen run`/`supervise` client holds the device; stop it.
- Daemon can hang re-fetching a poisoned cache (e.g. `e005.mp4` as 404 HTML); retry recovers.
- Test device (no app): `python tools/probe_ly.py`, `python tools/probe_fps.py` (exit 1, "Device not found").
- trcc `FrameSource.latest()` returns RAW RGB (not JPEG); the module wrapper must hand `img.tobytes()`.