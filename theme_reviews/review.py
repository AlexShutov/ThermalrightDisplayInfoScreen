#!/usr/bin/env python3
"""Review cloud themes on the TRCC panel one by one and record likes.

Loads every cloud theme (``trcc theme cloud-load`` -- downloads from
Thermalright's host and caches under ``data/web/<res>/``), waits ``--seconds``
for you to watch it, then moves on.  While a theme is showing you can type:

    +   like it            -> appended to favorites.json
    s   skip to the next   -> stop waiting for this one
    q   quit now           -> save favorites.json and exit

Anything else is ignored; the window always auto-advances after ``--seconds``.
Running again resumes at ``--start <id>`` (default: the first theme), and themes
already in favorites.json are still shown so you can change your mind; ``--liked``
skips them instead.

``favorites.json`` is written atomically (temp + ``os.replace``) after every like,
so a crash or Ctrl-C never loses your list.  It lives next to this script.

This is a dev/review tool -- not part of the shipped MIT package.
"""

# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import select
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_LIST = os.path.join(HERE, "cloud_themes_raw.txt")
FAVORITES = os.path.join(HERE, "favorites.json")

_VENV_TRCC = os.path.join(HERE, "..", ".venv-trcc", "bin", "trcc")
TRCC = os.environ.get("TRCC", _VENV_TRCC if os.path.exists(_VENV_TRCC) else "trcc")
FFMPEG = os.environ.get("FFMPEG", "/opt/homebrew/bin/ffmpeg")

_ID_RE = re.compile(r"^\s*([a-zA-Z]\d{3,4})\s+(.+?)\s*$")


def load_themes(raw_path: str) -> list[tuple[str, str]]:
    """Return ``[(id, category), ...]``; fall back to ``cloud-list`` if needed."""
    lines: list[str]
    if os.path.exists(raw_path):
        with open(raw_path) as fh:
            lines = fh.read().splitlines()
    else:
        lines = subprocess.run(
            [TRCC, "theme", "cloud-list"], capture_output=True, text=True,
            check=False,
        ).stdout.splitlines()
    themes: list[tuple[str, str]] = []
    for line in lines:
        m = _ID_RE.match(line)
        if m:
            themes.append((m.group(1), m.group(2)))
    return themes


def filter_panel_themes(
    themes: list[tuple[str, str]], thumb_dir: str
) -> list[tuple[str, str]]:
    """Keep only themes loadable on the attached 1920x440 panel.

    ``cloud-list`` returns the *whole* hosted catalog across every display size;
    only the 1920x440 panel-compatible themes ship a preview PNG under
    ``data/web/4401920/<id>.png`` (measured: 220 previews == exactly the 220
    whose video returns HTTP 200; the other 78 are for a 320x320 square display
    and 404 on both mirrors).  A theme without a preview therefore has no video
    at the panel resolution and can never be loaded onto this panel, so it is
    dropped here.
    """
    return [
        (tid, cat) for tid, cat in themes
        if os.path.exists(os.path.join(thumb_dir, f"{tid}.png"))
    ]


def load_favorites(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        try:
            return list(json.load(fh).get("favorites", []))
        except (ValueError, OSError):
            return []


def save_favorites(path: str, favorites: list[str], themes: dict[str, str]) -> None:
    """Atomic write, merging with whatever is already on disk.

    The script may be driven externally (the assistant records ``+`` directly
    into ``favorites.json``); merge-on-save means a mid-run save never drops a
    like recorded outside this process.  On-disk entries come first.
    """
    on_disk = load_favorites(path)
    merged: list[str] = []
    seen: set[str] = set()
    for tid in list(on_disk) + list(favorites):
        if tid not in seen:
            merged.append(tid)
            seen.add(tid)
    data = {"favorites": merged, "themes": {tid: themes.get(tid) for tid in merged}}
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def set_dev_brightness(device: str, pct: int) -> None:
    """Set native panel brightness to clamp(*pct*, 0..100).  Never raises."""
    try:
        pct = max(0, min(100, int(pct)))
    except (TypeError, ValueError):
        pct = 100
    with contextlib.suppress(Exception):
        subprocess.run(
            [TRCC, "display", "set-brightness", device, str(pct)],
            capture_output=True, text=True, check=False,
        )


def encode_brightness_video(src: str, dst: str, pct: int) -> bool:
    """Encode *src* with ffmpeg ``eq=brightness`` so *pct* dims/brightens it.

    Returns True on success; never touches *src* and never raises.  A partial
    *dst* is removed on failure.
    """
    v = (int(pct) - 100) / 100.0
    for codec in ("h264_videotoolbox", "libx264"):
        args = [
            FFMPEG, "-y", "-i", src,
            "-vf", f"eq=brightness={v:.6f}",
            "-c:v", codec, "-b:v", "1000k",
            "-loglevel", "error", dst,
        ]
        try:
            r = subprocess.run(args, capture_output=True, text=True, check=False)
        except Exception:
            r = None
        if r is not None and r.returncode == 0 and os.path.exists(dst):
            return True
        with contextlib.suppress(OSError):
            os.remove(dst)
    return False


def load_local_video(device: str, path: str) -> bool:
    """Play ``trcc display load-video`` and report success.  Never raises."""
    try:
        r = subprocess.run(
            [TRCC, "display", "load-video", device, path],
            capture_output=True, text=True, check=False,
        )
        return bool(r and r.returncode == 0)
    except Exception:
        return False


def load_theme(device: str, theme_id: str) -> bool:
    """Load/play a cloud theme; return True on success (one retry)."""
    for attempt in (1, 2):
        r = subprocess.run(
            [TRCC, "theme", "cloud-load", device, theme_id],
            capture_output=True, text=True, check=False,
        )
        if r.returncode == 0 and "playing" in r.stdout:
            return True
        if attempt == 1 and "playback loaded" not in r.stdout:
            # e.g. transient daemon-busy timeout; wait and retry once.
            time.sleep(1.5)
    return False


def wait_for_choice(seconds: float) -> str | None:
    """Block up to ``seconds``, returning '+'/'s'/'q' or None on timeout."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not sys.stdin.isatty() or select.select([sys.stdin], [], [], 0.15)[0]:
            ch = sys.stdin.read(1)
            if not ch:
                time.sleep(0.15)
                continue
            if ch in "+sq\n\r":
                return ch.strip() or None
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Review cloud themes on the panel.")
    ap.add_argument("--key", default="0416:5408", help="device key")
    ap.add_argument("--seconds", type=float, default=20.0, help="watch window per theme")
    ap.add_argument("--start", default=None, help="resume at this theme id")
    ap.add_argument("--liked-only", action="store_true",
                    help="only show themes already in favorites")
    ap.add_argument("--skip-liked", action="store_true",
                    help="skip themes already in favorites")
    args = ap.parse_args()

    themes = load_themes(RAW_LIST)
    if not themes:
        print("no cloud themes found", file=sys.stderr)
        return 1
    favorites = load_favorites(FAVORITES)
    fav_set = set(favorites)
    meta = {tid: cat for tid, cat in themes}

    start_at = 0
    if args.start:
        for i, (tid, _) in enumerate(themes):
            if tid == args.start:
                start_at = i
                break

    for i, (tid, cat) in enumerate(themes[start_at:], start=start_at):
        if args.liked_only and tid not in fav_set:
            continue
        if args.skip_liked and tid in fav_set:
            continue
        ok = load_theme(args.key, tid)
        state = "liked" if tid in fav_set else "new"
        print(f"[{i + 1}/{len(themes)}] {tid} -- {cat} ({state}; {args.seconds:.0f}s)", flush=True)
        if not ok:
            print(f"   !! cloud-load failed for {tid}; skipping", flush=True)
            continue
        choice = wait_for_choice(args.seconds)
        if choice == "+":
            if tid not in fav_set:
                favorites.append(tid)
                fav_set.add(tid)
            save_favorites(FAVORITES, favorites, meta)
            print(f"   + liked {tid} -> favorites.json", flush=True)
        elif choice == "s":
            print(f"   skipped {tid}", flush=True)
        elif choice == "q":
            save_favorites(FAVORITES, favorites, meta)
            print(f"   quit; saved {len(favorites)} favorite(s) -> favorites.json", flush=True)
            return 0

    save_favorites(FAVORITES, favorites, meta)
    print(f"done; {len(favorites)} favorite(s) -> {FAVORITES}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
