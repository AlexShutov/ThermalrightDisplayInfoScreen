"""Theme selection source: the manager keeps the user's last-chosen theme applied.

The theme settings app (with the brightness slider) persists its current choice in
a single-row ``current_theme`` marker inside ``themes.db``.  This module reads that
marker, verifies against trcc's own ``trcc.json`` what is actually on the panel, and
re-applies the choice when the two diverge (e.g. after a daemon restart).  A fallback
reads the top of the download ``queue`` so a DB written before the marker exists still
yields the most recently browsed theme.

Everything is injectable (the subprocess runner, the clock, the paths) so the whole
reconcile loop is testable headless without hardware or trcc.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .config import Config

RunFn = Callable[[Sequence[str]], object]
log = logging.getLogger("manager.theme_source")


@dataclass(frozen=True)
class ThemeSelection:
    """What the user last chose in the settings app."""

    tid: str
    brightness: int = 100
    video_path: str | None = None

    @property
    def is_baked(self) -> bool:
        """True when the choice is a brightness-baked clip (played by path)."""
        return bool(self.video_path)

    @property
    def signature(self) -> tuple[str, int, str | None]:
        """A comparable snapshot: a change means the choice moved."""
        return (self.tid, self.brightness, self.video_path)


class SettingsDbThemeSource:
    """Reads the settings app's ``themes.db`` for the current-theme marker."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def current(self) -> ThemeSelection | None:
        """The persisted choice, or the top of the queue as a fallback.

        Never raises: a missing/corrupt DB simply yields None.
        """
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error:
            return None
        try:
            row = conn.execute(
                "SELECT tid, brightness, video_path FROM current_theme WHERE id=1"
            ).fetchone()
        except sqlite3.Error:
            row = None
        finally:
            conn.close()
        if row and row["tid"]:
            return ThemeSelection(
                tid=str(row["tid"]),
                brightness=int(row["brightness"] or 100),
                video_path=row["video_path"],
            )
        return self._queue_fallback()

    def _queue_fallback(self) -> ThemeSelection | None:
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT tid FROM queue ORDER BY priority DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            tid = str(row["tid"])
        except sqlite3.Error:
            return None
        finally:
            conn.close()
        return ThemeSelection(tid=tid, brightness=self._brightness(tid))

    def _brightness(self, tid: str) -> int:
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
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


def default_trcc_json_path() -> str | None:
    """The platform default for trcc's ``trcc.json`` (macOS today), else None.

    Linux (Pi / OrangePi) keeps trcc's config elsewhere; the answer is overridable
    via ``theme_settings.trcc_json_path`` in ``manager.yaml`` so a port is config-only.
    """
    candidate = os.path.expanduser("~/Library/Application Support/trcc/trcc.json")
    return candidate if os.path.exists(candidate) else None


def _parse_theme_ref(ref: object) -> tuple[str, int] | None:
    """Extract ``(tid, brightness)`` from a trcc ``current_theme``/``background_path``.

    Handles ``a014_brightness_adjusted_19`` and plain ids or file names like
    ``d046`` / ``web/1920440/d046.mp4``.  Returns None when unsuable.
    """
    if not ref:
        return None
    name = os.path.basename(os.path.normpath(str(ref)))
    match = re.search(r"_(?:brightness_adjusted)_(\d+)$", name)
    if match:
        return (name[: match.start()], int(match.group(1)))
    tid = re.sub(r"\.(mp4|webm|mkv|gif)$", "", name)
    return (tid, 100) if tid else None


class TrccAppliedState:
    """What trcc reports as actually on the panel (reads ``trcc.json``)."""

    def __init__(self, json_path: str | None) -> None:
        self._json_path = json_path

    def applied(self, device_key: str) -> tuple[str, int] | None:
        """``(tid, brightness)`` trcc currently has for *device_key*; None if unknown."""
        if not self._json_path or not os.path.exists(self._json_path):
            return None
        try:
            with open(self._json_path) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        device = (data.get("devices", {}) or {}).get(device_key, {})
        # ``background_path`` is the video actually played (a cloud theme or a
        # single-video baked clip); ``current_theme`` can lag it (the folder the
        # user last browsed), so the played video wins.
        ref = device.get("background_path") or device.get("current_theme")
        return _parse_theme_ref(ref)


class TrccThemeApplier:
    """Runs the trcc CLI to actually apply a theme (cloud-load or load-video)."""

    def __init__(
        self,
        device_key: str,
        *,
        trcc_cmd: Sequence[str] = ("trcc",),
        run: RunFn | None = None,
    ) -> None:
        self._device_key = device_key
        self._trcc_cmd = tuple(trcc_cmd)
        self._run = run or _default_run

    def apply(self, selection: ThemeSelection) -> bool:
        """Apply *selection*; True when the command ran without raising."""
        cmd = list(self._trcc_cmd)
        if selection.is_baked and selection.video_path and os.path.exists(selection.video_path):
            cmd += ["display", "load-video", self._device_key, selection.video_path]
        else:
            cmd += ["theme", "cloud-load", self._device_key, selection.tid]
        try:
            result = self._run(cmd)
            code = getattr(result, "returncode", None)
            log.info(
                "theme applied: %s%s", " ".join(cmd),
                "" if code is None else f" (rc={code})",
            )
            return True
        except Exception:
            log.warning("theme apply failed: %s", " ".join(cmd))
            return False


class ThemeReconciler:
    """Keeps the panel on the user's last-selected theme.

    Every ``step`` reads the settings-app choice and, when it differs from what
    trcc has applied (or the choice moved), re-applies it — debounced by a cooldown
    so an unreachable daemon does not trigger a busy re-apply loop.
    """

    def __init__(
        self,
        source: SettingsDbThemeSource,
        applied: TrccAppliedState,
        applier: TrccThemeApplier,
        *,
        device_key: str = "0416:5408",
        clock: Callable[[], float] = time.monotonic,
        cooldown_s: float = 5.0,
    ) -> None:
        self._source = source
        self._applied = applied
        self._applier = applier
        self._device_key = device_key
        self._clock = clock
        self._cooldown_s = cooldown_s
        self._last_signature: tuple | None = None
        self._last_apply: float | None = None

    def step(self, *, force: bool = False) -> bool:
        """Reconcile once; True when an apply was issued."""
        selection = self._source.current()
        if selection is None:
            return False
        signature = selection.signature
        actual = self._applied.applied(self._device_key)
        if signature == self._last_signature:
            if force:
                log.debug("reconcile: forced re-assert of %s", signature)
            elif actual is not None:
                if actual == (selection.tid, selection.brightness):
                    log.debug("reconcile: in sync (%s)", signature)
                    return False  # already in sync
            else:
                log.debug("reconcile: no verification, already applied %s", signature)
                return False  # no verification: already applied the last known choice
        if (
            self._last_apply is not None
            and self._clock() - self._last_apply < self._cooldown_s
        ):
            log.debug("reconcile: skipped, within cooldown")
            return False  # debounce: avoid a tight retry loop on a down daemon
        log.info("reconcile: applying %s (panel=%s)", signature, actual)
        if self._applier.apply(selection):
            self._last_signature = signature
            self._last_apply = self._clock()
            return True
        # Apply failed (e.g. daemon down); keep _last_signature so the cooldown
        # prevents a tight retry loop, but the next poll tries again.
        return False

    @property
    def last_signature(self) -> tuple | None:
        """The signature last successfully applied (for tests/inspection)."""
        return self._last_signature


def build_reconciler(cfg: Config, *, run: RunFn | None = None) -> ThemeReconciler | None:
    """Compose the real source/applier/reconciler from *cfg*, or None when disabled."""
    settings = cfg.theme_settings
    if not settings.db_path:
        return None
    db_path = os.path.expanduser(settings.db_path)
    json_path = settings.trcc_json_path or default_trcc_json_path()
    if json_path:
        json_path = os.path.expanduser(json_path)
    source = SettingsDbThemeSource(db_path)
    applied = TrccAppliedState(json_path)
    applier = TrccThemeApplier(settings.device_key, trcc_cmd=settings.trcc_cmd, run=run)
    return ThemeReconciler(
        source,
        applied,
        applier,
        device_key=settings.device_key,
        cooldown_s=settings.apply_cooldown_s,
    )


def _default_run(args: Sequence[str]) -> object:
    return subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        timeout=10.0,
    )
