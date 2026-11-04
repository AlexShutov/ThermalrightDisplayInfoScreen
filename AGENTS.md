# AGENTS.md

## Project Goal
A **portable, self-contained app** that drives the **Thermalright Trofeo Vision 11.3 LCD**
(USB `0416:5408`, "LY" bulk protocol) as a system-info dashboard, with:

- **USB reconnect** (the device sits in a car; power/USB cable can drop) — recover automatically.
- **Reactive rendering** — render + send only when displayed data changes (it is a picture-frame
  panel; identical data does not need resending).
- **Keepalive** — the panel reverts to its logo ~2–3 s after the last frame, so send at least every
  ~1.5–2 s to hold the dashboard.
- **Portability without an LLM** — the runtime is plain Python + system tools; it must build, run
  and configure with `pip install` and a config file, with **no AI/LLM required** at build or run time.

## Hardware / protocol facts (measured on real hardware, do not re-derive)

- Device: `VID 0x0416 PID 0x5408`, `USBDISPLAY`, LY bulk.
- Endpoints: **write EP `0x09` OUT**, **read EP `0x81` IN** (auto-detected; do not hardcode the
  class's `_EP_WRITE`).
- **Handshake**: write 2048 bytes → read 512 bytes. Validate `resp[0]==3`, `resp[1]==0xFF`,
  `resp[8]==1`. `PM = 64 + max(resp[20], 1)` (clamp raw<=3 to 1), `SUB = resp[22] + 1`.
  `PM==69` → Trofeo Vision **11.3** (marketed 1920×400; handshake reply reports 1920×440).
- **Frame send**: payload → 512-byte chunks (16-byte header + 496 data). Header:
  `b0=0x01, b1=0xFF, u32@2=total_size, u16@6=data_len, b8=0x01(LY), u16@9=num_chunks, u16@11=idx`.
  Pad chunk count to a multiple of **4**. Write in **4096-byte** writes on `0x09`, then read a
  512-byte ACK on `0x81`.
- **Frame-size ceiling: JPEG ≤ 450 000 bytes.** Above ~450 KB the panel silently freezes/drops
  frames (measured: Q~80/443 KB displays; Q~88/553 KB freezes). Never exceed the budget — enforce
  a quality/scale shrink loop. Do **not** rely on the ACK to detect a drop (ACK returns even for
  dropped frames).
- **Frame-rate ceiling: ~18 FPS** (write+ACK round-trip ≈ 55 ms), essentially independent of frame
  size within budget. This is the vendor's own `idle 2 / active 15 FPS` design.
- **Revert-to-logo**: the panel returns to the logo ~2–3 s after the last frame → the renderer must
  send a keepalive frame at least every ~1.5–2 s.

## Essential Architecture

- **`src/trofeo/`** package (installable, console script `trofeo`):
  - `supervise.py` — USB-lifetime supervisor (state machine `WAIT ⟷ RUN`; poll `1s`, backoff;
    starts/stops the engine; handles SIGINT/SIGTERM).
  - `render.py` — **reactive renderer**: collects metric changes → dirty-check → re-render; plus a
    keepalive timer (~2 s); enforces the 450 KB frame budget.
  - `ly.py` — our own LY USB transport (proven, pure Python via pyusb): handshake + frame send
    (+ ACK). This makes the core path self-contained and portable without `trcc`.
  - `canvas.py` — frame composition (Pillow) for the dashboard.
  - `sensors.py` — CPU/GPU/RAM/temp/disk/net collection (psutil).
  - `config.py` — YAML-first config.
- **Optional engine** `trcc-linux` (a ready Control Center, **GPL-3.0**) for advanced themes/video.
  The thin client can drive it, or fall back to `ly.py`. On real hardware (9.12.0, macOS dev) it
  detects `0416:5408`, connects as **Trofeo Vision 11.3, 1920×440, model_id 69** (cross-validates our
  handshake/PM=69) and sends frames. The **persistent engine is `trcc daemon`** (owns USB, serves the
  IPC socket, renders + keeps the panel updated) — so the supervisor wraps `.../trcc daemon`. The
  one-shot commands (`display play`, `display load-image`, `display serve`) are **triggers** that
  auto-start the daemon when none is running, and are NOT standalone long-running engine processes.
  We still layer our own supervisor on top so USB reconnect is our responsibility.

## Theme Manager (local HTTP service + Qt adapter)

The active project work extends the app into a **theme manager / SDK** on top of `trcc daemon`:

- **Architecture: two planes.**
  - *Display plane* (the host that owns USB + the panel): `manager` (HTTP service) + `trcc daemon`
    (plays the video theme) + a **Qt adapter module** that draws the dashboard *on top of* the theme.
  - *Data plane* (any host on LAN): producer apps/daemons `POST` JSON to the manager. Merge stays
    local on the display host; a remote producer crashing never kills the dashboard (last state holds).
- **Manager stack: Python + FastAPI + uvicorn** (single worker, `loop="asyncio"`), **pydantic v2** for
  request validation. Chosen for 24/7 robustness: request-scoped state (no global accumulation →
  no leak), one async worker (no per-request thread → no race), bounded request-body size middlware
  (→ 413 instead of OOM on small RAM), timeouts on every outbound call, `systemd Restart=on-failure`.
- **Manager → module bridge: atomic state file with monotonic `seq`** (temp + `os.replace`, the module
  polls and reacts to `seq` growth). No IPC library; avoids torn reads and backpressure; the module
  sees stale data via a `seq`/`updated_at` guard.
- **API contract (subject to change as we build):**
  - `POST /dashboard/setup` — `{ "gauges":[{"id","label","unit","min","max"}] }`
  - `POST /dashboard/update` — `{ "values":{...} }` (full or partial)
  - `POST /settings` — `{ "themes":{"day":id,"night":id}, "adapter":{"day":{palette},"night":{palette}} }`;
    the manager computes the **active palette from the current mode** and is the way theme colors reach
    the adapter (color names: `background`, `panel`, `accent`, `text`, `muted`).
  - `POST /theme` — `{ "mode":"day"|"night" }` (manual trigger), switches trcc theme + adapter style.
  - `GET /health` — `{ "status","uptime_s","seq","mode" }`
  - Bridge state object: `{ "seq", "mode", "active_theme", "palette", "setup", "values" }`
- **Render/lifecycle rule:** *no data → `background.end(key)`* (clean theme); *data present →
  `background.show(key, source, fps)`*. Render reactively (on change), keepalive for the panel.
- **Composite choice: Qt adapter (variant A)** — theme video plays as the background, Qt draws an
  overlay packed into the frame and handed to `background`. Static-JPEG fallback is not the target
  (loses live cloud themes).
- **Licensing:** the manager stays **MIT**; the **Qt adapter module is GPL-3.0** (imports trcc
  internals) — a separate external integration, never copied into MIT sources (same rule as trcc).
- **Hardware target:** dev + runtime is **macOS** today (Trofeo is USB here; trcc daemon runs headless).
  **Pi 5 / Orange Pi 6 Plus are future** deployment targets. Keep platform specifics (ffmpeg hwaccel,
  headless Qt `QT_QPA_PLATFORM=offscreen`, DPI/devicePixelRatio, `sensors` temperature branch, Python
  version, udev/systemd) behind `config.py` so the port later is config-only. On Pi 5 verify `trcc`
  runs on aarch64 (the one real porting risk); fallback is our own `ly.py` + `supervise.py`.

## Memory System (read on session start, archive on wrap-up)

We keep a trcc-style memory tree so a new session/LLM picks up full context without re-deriving it:

- **Startup order:** read `memory/SESSION.md` first (live handoff: current state + next step), then
  `memory/MEMORY.md` (index of durable topic files), then open the topic files relevant to the task
  (`hardware_protocol`, `trcc_engine`, `trcc_module_api`, `architecture`, `conventions`).
- **Live vs durable:** `memory/SESSION.md` holds only current-run state and is rewritten every session.
  Durable facts go in a topic file + the index, never in `SESSION.md`.
- **Wrap-up:** before overwriting `SESSION.md`, copy its outgoing content to
  `memory/sessions/YYYY-MM-DD-<slug>.md` and add a line to that folder's `README.md`. Then rewrite it.
- **Verify, don't trust prose:** memory is point-in-time. Re-measure `file:line` claims against the
  current code before acting on them (anchors drift). Treat any written plan as a hypothesis, not a fact.

## Repository-Specific Conventions

- **Runtime is pure Python.** Deps: `pyusb`, `pillow`, `psutil` (+ optional `trcc-linux`). No LLM, no
  AI SDK, no generated-runtime dependency; `pip install .` must be enough to run.
- **`src/` layout + `pyproject.toml`.** Entry point: `trofeo = trofeo.__main__:main`.
- **YAML-first config** (`config.yaml`); do not hardcode values; add options to config first, then
  load them in `config.py`.
- **`max_frame_bytes = 450000`** everywhere — the encode path must shrink (quality/scale) until the
  JPEG fits; never bypass.
- **Keepalive** is required (panel revert). Default `keepalive_s = 2.0`.
- **Reconnect state machine** lives in `supervise.py`; do not scatter USB lifecycle logic elsewhere.
- **Portability:** the same code runs on macOS (dev) and Linux aarch64 (Raspberry Pi / Orange Pi).
  Linux deployment: `deploy/` ships `systemd` (Restart=on-failure) + `udev` rule. No macOS-only
  native helper is required by our code (`trcc`'s powermetrics helper is optional and macOS-only).
- **Licenses:** our code is **MIT**. `trcc-linux` is **GPL-3.0** — do **not** copy its code into our
  MIT sources; keep it as an external integration (subprocess/installed dependency) to avoid
  copyleft on our package.
- **Probes:** temporary hardware tests (`tools/probe_ly.py`, `tools/probe_fps.py`) are dev tools,
  not shipped in the package.
- License header expected in `.py` files: `SPDX-License-Identifier: MIT`.

## Git / Commit Policy

- **One commit per task.** For every item in the Kilo/KiloCode task list, create a
  **separate commit** whose message describes that task (the message should read
  like the task's description in the task list).
- Commit each task as soon as it is finished, before moving on to the next one.
- Keep a commit focused on one task — do not mix unrelated changes into one commit.
- Match the repo's existing commit style / wording.

## Bug-Analysis Gate (mandatory, before AND after every change)

Never edit a file without first analysing it, and never finish without confirming you introduced no
new bugs. This is a gate, not a style preference.

**Before any change to a file, OUT LOUD:**
- Read the file being changed AND its callers/consumers (don't grep words to conclude — trace the
  data through each hop: signal -> handler -> execute -> service -> render; stop at the first hop you
  haven't read).
- Report findings by severity — **critical** (crash/data-loss/wrong output on the happy path),
  **major** (breaks a supported path, edge conditions), **minor** (style/naming/UX) — and say which
  one you're about to affect.

**After the change + tests, again:**
- Re-run the mechanical harness: `tools/quality_check.sh` (ruff + mypy + pytest).
- Run the **semantic bug-hunt subagent** (below) on the changed area; it reports critical/major/minor
  + any regressions. Fix or justify each one before declaring done.
- Confirm explicitly: "no new bugs in <file>; change X tested".

**Semantic bug-hunt subagent prompt (use as-is, replace `<scope>`):**
> `Analyze this changed area for bugs. Be very thorough: open the file(s) and their callers, trace data
> through every hop (signal -> handler -> execute -> service -> render). Report by severity: critical
> (crash / data loss / wrong output on the happy path), major (breaks a supported path), minor
> (style/naming). Then re-check the diff for regressions introduced by the change. Return a concise
> list with file:line evidence and a verdict: CLEAN or issues to fix. Scope: <scope>`

**Verification is by run, not prose.** A phase/change is "done" only when the harness is green and the
subagent verdict is CLEAN (or every issue is fixed/justified). If you defer verification to hardware
"later", say so out loud (that is the progress-theater trap).

## Critical Developer Workflows

- Install: `pip install -e .` (or `pip install .`).
- Run the dashboard client: `trofeo run` (reactive renderer + keepalive + reconnect).
- Run the supervisor: `trofeo supervise` (USB lifecycle around an engine).
- Config: edit `config.yaml`, then run; `trofeo --version`.
- Device test (dev, no app): `python tools/probe_ly.py` (handshake + frame ladder),
  `python tools/probe_fps.py` (measure ceiling).

## Data Flow (practical)

- `config.yaml` → `config.py` (panel size, keepalive, budget, sensor set, theme/video).
- `sensors.py` → metric snapshots → `render.py` dirty-check.
- `render.py` (on change or keepalive) → `canvas.py` compose → JPEG (≤450 KB) → `ly.py` send → USB.
- `supervise.py` wraps the whole loop so a USB drop → `WAIT` → re-open+handshake → resume.

## Reference Files

- USB/protocol + transport: `src/trofeo/ly.py`, `protocol.py`
- Reconnect: `src/trofeo/supervise.py`
- Reactive render + keepalive + budget: `src/trofeo/render.py`
- Config: `src/trofeo/config.py`
- Deploy: `deploy/trofeo-supervisor.service`, `deploy/99-trofeo-lcd.rules`
- Upstream protocol reference (read-only): `Lexonight1/thermalright-trcc-linux`
  (`doc/PROTOCOL_USBLCDNEW.md`, `src/trcc/adapters/device/ly_lcd.py`)