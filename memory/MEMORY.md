# MEMORY.md — index of durable topic files

Read **`SESSION.md` first** (live handoff), then this index, then open the topic files relevant to
the task. The index is a directory, not the detail — each topic has its own file. Durable facts never
go in `SESSION.md`; they belong in a topic file + this index.

Auto-loaded into context. Keep the index <= ~200 lines; detail lives in the linked files.

## Topic files

- **`hardware_protocol.md`** — device/wire facts (VID/PID, handshake, frame chunks, 450 KB ceiling,
  ~18 FPS, revert-to-logo, USB lifecycle + max_frame_bytes rules, USB contention).
- **`trcc_engine.md`** — trcc-linux engine (`trcc daemon`), cloud-theme catalog (210 real themes,
  day `d037` / night `d029`), ffmpeg/7z, previews, Raspberry Pi hardware verdicts.
- **`trcc_module_api.md`** — trcc extension API: BaseModule, FrameSource/background.show/end,
  BackgroundKind, frames.watch, capability gate, install steps, our stub module.
- **`architecture.md`** — Theme Manager decision record: two planes, FastAPI+uvicorn, atomic
  state-file bridge with `seq`, API contract, render lifecycle, Qt adapter (variant A), day/night style.
- **`testing.md`** — testing strategy reused from trcc: injectable ports (FrameSink/SensorSource/
  TrccBackground/StateStore), MockPanel + fake transport, pytest `tests/` + `dev/` smoke harnesses,
  coverage ratchet, verify-by-run.
- **`trcc_borrowed.md`** — catalog of trcc design ideas we adapt (design only, NEVER copied GPL code):
  download/install pipeline, errno-classified retry/backoff, last-command-wins sender, atomic+versioned
  settings, multi-process logging, health/report, daemon/IPC lifecycle, module installer, sensor
  self-guarding, safe file/JSON helpers, FastAPI/pydantic patterns, single-broadcast merge, small utils.
  FOLLOW THESE during development.
- **`conventions.md`** — code/style, licenses (MIT ours / GPL-3.0 external), git/commit policy, repo.
- **`hardware_verification.md`** — on-screen check-list (only with the real panel + trcc daemon):
  composite-over-theme, attach race, 450 KB budget, FPS, day/night without restart, USB contention.
  Each phase is otherwise proven headless via `tools/quality_check.sh`.

## Live state
- **`SESSION.md`** — where we are, decisions of the current run, next step. Rewritten each session.
- Archives of past handoffs: `memory/sessions/` (dated, immutable) — see its `README.md`.

## Hard rules worth repeating here
- Runtime pure Python; deps `pyusb`, `pillow`, `psutil` (+ optional `trcc-linux`). No AI/LLM at run.
- `max_frame_bytes = 450000`; keepalive `keepalive_s = 2.0`; USB lifecycle only in `supervise.py`.
- Our code = **MIT**; `trcc-linux` + Qt adapter module = **GPL-3.0** external (never copy into MIT).
- Platform specifics behind `config.py` so the Pi 5 / Orange Pi 6 Plus port is config-only.