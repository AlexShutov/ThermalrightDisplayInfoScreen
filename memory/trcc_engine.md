# trcc_engine.md — trcc-linux engine + cloud theme facts

## Engine (verified 9.12.0 on macOS dev)
- Detects `0416:5408`, connects as **Trofeo Vision 11.3, 1920x440, model_id 69** — cross-validates
  our handshake/PM=69. `display load-image` sends frames (52387 bytes observed).
- The persistent engine is **`trcc daemon`** (owns USB, serves IPC socket, renders + keeps the panel
  updated). One-shot `display play|load-image|serve` are triggers that auto-spawn the daemon when none
  is running; NOT standalone long-running processes.
- We still layer `supervise.py` on top so USB reconnect is our responsibility.
- Daemon hang: re-fetching a poisoned cache (e.g. `e005.mp4` cached as 404 HTML) can stall; retry
  `display play` recovers.

## Cloud themes (background MP4s)
- Catalog library for this panel = `bj1920440` (resolution library `1920440`).
  Base URLs: China `http://www.czhorde.com/tr/bj1920440/`, Intl `www.czhorde.cc`.
  Video URL `{base}{id}.mp4`; directory listing blocked (IIS 403.14).
- Scan of catalog (298 ids): **210 real themes** (Gallery 50, Tech 20, HUD 50, Light 40, Nature 50);
  78 = 404; Aesthetic `y*` are ~3 KB placeholders (not real).
- Cloud video playback needs **ffmpeg** (not QtMultimedia); local themes need **7z** (p7zip).
- Preview thumbnails extracted from vendor archive `web/1920440.7z` -> `/tmp/webpreviews/*.png` (120x120).

## Recognized real theme ids seen on panel
c001, a026, c066, d037, d036 (night), d029 (night). **Day target `d037`, night target `d029`.**

## Verdict on Raspberry Pi hardware
- Pi 3 Model B (4xA53 1.2GHz, 1GB, 4xUSB): good "golden middle" — no OTG issue, 1GB RAM.
- Pi 5 (4xA76 2.4GHz, 4/8GB): overqualified, production target (present, use it).
- Orange Pi 6 Plus (RK3588): future target (not arrived).
- Pi Zero 2 W (512MB): borderline, experimental only; needs HW decode, low fps, gpu_mem.
- Pi Zero (single-core): NOT supported.
- Dev + runtime today = **macOS** (Trofeo is USB here); port to Pi later is config-only.