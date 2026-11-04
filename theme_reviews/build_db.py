#!/usr/bin/env python3
"""Download, frame-analyse and index every TRCC cloud theme.

Downloads each theme's media over plain HTTP (two-mirror fallback, no trcc
dependency), analyses it frame-by-frame with ffmpeg's ``signalstats`` to decide
dark/light and whether it contains bright flashes, and records everything in
``themes.db`` (see ``theme_db.py``).  Run on macOS dev now; portable to a
Raspberry Pi later.  Also exposes the download queue that the GUI drives.

Usage::

    build_db.py --all              # fill the whole DB (enqueue + process once)
    build_db.py --queue            # persistent worker (the GUI prioritises it)
    build_db.py --theme a001       # synchronous single-theme fetch (app)
    build_db.py --enqueue a001     # bump a theme to the front of the queue
    build_db.py --status           # print a summary

Dev/review tool (MIT) -- not part of the shipped ``src/trofeo`` package.
"""

# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import contextlib
import os
import re
import sqlite3
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from review import RAW_LIST, TRCC, filter_panel_themes, load_themes  # noqa: E402
from theme_db import THEMES_ROOT, connect, media_rel_path  # noqa: E402

RES = "1920x440"
DIR_KEY = "1920440"
MIRRORS = [
    f"http://www.czhorde.com/tr/bj{DIR_KEY}/",
    f"http://www.czhorde.cc/tr/bj{DIR_KEY}/",
]
THUMB_DIR = os.path.expanduser("~/Library/Application Support/trcc/data/web/4401920")

DARK_LUMA = 110.0      # median luma below this -> dark theme
FLASH_LUMA = 200.0     # a sampled frame at/above this is a bright flash
FLASH_DELTA = 60.0     # AND peak-minus-median at/above this counts as a flash
FLASH_MIN = 150.0      # a flash must also reach at least this luma
SAMPLE_EVERY = 5       # analyse every Nth frame
MAX_BYTES = 64 * 1024 * 1024
UA = "trofeo-theme-db/1.0"
_HTTP_TIMEOUT = 30

_YAVG_RE = re.compile(r"lavfi\.signalstats\.YAVG=([0-9eE.+-]+)")


class ThemeDownloadError(Exception):
    """All mirrors failed to deliver a theme's media."""


class _Missing(Exception):
    """A mirror answered 404 for a theme."""


class _TooLarge(Exception):
    """Media exceeded MAX_BYTES."""


def load_catalog(panel_only: bool = False) -> list[tuple[str, str]]:
    themes = load_themes(RAW_LIST)
    return filter_panel_themes(themes, THUMB_DIR) if panel_only else themes


# --------------------------------------------------------------------------
# Media download (portable, no trcc)
# --------------------------------------------------------------------------


def _fetch(url: str, dest: str) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        resp = urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise _Missing(url) from exc
        raise ThemeDownloadError(f"{url}: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ThemeDownloadError(f"{url}: {exc.reason}") from exc
    part = dest + ".part"
    written = 0
    with contextlib.closing(resp) as r, open(part, "wb") as fh:
        while True:
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_BYTES:
                fh.close()
                os.unlink(part)
                raise _TooLarge(f"{url}: exceeds {MAX_BYTES} bytes")
            fh.write(chunk)
        fh.flush()
        os.fsync(fh.fileno())
    if written == 0:
        os.unlink(part)
        raise ThemeDownloadError(f"{url}: empty response")
    os.replace(part, dest)


def _fetch_png(url: str, dest: str) -> None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: SIM117
            if getattr(resp, "status", 200) == 200:
                data = resp.read(1024 * 1024)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as fh:
                    fh.write(data)
    except Exception:
        pass


def download_media(tid: str) -> str:
    dest_dir = os.path.join(THEMES_ROOT, tid)
    os.makedirs(dest_dir, exist_ok=True)
    mp4_dest = os.path.join(dest_dir, f"{tid}.mp4")
    errors: list[str] = []
    for mirror in MIRRORS:
        url = f"{mirror}{tid}.mp4"
        try:
            _fetch(url, mp4_dest)
        except _Missing as exc:
            errors.append(str(exc))
            continue
        except _TooLarge as exc:
            raise ThemeDownloadError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - try the other mirror
            errors.append(f"{url}: {exc}")
            continue
        _fetch_png(f"{mirror}{tid}.png", os.path.join(dest_dir, f"{tid}.png"))
        return mp4_dest
    raise ThemeDownloadError(f"all mirrors failed for {tid}: {'; '.join(errors)}")


# --------------------------------------------------------------------------
# Frame analysis (ffmpeg signalstats)
# --------------------------------------------------------------------------


def _probe_duration_fps(mp4_path: str, n_samples: int) -> tuple[float, float]:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=duration,r_frame_rate", "-of", "csv=p=0",
             mp4_path],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            parts = proc.stdout.strip().split(",")
            duration = 0.0
            fps = 0.0
            for field in parts:
                field = field.strip()
                if not field or field == "N/A":
                    continue
                if "/" in field:
                    num, _, den = field.partition("/")
                    if den and float(den):
                        fps = float(num) / float(den)
                else:
                    with contextlib.suppress(ValueError):
                        duration = float(field)
            if duration <= 0 and fps > 0 and n_samples:
                duration = n_samples * SAMPLE_EVERY / fps
            return duration, fps
    except Exception:
        pass
    return 0.0, 0.0


def analyze_video(mp4_path: str) -> dict:
    filt = f"select='not(mod(n,{SAMPLE_EVERY}))',signalstats,metadata=print:file=-"
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-i", mp4_path,
         "-vf", filt, "-an", "-f", "null", "-"],
        capture_output=True, text=True, timeout=300, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): "
                           f"{proc.stderr.strip()[-300:]}")
    yavg: list[float] = []
    for line in proc.stdout.splitlines():
        m = _YAVG_RE.search(line)
        if m:
            yavg.append(float(m.group(1)))
    if not yavg:
        raise RuntimeError("no YAVG samples from signalstats")
    yavg = yavg[:2000]
    srt = sorted(yavg)
    n = len(yavg)
    p99 = srt[min(n - 1, int(0.99 * n))]
    peak = max(yavg)
    median = statistics.median(yavg)
    bright = sum(1.0 for v in yavg if v >= FLASH_LUMA)
    flash = sum(1.0 for v in yavg if v >= FLASH_LUMA)
    duration, fps = _probe_duration_fps(mp4_path, n)
    return {
        "median_luma": median,
        "peak_luma": peak,
        "p99_luma": p99,
        "bright_frac": bright / n,
        "flash_frac": flash / n,
        "duration_s": duration,
        "fps": fps,
    }


def classify(stats: dict) -> tuple[bool, bool]:
    is_dark = stats["median_luma"] < DARK_LUMA
    peak = stats["peak_luma"]
    has_flash = (
        peak >= FLASH_LUMA
        or (peak >= FLASH_MIN and (peak - stats["median_luma"]) >= FLASH_DELTA)
    )
    return bool(is_dark), bool(has_flash)


def empty_stats() -> dict:
    return {k: 0.0 for k in (
        "median_luma", "peak_luma", "p99_luma", "bright_frac",
        "flash_frac", "duration_s", "fps",
    )}


# --------------------------------------------------------------------------
# DB writes + queue
# --------------------------------------------------------------------------


def save_theme_row(conn: sqlite3.Connection, tid: str, category: str,
                   rel_path: str, stats: dict, is_dark: bool, has_flash: bool,
                   size_bytes: int, status: str, error_new: str | None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    ts = now if status == "ok" else None
    conn.execute(
        "INSERT INTO themes (id, category, rel_path, is_dark, has_flash, "
        "median_luma, peak_luma, p99_luma, bright_frac, flash_frac, duration_s, "
        "fps, size_bytes, status, error, downloaded_at, analyzed_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "category=excluded.category, rel_path=excluded.rel_path, "
        "is_dark=excluded.is_dark, has_flash=excluded.has_flash, "
        "median_luma=excluded.median_luma, peak_luma=excluded.peak_luma, "
        "p99_luma=excluded.p99_luma, bright_frac=excluded.bright_frac, "
        "flash_frac=excluded.flash_frac, duration_s=excluded.duration_s, "
        "fps=excluded.fps, size_bytes=excluded.size_bytes, status=excluded.status, "
        "error=excluded.error, downloaded_at=excluded.downloaded_at, "
        "analyzed_at=excluded.analyzed_at, updated_at=excluded.updated_at",
        (tid, category, rel_path, int(is_dark), int(has_flash),
         stats["median_luma"], stats["peak_luma"], stats["p99_luma"],
         stats["bright_frac"], stats["flash_frac"], stats["duration_s"],
         stats["fps"], size_bytes, status, error_new, ts, ts, now),
    )


def enqueue_all_missing(conn: sqlite3.Connection, themes: list[tuple[str, str]]) -> None:
    for tid, _cat in themes:
        row = conn.execute("SELECT status FROM themes WHERE id=?", (tid,)).fetchone()
        if row is None or row["status"] != "ok":
            conn.execute(
                "INSERT INTO queue (tid, priority, status) VALUES (?, 0, 'pending') "
                "ON CONFLICT(tid) DO UPDATE SET status='pending'",
                (tid,),
            )
    conn.commit()


def next_not_ok(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT q.tid, t.category FROM queue q "
        "LEFT JOIN themes t ON t.id = q.tid "
        "WHERE q.status = 'pending' AND (t.status IS NULL OR t.status != 'ok') "
        "ORDER BY q.priority DESC LIMIT 1"
    ).fetchone()


def mark_done(conn: sqlite3.Connection, tid: str) -> None:
    conn.execute("UPDATE queue SET status='done' WHERE tid=?", (tid,))
    conn.commit()


def process_one(conn: sqlite3.Connection, tid: str, category: str,
                warm_trcc: bool = False) -> bool:
    try:
        path = download_media(tid)
        size = os.path.getsize(path)
        stats = analyze_video(path)
        is_dark, has_flash = classify(stats)
        save_theme_row(conn, tid, category or "", media_rel_path(tid),
                       stats, is_dark, has_flash, size, "ok", None)
        conn.commit()
        if warm_trcc:
            _warm_trcc(tid)
        return True
    except Exception as exc:  # noqa: BLE001 - record and move on
        save_theme_row(conn, tid, category or "", media_rel_path(tid),
                       empty_stats(), False, False, 0, "error", str(exc))
        with contextlib.suppress(sqlite3.Error):
            conn.commit()
        return False


def _warm_trcc(tid: str) -> None:
    with contextlib.suppress(Exception):
        subprocess.run(
            [TRCC, "theme", "cloud-download", tid, "--resolution", RES],
            capture_output=True, text=True, timeout=120, check=False,
        )


def category_for(conn: sqlite3.Connection, tid: str,
                 catalog: list[tuple[str, str]]) -> str:
    for t, cat in catalog:
        if t == tid:
            return cat
    row = conn.execute("SELECT category FROM themes WHERE id=?", (tid,)).fetchone()
    return row["category"] if row else ""


def show_status(conn: sqlite3.Connection) -> None:
    counts = {r["status"]: r["c"] for r in conn.execute(
        "SELECT status, COUNT(*) c FROM themes GROUP BY status").fetchall()}
    queued = conn.execute(
        "SELECT COUNT(*) c FROM queue WHERE status='pending'").fetchone()["c"]
    print(f"themes: ok={counts.get('ok', 0)} error={counts.get('error', 0)} "
          f"missing={counts.get('missing', 0)} total={sum(counts.values())}")
    print(f"queue: pending={queued}")
    for row in conn.execute(
        "SELECT id, is_dark, has_flash, status FROM themes "
        "WHERE status='ok' ORDER BY id LIMIT 5").fetchall():
        print(f"  {row['id']} dark={bool(row['is_dark'])} "
              f"flash={bool(row['has_flash'])} {row['status']}")


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------


def run_queue(conn: sqlite3.Connection, catalog: list[tuple[str, str]],
              warm_trcc: bool, sleep_s: float, idle_s: float,
              label: str) -> int:
    enqueue_all_missing(conn, catalog)
    total = conn.execute(
        "SELECT COUNT(*) c FROM queue WHERE status='pending'").fetchone()["c"]
    done = 0
    while True:
        item = next_not_ok(conn)
        if not item:
            if label == "all":
                break
            time.sleep(idle_s)
            item = next_not_ok(conn)
            if not item:
                continue
        tid, cat = item["tid"], item["category"]
        t0 = time.monotonic()
        ok = process_one(conn, tid, cat, warm_trcc=warm_trcc)
        mark_done(conn, tid)
        dt = time.monotonic() - t0
        done += 1
        print(f"[{done}/{total}] {tid} {'ok' if ok else 'err'} {dt:.1f}s", flush=True)
        time.sleep(sleep_s)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Download/analyse/index cloud themes.")
    ap.add_argument("--theme", metavar="ID", help="fetch a single theme now")
    ap.add_argument("--enqueue", metavar="ID", help="bump a theme in the queue")
    ap.add_argument("--no-front", action="store_true",
                    help="with --enqueue: append to the queue instead of the head")
    ap.add_argument("--queue", action="store_true", help="run the persistent worker")
    ap.add_argument("--all", action="store_true", help="fill the DB once")
    ap.add_argument("--status", action="store_true", help="print a summary")
    ap.add_argument("--panel-only", action="store_true",
                    help="only panel-compatible themes (skips other displays)")
    ap.add_argument("--warm-trcc", action="store_true",
                    help="also warm the trcc cache (dev, needs trcc)")
    ap.add_argument("--sleep", type=float, default=0.3, help="pause between themes")
    ap.add_argument("--idle", type=float, default=2.0, help="idle poll when queue empty")
    args = ap.parse_args()

    if args.enqueue:
        from theme_db import bump_queue
        bump_queue(args.enqueue, front=not args.no_front)
        print(f"queued {args.enqueue} (front={not args.no_front})", flush=True)
        return 0

    if args.status:
        conn = connect()
        with contextlib.suppress(Exception):
            show_status(conn)
        conn.close()
        return 0

    if args.theme:
        catalog = load_catalog(args.panel_only)
        tid = args.theme
        conn = connect()
        cat = category_for(conn, tid, catalog)
        ok = process_one(conn, tid, cat, warm_trcc=args.warm_trcc)
        mark_done(conn, tid)
        conn.close()
        print(f"{tid}: {'ok' if ok else 'error'}", flush=True)
        return 0 if ok else 1

    catalog = load_catalog(args.panel_only)
    if not catalog:
        print("no cloud themes found", file=sys.stderr)
        return 1
    conn = connect()
    mode = "queue" if args.queue else "all"
    rc = run_queue(conn, catalog, args.warm_trcc, args.sleep, args.idle, mode)
    conn.close()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
