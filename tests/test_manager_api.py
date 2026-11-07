"""HTTP contract tests for the Theme Manager (TestClient + fakes, no hardware)."""
# SPDX-License-Identifier: MIT
from __future__ import annotations

from fastapi.testclient import TestClient

from hardwarescreen.adapter.dashboard import compose_dashboard
from manager.api import create_app
from manager.config import Config
from tests.fakes import FakeStore

PALETTE_SIZE = (256, 220)


def _client(cfg: Config | None = None, *, raise_on_switch: bool = False):
    cfg = cfg or Config()
    store = FakeStore()
    switches: list[str] = []

    def rec(mode: str) -> None:
        if raise_on_switch:
            raise RuntimeError("trcc is down")
        switches.append(mode)

    app = create_app(cfg, store, switch_theme=rec, clock=lambda: 1.0)
    return TestClient(app), store, switches


def test_health_initial():
    client, store, _ = _client()
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["mode"] == "day"
    assert body["seq"] == 0
    assert isinstance(body["uptime_s"], (int, float))


def test_health_reports_recovered_seq_after_restart(tmp_path):
    from manager.store import AtomicStateFile

    p = tmp_path / "state.json"
    AtomicStateFile(str(p)).write({"mode": "day"})  # seq 1 persisted
    cfg = Config(state_file=str(p))
    store = AtomicStateFile(str(p))
    client = TestClient(create_app(cfg, store, clock=lambda: 1.0))
    assert client.get("/health").json()["seq"] == 1


def test_setup_stores_gauges_and_returns_seq():
    client, store, _ = _client()
    r = client.post("/dashboard/setup", json={"gauges": [
        {"id": "cpu", "label": "CPU", "unit": "%", "min": 0, "max": 100},
    ]})
    assert r.status_code == 200
    assert r.json()["seq"] == 1
    assert store.read()["setup"][0]["id"] == "cpu"


def test_update_merges_last_wins():
    client, store, _ = _client()
    client.post("/dashboard/setup", json={"gauges": []})
    seq2 = client.post("/dashboard/update", json={"values": {"cpu": 42, "gpu": 70}}).json()["seq"]
    seq3 = client.post("/dashboard/update", json={"values": {"cpu": 50}}).json()["seq"]
    assert (seq2, seq3) == (2, 3)
    assert store.read()["values"] == {"cpu": 50, "gpu": 70}  # last-write-wins, no growth


def test_settings_derives_active_palette_for_current_mode():
    client, _, _ = _client()
    r = client.post("/settings", json={
        "themes": {"day": "d037", "night": "d029"},
        "adapter": {"day": {"background": "#fff"}, "night": {"background": "#000"}},
    })
    assert r.status_code == 200
    assert r.json()["mode"] == "day"
    assert r.json()["palette"]["background"] == "#fff"


def test_settings_palette_reaches_bridge_and_dashboard():
    client, store, _ = _client()
    client.post("/dashboard/setup", json={"gauges": [
        {"id": "cpu", "label": "CPU", "unit": "%", "min": 0, "max": 100},
    ]})
    client.post("/dashboard/update", json={"values": {"cpu": 100}})
    r = client.post("/settings", json={
        "themes": {"day": "d037", "night": "d029"},
        "adapter": {"day": {
            "background": "#f2f3f7", "panel": "#ffffff",
            "accent": "#ff0000", "text": "#1a1a1e", "muted": "#6b7280",
        }},
    })
    assert r.status_code == 200
    bridge = store.read()
    assert bridge["palette"]["accent"] == "#ff0000"
    assert set(bridge) >= {"mode", "active_theme", "palette", "setup", "values"}
    img = compose_dashboard(bridge["setup"], bridge["values"], bridge["palette"], PALETTE_SIZE)
    assert b"\xff\x00\x00" in img.tobytes()


def test_theme_switches_mode_calls_switcher_and_updates_theme():
    client, store, switches = _client()
    r = client.post("/theme", json={"mode": "night"})
    assert r.status_code == 200
    assert r.json()["mode"] == "night"
    assert r.json()["active_theme"] == "d029"
    assert switches == ["night"]
    assert client.get("/health").json()["mode"] == "night"
    assert store.read()["mode"] == "night"


def test_theme_invalid_mode_422():
    client, _, _ = _client()
    assert client.post("/theme", json={"mode": "dusk"}).status_code == 422


def test_body_too_large_413():
    cfg = Config(body_limit_bytes=10)
    client, _, _ = _client(cfg)
    r = client.post("/dashboard/update", json={"values": {"a": "x" * 100}})
    assert r.status_code == 413


def test_auth_required_when_token_set():
    cfg = Config(auth_token="secret")
    client, _, _ = _client(cfg)
    assert client.get("/health").status_code == 401
    assert client.post("/theme", json={"mode": "day"}).status_code == 401
    ok = client.get("/health", headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200


def test_switch_failure_does_not_kill_api_last_state_holds():
    client, store, _ = _client(raise_on_switch=True)
    r = client.post("/theme", json={"mode": "night"})
    assert r.status_code == 200  # a bad trcc call is non-fatal
    assert store.read()["mode"] == "night"  # last state still published
    assert store.read()["seq"] == 1


def test_theme_settings_watcher_applies_on_startup(tmp_path):
    """A configured theme_settings DB drives a startup reconcile (trcc CLI)."""
    import sqlite3

    from manager.config import ThemeSettings

    db = tmp_path / "themes.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE current_theme (id INTEGER PRIMARY KEY CHECK (id=1), "
        "tid TEXT NOT NULL, brightness INTEGER DEFAULT 100, video_path TEXT, "
        "updated_at TEXT);"
        "CREATE TABLE queue (tid TEXT PRIMARY KEY, priority REAL, "
        "status TEXT DEFAULT 'pending');"
        "CREATE TABLE themes (id TEXT PRIMARY KEY, brightness INTEGER DEFAULT 100);"
    )
    conn.execute("INSERT INTO current_theme (id, tid, brightness) VALUES (1, 'd046', 100)")
    conn.commit()
    conn.close()

    trcc_json = tmp_path / "trcc.json"
    trcc_json.write_text('{"devices":{"0416:5408":{"current_theme":"a014_brightness_adjusted_19"}}}')
    commands: list = []

    def run(args):
        commands.append(list(args))

    cfg = Config(theme_settings=ThemeSettings(
        db_path=str(db), trcc_json_path=str(trcc_json), device_key="0416:5408",
        trcc_cmd=["trcc"], poll_s=0.3,
    ))
    store = FakeStore()
    # Entering the client runs the lifespan startup: forced reconcile -> cloud-load.
    with TestClient(create_app(cfg, store, run=run)) as client:
        assert client.get("/health").status_code == 200
        assert ["trcc", "theme", "cloud-load", "0416:5408", "d046"] in commands


def test_restart_restores_setup_and_values_from_bridge(tmp_path):
    """A manager restart must not blank the overlay: setup/values are restored."""
    from manager.store import AtomicStateFile

    p = tmp_path / "state.json"
    AtomicStateFile(str(p)).write({
        "mode": "day",
        "setup": [{"id": "cpu", "label": "CPU", "unit": "%", "min": 0, "max": 100}],
        "values": {"cpu": 42, "fps": 60.0},
    })
    store = AtomicStateFile(str(p))
    client = TestClient(create_app(Config(state_file=str(p)), store, clock=lambda: 1.0))
    client.post("/theme", json={"mode": "night"})  # forces a fresh bridge write
    bridge = store.read()
    assert bridge["setup"] == [{"id": "cpu", "label": "CPU", "unit": "%", "min": 0, "max": 100}]
    assert bridge["values"] == {"cpu": 42, "fps": 60.0}
    assert bridge["mode"] == "night"
