# conventions.md — code, licenses, git, repo

## Runtime / code
- Pure Python; deps `pyusb`, `pillow`, `psutil` (+ optional `trcc-linux`). No AI/LLM at build/run;
  `pip install .` must run it.
- `src/` layout, `pyproject.toml`, entry point `trofeo = trofeo.__main__:main`.
- YAML-first config (`config.yaml`); add options to config before loading in `config.py`.
- Keep platform specifics (ffmpeg hwaccel, headless Qt `QT_QPA_PLATFORM=offscreen`, DPI/devicePixelRatio,
  sensors temp branch, Python version, udev/systemd) behind `config.py` so the port is config-only.
- License header in `.py` files: `SPDX-License-Identifier: MIT`.
- Probes (`tools/probe_ly.py`, `tools/probe_fps.py`) are dev tools, not shipped.

## Licenses
- Our code = **MIT**. `trcc-linux` and the **Qt adapter module** = **GPL-3.0**, external integrations
  (subprocess/installed dependency). Never copy their code into our MIT sources.

## Git / commit policy
- One commit per task; message reads like the task's description. Commit each task when finished.
- Manual commands compared to trcc repo's own memory convention (its CLAUDE.md startup/wrap-up loop).

## Repo
- Public: `github.com/AlexShutov/ThermalrightDisplayInfoScreen`.
- Commits: `47ba71c` (trcc engine + daemon), `322765b` (full-res 1920x440 render),
  `b9f3a38` (licenses README), `fd84893` (stub module), `3c24f89` (theme catalog PDF).
- `trcc_themes_catalog.pdf` — WB-style grid of all 210 themes with previews (14 pages), committed.