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
  The thin client can drive it, or fall back to `ly.py`. `trcc-linux` also has **no** USB reconnect
  and a frame-size guard to verify — that is why we layer our own supervisor/renderer on top.

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