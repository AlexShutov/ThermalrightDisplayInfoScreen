"""Tests for the theme selection source + reconciler (headless, no trcc/hardware)."""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import sqlite3

from manager.config import Config, ThemeSettings
from manager.theme_source import (
    SettingsDbThemeSource,
    ThemeReconciler,
    ThemeSelection,
    TrccAppliedState,
    TrccThemeApplier,
    _parse_theme_ref,
    build_reconciler,
)

TID = "d046"


def _mk_db(path) -> None:
    """Create a settings-app DB with the current_theme marker, like theme_db."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE themes (id TEXT PRIMARY KEY, brightness INTEGER DEFAULT 100);"
        "CREATE TABLE queue (tid TEXT PRIMARY KEY, priority REAL, status TEXT DEFAULT 'pending');"
        "CREATE TABLE current_theme (id INTEGER PRIMARY KEY CHECK (id=1), tid TEXT NOT NULL, "
        "brightness INTEGER DEFAULT 100, video_path TEXT, updated_at TEXT);"
    )
    conn.execute("INSERT INTO current_theme (id, tid, brightness, video_path) "
                 "VALUES (1, ?, ?, ?)", (TID, 19, None))
    conn.commit()
    conn.close()


def _mk_empty_db(path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE themes (id TEXT PRIMARY KEY, brightness INTEGER DEFAULT 100);"
        "CREATE TABLE queue (tid TEXT PRIMARY KEY, priority REAL, status TEXT DEFAULT 'pending');"
        "CREATE TABLE current_theme (id INTEGER PRIMARY KEY CHECK (id=1), tid TEXT NOT NULL, "
        "brightness INTEGER DEFAULT 100, video_path TEXT, updated_at TEXT);"
    )
    conn.commit()
    conn.close()


def test_source_reads_current_marker(tmp_path):
    db = tmp_path / "themes.db"
    _mk_db(db)
    assert SettingsDbThemeSource(str(db)).current() == ThemeSelection(
        tid=TID, brightness=19, video_path=None
    )


def test_source_falls_back_to_queue_top(tmp_path):
    db = tmp_path / "themes.db"
    _mk_empty_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO queue (tid, priority) VALUES ('a001', 10), ('a009', 99)")
    conn.commit()
    conn.close()
    sel = SettingsDbThemeSource(str(db)).current()
    assert sel is not None and sel.tid == "a009"  # highest priority = most recent


def test_source_missing_db_yields_none(tmp_path):
    assert SettingsDbThemeSource(str(tmp_path / "nope.db")).current() is None


def test_parse_theme_ref_baked_and_plain():
    assert _parse_theme_ref("/x/single-video/a014_brightness_adjusted_19") == ("a014", 19)
    assert _parse_theme_ref("web/1920440/d046.mp4") == ("d046", 100)
    assert _parse_theme_ref(None) is None


def test_trcc_applied_reads_json(tmp_path):
    p = tmp_path / "trcc.json"
    p.write_text(json.dumps({"devices": {"0416:5408": {
        "current_theme": "a014_brightness_adjusted_19",
    }}}))
    # _parse_theme_ref is used by TrccAppliedState too.
    state = TrccAppliedState(str(p))
    assert state.applied("0416:5408") == ("a014", 19)
    assert state.applied("other") is None


def test_trcc_applied_prefers_background_path_over_stale_current_theme(tmp_path):
    # After a cloud-load the played video (background_path) is d046 while the
    # current_theme folder still points at an old single-video; the played one wins.
    p = tmp_path / "trcc.json"
    p.write_text(json.dumps({"devices": {"0416:5408": {
        "current_theme": "single-video/a014_brightness_adjusted_19",
        "background_path": "trcc/data/web/1920440/d046.mp4",
    }}}))
    assert TrccAppliedState(str(p)).applied("0416:5408") == ("d046", 100)


def test_applier_cloud_load_vs_baked(tmp_path):
    raw = tmp_path / "clip.mp4"
    raw.write_bytes(b"x")
    commands: list = []

    def run(args):
        commands.append(list(args))

    applier = TrccThemeApplier("0416:5408", trcc_cmd=("trcc",), run=run)
    applier.apply(ThemeSelection("d046", 100))
    applier.apply(ThemeSelection("d046", 19, str(raw)))
    assert commands == [
        ["trcc", "theme", "cloud-load", "0416:5408", "d046"],
        ["trcc", "display", "load-video", "0416:5408", str(raw)],
    ]


class _RecordingApplier:
    def __init__(self) -> None:
        self.selections: list[ThemeSelection] = []

    def apply(self, sel: ThemeSelection) -> bool:
        self.selections.append(sel)
        return True


class _StaticApplied:
    def __init__(self, value):
        self.value = value

    def applied(self, key: str):
        return self.value


class _StaticSource:
    def __init__(self, sel):
        self.sel = sel

    def current(self):
        return self.sel


def test_reconciler_applies_on_stale_panel():
    applier = _RecordingApplier()
    source = _StaticSource(ThemeSelection("d046", 100))
    applied = _StaticApplied(("a014", 19))  # panel shows an old theme
    rec = ThemeReconciler(source, applied, applier, device_key="0416:5408",
                          cooldown_s=0.0)
    assert rec.step(force=True) is True
    assert applier.selections == [ThemeSelection("d046", 100)]


def test_reconciler_reapplies_after_daemon_restart():
    state = {"applied": ("a014", 19)}

    class _Applied(_StaticApplied):
        def applied(self, key):
            return state["applied"]

    applier = _RecordingApplier()
    elapsed = [0.0]
    clock = lambda: elapsed[0]  # noqa: E731
    source = _StaticSource(ThemeSelection("d046", 100))
    rec = ThemeReconciler(source, _Applied(("a014", 19)), applier,
                          device_key="0416:5408", clock=clock, cooldown_s=0.0)
    assert rec.step() is True
    state["applied"] = ("d046", 100)
    assert rec.step() is False  # in sync: nothing to do

    # Daemon restart resets the panel to a stale default -> re-apply.
    elapsed[0] = 1.0
    state["applied"] = ("d037", 100)
    assert rec.step() is True
    assert applier.selections[-1] == ThemeSelection("d046", 100)


def test_reconciler_respects_cooldown():
    class _Applied(_StaticApplied):
        def applied(self, key):
            return ("a014", 19)

    applier = _RecordingApplier()
    elapsed = [0.0]
    clock = lambda: elapsed[0]  # noqa: E731
    source = _StaticSource(ThemeSelection("d046", 100))
    rec = ThemeReconciler(source, _Applied(("a014", 19)), applier, device_key="x",
                          clock=clock, cooldown_s=5.0)
    assert rec.step() is True
    elapsed[0] = 2.0
    assert rec.step() is False  # within cooldown: no busy re-apply loop
    elapsed[0] = 6.0
    assert rec.step() is True  # cooldown elapsed -> re-apply


def test_no_verification_only_applies_on_change():
    class _NoVerification:
        def applied(self, key):
            return None  # trcc.json unavailable -> trust the last apply

    applier = _RecordingApplier()
    source = _StaticSource(ThemeSelection("d046", 100))
    rec = ThemeReconciler(source, _NoVerification(), applier, device_key="x",
                          cooldown_s=0.0)
    assert rec.step() is True
    assert rec.step() is False  # unchanged, no verification -> no spurious re-apply


def test_build_reconciler_disabled_without_db():
    cfg = Config(theme_settings=ThemeSettings(db_path=None))
    assert build_reconciler(cfg) is None


def test_build_reconciler_composes(tmp_path):
    db = tmp_path / "themes.db"
    _mk_db(db)
    cfg = Config(theme_settings=ThemeSettings(
        db_path=str(db),
        trcc_json_path=str(tmp_path / "trcc.json"),
        trcc_cmd=["trcc"],
        device_key="0416:5408",
    ))
    rec = build_reconciler(cfg)
    assert rec is not None and rec.last_signature is None


def test_build_reconciler_issues_cloud_load_from_real_db(tmp_path):
    db = tmp_path / "themes.db"
    _mk_db(db)  # marker d046, brightness 19, no baked path
    jsonp = tmp_path / "trcc.json"
    jsonp.write_text(json.dumps({"devices": {"0416:5408": {
        "current_theme": "a014",
    }}}))
    commands: list = []

    def run(args):
        commands.append(list(args))

    cfg = Config(theme_settings=ThemeSettings(
        db_path=str(db), trcc_json_path=str(jsonp), device_key="0416:5408",
        trcc_cmd=["trcc"], apply_cooldown_s=0.0,
    ))
    rec = build_reconciler(cfg, run=run)
    assert rec is not None
    assert rec.step(force=True) is True
    assert commands == [["trcc", "theme", "cloud-load", "0416:5408", "d046"]]


def test_config_loads_theme_settings_from_yaml(tmp_path):
    yml = tmp_path / "m.yaml"
    yml.write_text(
        "theme_settings:\n  db_path: /tmp/x/themes.db\n  device_key: a1\n"
    )
    cfg = Config.load(str(yml))
    assert cfg.theme_settings.db_path == "/tmp/x/themes.db"
    assert cfg.theme_settings.device_key == "a1"
    assert cfg.theme_settings.trcc_json_path is None
