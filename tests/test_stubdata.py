"""Unit tests for the stub data source (Phase 2) — no manager / no hardware."""
# SPDX-License-Identifier: MIT
from __future__ import annotations

from stubdata.config import Config
from stubdata.producer import Producer, make_gauges


class RecordingClient:
    def __init__(self):
        self.setup_calls: list[list[dict]] = []
        self.updates: list[dict[str, float]] = []
        self.themes: list[str] = []

    def setup(self, gauges: list[dict]) -> None:
        self.setup_calls.append(gauges)

    def update(self, values: dict[str, float]) -> None:
        self.updates.append(values)

    def set_theme(self, mode: str) -> None:
        self.themes.append(mode)


def test_make_gauges_returns_count_and_fields():
    gs = make_gauges(3)
    assert len(gs) == 3
    assert gs[0] == {"id": "cpu", "label": "CPU", "unit": "%"}
    assert gs[1]["id"] == "ram"


def test_producer_start_posts_setup():
    client = RecordingClient()
    p = Producer(Config(num_gauges=4), client)
    p.start()
    assert len(client.setup_calls) == 1
    assert len(client.setup_calls[0]) == 4


def test_step_posts_update_within_range():
    client = RecordingClient()
    cfg = Config(num_gauges=6, value_min=0.0, value_max=100.0, seed=1)
    p = Producer(cfg, client)
    switched = p.step()
    assert switched is False
    assert len(client.updates) == 1
    vals = client.updates[0]
    assert set(vals) == {g["id"] for g in make_gauges(6)}
    assert all(0.0 <= v <= 100.0 for v in vals.values())


def test_switch_probability_one_flips_mode_and_returns_true():
    client = RecordingClient()
    p = Producer(Config(switch_probability=1.0, seed=1), client)
    assert p.step() is True
    assert client.themes == ["night"]
    assert p.mode == "night"
    # flips back on a second step
    assert p.step() is True
    assert client.themes == ["night", "day"]


def test_seeded_sequence_is_reproducible():
    ca, cb = RecordingClient(), RecordingClient()
    a = Producer(Config(seed=7), ca)
    b = Producer(Config(seed=7), cb)
    for _ in range(5):
        a.step()
        b.step()
    assert ca.updates == cb.updates
    assert ca.themes == cb.themes
    assert a.mode == b.mode
