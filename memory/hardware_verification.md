# hardware_verification.md — on-screen check-list (requires the panel)

These checks can ONLY run with the real 1920x440 panel (`0416:5408`) attached and
`trcc daemon` running with USB access. Every phase is otherwise proven headless
(`tests/`, `tools/quality_check.sh`). Work from top to bottom; run each and record
pass/fail. All timings/budgets are the totals the manager assumes.

Prep (once, before the check-list):
```
# start the manager (writes the atomic bridge manager_state.json)
hardwarescreen-manager --config manager.yaml
# start the stub producer (optional, feeds gauges + random day/night switches)
hardwarescreen-stubdata --config stubdata.yaml
# install + enable the dashboard trcc module (GPL, dev/trcc-module-dashboard)
```             

## A. Composite over theme
- [ ] `dev/trcc-module-dashboard` enable: panel shows gauge bars over the day theme.
- [ ] `background.end` (module disable / no-data) restores the pure theme (no leftover).
- [ ] Bar color/text readable on both day and night palettes (no washed-out contrast).
- [ ] Empty `setup`+`values` (no data) -> clean theme, NOT a stale dashboard.

## B. Attach race
- [ ] Enable the module BEFORE the panel is attached: no crash; dashboard appears
      once the panel attaches (driver defers `show`).
- [ ] Unplug/replug the panel while the module is shown: recovers to a live dashboard.

## C. Render budget / geometry
- [ ] Renderer never emits a frame > `max_frame_bytes` (450 KB) at 1920x440.
- [ ] `compose_dashboard` at 1920x440 produces a valid frame every tick (no
      `ValueError`, no zero-height gauge crash with many gauges).

## D. FPS / smoothness
- [ ] `tools/probe_fps.py` reports panel FPS on the real device.
- [ ] Dashboard updates at the configured `fps` with no visible stutter/frame drops.

## E. Day/night switch without restart
- [ ] `POST /theme {"mode":"night"}` -> background switches to `d029`; `day` ->
      `d037`; manager stays up (single async worker not frozen).
- [ ] The reported `/theme` `active_theme` matches the theme actually on screen
      (live `state.themes`, not stale startup config).
- [ ] `POST /settings` with new themes/palettes -> next switch uses the new value.

## F. USB contention
- [ ] `tools/probe_ly.py` / `tools/probe_fps.py` exit cleanly ("Device not found")
      when no device present (exit 1, no traceback).
- [ ] Only one client owns the device at a time; manager + producer never contend.

## G. Auth / body bound (no hardware, sanity)
- [ ] With `auth_token` set, unauthenticated `/health` returns 401.
- [ ] A payload over `body_limit_bytes` returns 413 (fast-path + stream count).

## Known non-goals / caveats to confirm on device
- The trcc `FrameSource` consumes raw RGB; if the module instead hands JPEG, the
  panel shows garbage / wrong colours — confirm the wrapper uses `img.tobytes()`.
- Qt-overlay composite (theme video under gauges) is the intended look; this
  adapter renders a plain background. Verify a real theme-video base + overlay later.