#!/usr/bin/env python3
"""Warm the trcc cloud-theme cache so the review GUI pages instantly.

``trcc theme cloud-load`` downloads a theme on first use; over a slow link that
takes ~18 s and can blow past the daemon's 30 s IPC timeout, so the panel does
not switch in time.  This script pre-fetches every not-yet-cached theme with
``cloud-download`` (cache-only, idempotent, never touches the panel), filling
``data/web/1920440/`` so the GUI's ``cloud-load`` becomes instant.

Dev tool (MIT), best-effort: a theme that fails is retried once and reported.

Usage:
    .venv-trcc/bin/python theme_reviews/prefetch.py            # fetch all uncached
    .venv-trcc/bin/python theme_reviews/prefetch.py --dry-run  # count uncached
"""

# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from review import RAW_LIST, TRCC, filter_panel_themes, load_themes  # noqa: E402

WEB_DIR = os.path.expanduser("~/Library/Application Support/trcc/data/web/1920440")
THUMB_DIR = os.path.expanduser("~/Library/Application Support/trcc/data/web/4401920")


def is_cached(theme_id: str) -> bool:
    for ext in ("mp4", "gif", "mov", "webm", "mkv", "avi"):
        if os.path.exists(os.path.join(WEB_DIR, f"{theme_id}.{ext}")):
            return True
    return False


def download(theme_id: str, resolution: str) -> bool:
    for _ in range(2):
        r = subprocess.run(
            [TRCC, "theme", "cloud-download", theme_id, "--resolution", resolution],
            capture_output=True, text=True, check=False,
        )
        if r.returncode == 0:
            return True
        time.sleep(0.5)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Warm the cloud-theme cache.")
    ap.add_argument("--resolution", default="1920x440", help="oriented catalog size")
    ap.add_argument("--dry-run", action="store_true", help="only count uncached")
    args = ap.parse_args()

    themes = filter_panel_themes(load_themes(RAW_LIST), THUMB_DIR)
    uncached = [tid for tid, _ in themes if not is_cached(tid)]
    if args.dry_run:
        print(f"{len(themes)} themes total; {len(uncached)} not yet cached; "
              f"{len(themes) - len(uncached)} cached")
        return 0

    print(f"prefetch: {len(uncached)} theme(s) to warm (total {len(themes)})", flush=True)
    for idx, tid in enumerate(uncached, start=1):
        if is_cached(tid):
            continue
        t0 = time.monotonic()
        ok = download(tid, args.resolution)
        dt = time.monotonic() - t0
        print(f"[{idx}/{len(uncached)}] {tid} {'ok' if ok else 'FAILED'} {dt:.1f}s", flush=True)
    print("prefetch done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
