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
hardwarescreen run                      # reactive renderer + keepalive + reconnect
hardwarescreen run --config config.yaml # explicit config
hardwarescreen --version
```

Wrap an external engine (ready made Control Center, GPL-3.0, optional):

```bash
pip install .[trcc]             # optional: trcc-linux
hardwarescreen supervise --cmd trcc daemon   # persistent engine (owns USB, renders + keepalive)
```

## Theme Manager (headless, no hardware)

The manager is a local HTTP service that owns the display plane; producers POST
JSON and the theme manager publishes an atomic bridge state that the Qt dashboard
adapter (see `dev/`) reads to draw gauges over the theme. The whole flow below
runs without a panel and without `trcc`.

Install with the dev/quality tooling:

```bash
pip install -e ".[dev]"
```

Run the manager and a stub data source (headless, no hardware):

```bash
hardwarescreen-manager --config manager.yaml   # port 8123, atomic bridge state file
hardwarescreen-stubdata --config stubdata.yaml # feeds gauges + day/night switches
```

On the real device the trcc dashboard module is enabled on the panel — see
`memory/hardware_verification.md` for the on-screen check-list and `memory/`
for the manager/architecture notes.

## Deploy on Linux (Raspberry Pi / Orange Pi)

- Copy `deploy/99-hardwarescreen-lcd.rules` to `/etc/udev/rules.d/` and reload udev.
- Copy `deploy/hardwarescreen-supervisor.service` to `~/.config/systemd/user/` (adjust
  `ExecStart` paths to your venv/project), then:
  `systemctl --user enable --now hardwarescreen-supervisor`.

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

- **This package** is **MIT**.
- **`trcc-linux`** is **GPL-3.0** and is used **only as an external engine** —
  an installed dependency / subprocess launched by `hardwarescreen supervise`. Its code
  is never copied into these sources, so this package stays MIT.

Optional trcc integration (external, GPL-3.0):
[https://github.com/Lexonight1/thermalright-trcc-linux](https://github.com/Lexonight1/thermalright-trcc-linux)