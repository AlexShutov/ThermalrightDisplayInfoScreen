#!/usr/bin/env python3
"""Interactive cloud-theme pager for the TRCC panel.

Pages through the hosted theme catalog (``trcc theme cloud-list``) and loads the
current one onto the panel (``trcc theme cloud-load`` -- downloads on demand and
caches under ``data/web/<res>/``).  Shows one theme at a time, prints its name,
lets you page forward/back, like a theme, and resume where you stopped.

Two ways to drive it:

* Interactive (run in a terminal -- the primary mode)::

      .venv-trcc/bin/python theme_reviews/browse.py

  Keys:
      Enter  like the current theme        (added to favorites.json)
      - / n  next theme                    (down-arrow also works)
      p      previous theme                (up-arrow also works)
      ?      show this help
      q      quit (position is remembered)

* Chat / machine-driven (each command loads + prints the name; position is
  persisted, so it resumes where you stopped):

      browse.py --next | --prev | --like | --goto <id> | --show | --list

Position (``review_state.json``) and favorites (``favorites.json``) are written
atomically, so restarting resumes at the last theme you were on.  This is a dev
tool, not part of the shipped MIT package.
"""

# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import termios
import tty

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from review import FAVORITES, HERE, TRCC, load_favorites, load_themes, save_favorites  # noqa: E402

STATE = os.path.join(HERE, "review_state.json")
RAW_LIST = os.path.join(HERE, "cloud_themes_raw.txt")

HELP = (
    "Enter=like  -/n=next  p=prev  ?=help  q=quit\n"
)


def load_state() -> str | None:
    if not os.path.exists(STATE):
        return None
    with open(STATE) as fh:
        try:
            return json.load(fh).get("last")
        except (ValueError, OSError):
            return None


def save_state(last: str) -> None:
    tmp = STATE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"last": last}, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, STATE)


def index_of(themes: list[tuple[str, str]], theme_id: str | None, default: int = -1) -> int:
    if theme_id:
        for i, (tid, _) in enumerate(themes):
            if tid == theme_id:
                return i
    return default


def show(themes: list[tuple[str, str]], i: int, key: str, fav: set[str]) -> str:
    """Load theme index ``i`` onto the panel, print its name, persist position."""
    i = max(0, min(len(themes) - 1, i))
    tid, cat = themes[i]
    r = subprocess.run(
        [TRCC, "theme", "cloud-load", key, tid], capture_output=True, text=True, check=False
    )
    mark = "LIKED" if tid in fav else "new"
    line = f"{tid} · {cat} · [{i + 1}/{len(themes)}] · {mark}"
    if r.returncode != 0 or "playing" not in r.stdout:
        err = (r.stderr.strip().splitlines() or ["?"])[-1]
        line += f"  !! load failed: {err}"
    print(line, flush=True)
    save_state(tid)
    return tid


def interactive(themes: list[tuple[str, str]], key: str) -> None:
    """Raw-key loop: Enter=like, -/n=next, p=prev, q=quit."""
    fav = set(load_favorites(FAVORITES))
    favorites = list(fav)
    meta = dict(themes)
    fd = sys.stdin.fileno()
    if not sys.stdin.isatty():
        print("stdin is not a tty; use --next/--prev/--like instead", file=sys.stderr)
        return
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        print(HELP, end="", flush=True)
        i = index_of(themes, load_state(), -1)
        if i < 0:
            cur = show(themes, 0, key, fav)
        else:
            print(f"{themes[i][0]} · {themes[i][1]} · [{i + 1}/{len(themes)}] · "
                  f"{'LIKED' if themes[i][0] in fav else 'new'}", flush=True)
        while True:
            ch = sys.stdin.read(1)
            if ch in "\r\n":
                cur = load_state() or ""
                idx = index_of(themes, cur, -1)
                if idx < 0:
                    print("no current theme to like", flush=True)
                    continue
                tid, cat = themes[idx]
                if tid not in fav:
                    favorites.append(tid)
                    fav.add(tid)
                save_favorites(FAVORITES, favorites, meta)
                print(f"  + liked {tid} ({cat}) -> favorites.json", flush=True)
                continue
            if ch in "-n":
                i = index_of(themes, load_state(), -1) + 1
                show(themes, i, key, fav)
                continue
            if ch == "p":
                i = index_of(themes, load_state(), -1) - 1
                show(themes, i, key, fav)
                continue
            if ch == "?":
                print(HELP, end="", flush=True)
                continue
            if ch == "q":
                break
            if ch == "\x1b":  # arrow key: read the following two bytes
                rest = sys.stdin.read(2)
                if rest == "[B":
                    show(themes, index_of(themes, load_state(), -1) + 1, key, fav)
                elif rest == "[A":
                    show(themes, index_of(themes, load_state(), -1) - 1, key, fav)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    print("bye", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Browse cloud themes.")
    ap.add_argument("--key", default="0416:5408")
    ap.add_argument("--interactive", action="store_true", help="force key mode")
    ap.add_argument("--next", action="store_true", help="advance to next theme")
    ap.add_argument("--prev", action="store_true", help="go back one theme")
    ap.add_argument("--like", action="store_true", help="like the current theme")
    ap.add_argument("--goto", metavar="ID", help="jump to a theme id")
    ap.add_argument("--show", action="store_true", help="reload+print current theme")
    ap.add_argument("--list", action="store_true", help="list all theme ids")
    args = ap.parse_args()

    themes = load_themes(RAW_LIST)
    if not themes:
        print("no cloud themes found")
        return 1
    meta = dict(themes)
    favorites = load_favorites(FAVORITES)
    fav = set(favorites)
    cur = load_state()
    i = index_of(themes, cur, -1)

    if args.interactive or (sys.stdin.isatty() and not (args.next or args.prev
                                                        or args.like or args.goto or args.show
                                                        or args.list)):
        interactive(themes, args.key)
        return 0

    if args.list:
        for tid, cat in themes:
            print(f"{tid}\t{cat}")
        return 0

    if args.like:
        if i < 0 or not cur:
            print("no current theme to like (browse --next first)")
            return 1
        if cur not in fav:
            favorites.append(cur)
            fav.add(cur)
        save_favorites(FAVORITES, favorites, meta)
        print(f"+ liked {cur} ({meta.get(cur, '')}) -> favorites.json")
        return 0

    if args.prev:
        i -= 1
    elif args.goto:
        gi = index_of(themes, args.goto, -1)
        if gi < 0:
            print(f"no such theme: {args.goto}")
            return 1
        i = gi
    elif args.next or args.show or i < 0:
        i += 1

    show(themes, i, args.key, fav)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
