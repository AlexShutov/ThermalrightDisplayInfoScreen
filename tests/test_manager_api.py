"""HTTP contract tests for the Theme Manager (TestClient + fakes, no hardware)."""
# SPDX-License-Identifier: MIT
from __future__ import annotations

from fastapi.testclient import TestClient

from manager.api import create_app
from manager.config import Config
from tests.fakes import FakeStore


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
