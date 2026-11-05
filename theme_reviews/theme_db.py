#!/usr/bin/env python3
"""App-side SQLite metadata + download queue for the TRCC cloud themes.

Themes are downloaded+analysed by ``build_db.py`` into ``themes/<id>/`` and the
result (dark/light, flash, relative path, stats) is recorded in ``themes.db``.
This module is the read/queue layer the GUI uses: it classifies a theme, knows
whether its media is already on disk, and can bump a theme to the front of the
downloader queue.  Pure stdlib (``sqlite3``) so it stays portable.

Dev/review tool (MIT) -- not part of the shipped ``src/hardwarescreen`` package.
"""

# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
THEMES_ROOT = os.path.join(HERE, "themes")
DB_PATH = os.path.join(HERE, "themes.db")
RES = "1920x440"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS themes (
    id TEXT PRIMARY KEY,
    category TEXT,
    rel_path TEXT,
    is_dark INTEGER,
    has_flash INTEGER,
    median_luma REAL,
    peak_luma REAL,
    p99_luma REAL,
    bright_frac REAL,
    flash_frac REAL,
    duration_s REAL,
    fps REAL,
    size_bytes INTEGER,
    status TEXT,
    error TEXT,
    downloaded_at TEXT,
    analyzed_at TEXT,
    updated_at TEXT,
    brightness INTEGER DEFAULT 100
);
CREATE TABLE IF NOT EXISTS queue (
    tid TEXT PRIMARY KEY,
    priority REAL,
    status TEXT DEFAULT 'pending'
);
"""


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    _migrate_brightness(conn)
    conn.commit()


def _migrate_brightness(conn: sqlite3.Connection) -> None:
    """Add the ``brightness`` column to existing DBs (idempotent)."""
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(themes)")}
        if "brightness" not in cols:
            conn.execute("ALTER TABLE themes ADD COLUMN brightness INTEGER DEFAULT 100")
    except sqlite3.Error:
        pass


def media_rel_path(tid: str) -> str:
    """Relative path (to ``HERE``) of the downloaded media for *tid*."""
    return os.path.join("themes", tid, f"{tid}.mp4")


def load_meta() -> dict[str, dict]:
    """Return ``{tid: row-dict}`` for every analysed theme; empty on any error."""
    try:
        conn = connect()
    except sqlite3.Error:
        return {}
    try:
        rows = conn.execute("SELECT * FROM themes").fetchall()
        return {r["id"]: dict(r) for r in rows}
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def is_ready(row: dict, theme_root: str = THEMES_ROOT) -> bool:
    """True if *row* is marked ok and its media really exists on disk."""
    try:
        status = row["status"]
        rel = row["rel_path"]
    except (KeyError, IndexError):
        return False
    if status != "ok" or not rel:
        return False
    # rel_path is 'themes/<id>/<id>.mp4', THEMES_ROOT is '<HERE>/themes',
    # so the file lives at dirname(THEMES_ROOT)/rel_path.
    return os.path.exists(os.path.join(os.path.dirname(theme_root), rel))


def get_brightness(tid: str) -> int:
    """Saved brightness (0..150) for *tid*, or 100 when missing/None.  Never raises."""
    try:
        conn = connect()
    except sqlite3.Error:
        return 100
    try:
        row = conn.execute(
            "SELECT brightness FROM themes WHERE id=?", (tid,)
        ).fetchone()
    except sqlite3.Error:
        return 100
    finally:
        conn.close()
    if row is None or row["brightness"] is None:
        return 100
    return int(row["brightness"])


def set_brightness(tid: str, pct: int) -> None:
    """Upsert a brightness-only row for *tid*.  Never raises."""
    try:
        conn = connect()
    except sqlite3.Error:
        return
    try:
        conn.execute(
            "INSERT INTO themes (id, brightness) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET brightness=excluded.brightness",
            (tid, int(pct)),
        )
        conn.commit()
    except sqlite3.Error:
        pass
    finally:
        conn.close()


def bump_queue(tid: str, front: bool = True) -> None:
    """Move *tid* to the head (or foot) of the download queue.  Never raises."""
    try:
        conn = connect()
    except sqlite3.Error:
        return
    try:
        if front:
            maxp = conn.execute("SELECT COALESCE(MAX(priority), 0) FROM queue").fetchone()[0]
            conn.execute(
                "INSERT INTO queue (tid, priority, status) VALUES (?, ?, 'pending') "
                "ON CONFLICT(tid) DO UPDATE SET priority=excluded.priority, status='pending'",
                (tid, maxp + 1),
            )
        else:
            conn.execute(
                "INSERT INTO queue (tid, priority, status) VALUES (?, 0, 'pending') "
                "ON CONFLICT(tid) DO NOTHING",
                (tid,),
            )
        conn.commit()
    except sqlite3.Error:
        pass
    finally:
        conn.close()
