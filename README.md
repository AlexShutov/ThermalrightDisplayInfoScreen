# ThermalrightDisplayInfoScreen

Portable, self-contained dashboard app for the **Thermalright Trofeo Vision 11.3
LCD** (USB `0416:5408`, "LY" bulk protocol) as a system-info screen.

- **Reactive rendering** — sends a frame only when the displayed data changes
  (it is a picture-frame panel).
- **Keepalive** — the panel reverts to its logo after ~2–3 s, so a frame is
  resent at least every `keepalive_s` (default 2 s).
- **USB reconnect** — survives USB/power loss (a car install): the native
  driver re-opens + re-handshakes automatically; a supervisor can also wrap an
  external engine (`trcc-linux`).
- **Portable, no LLM** — plain Python. `pip install .` + a config file is all
  it needs at build/run time. Runs on macOS (dev) and Linux aarch64
  (Raspberry Pi / Orange Pi).

## Install

```bash
pip install -e .        # or: pip install .
```

## Run

```bash
trofeo run                      # reactive renderer + keepalive + reconnect
trofeo run --config config.yaml # explicit config
trofeo --version
```

Wrap an external engine (ready made Control Center, GPL-3.0, optional):

```bash
pip install .[trcc]             # optional: trcc-linux
trofeo supervise --cmd trcc daemon   # persistent engine (owns USB, renders + keepalive)
```

## Deploy on Linux (Raspberry Pi / Orange Pi)

- Copy `deploy/99-trofeo-lcd.rules` to `/etc/udev/rules.d/` and reload udev.
- Copy `deploy/trofeo-supervisor.service` to `~/.config/systemd/user/` (adjust
  `ExecStart` paths to your venv/project), then:
  `systemctl --user enable --now trofeo-supervisor`.

## Hardware facts (see `AGENTS.md`)

- `VID 0x0416 PID 0x5408`, LY bulk; write EP `0x09`, read EP `0x81` (auto-detected).
- Handshake `PM=64+clamp(resp[20])`, `SUB=resp[22]+1`; `PM==69` → 11.3.
- Frame: JPEG ≤ **450000 bytes** (above ~450 KB the panel silently drops).
- Ceiling ~18 FPS (write+ACK ≈ 55 ms).

## Dev tools

```bash
python tools/probe_ly.py       # handshake + frame-size ladder
python tools/probe_fps.py      # measure FPS ceiling
```

## Licenses

This package is **MIT**. `trcc-linux` is **GPL-3.0** — integrate it as an
external engine (installed dependency / subprocess), do not copy its code into
these sources.