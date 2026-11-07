"""Unit tests for the atomic state-file StateStore (seq monotonic, no torn write)."""
# SPDX-License-Identifier: MIT
from __future__ import annotations

from manager.store import AtomicStateFile


def test_write_read_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    s = AtomicStateFile(str(p))
    assert s.write({"mode": "day"}) == 1
    state = s.read()
    assert state is not None
    assert state["mode"] == "day"
    assert state["seq"] == 1
    assert "updated_at" in state


def test_seq_monotonic_across_restart(tmp_path):
    state_path = tmp_path / "state.json"
    store = AtomicStateFile(str(state_path))
    assert store.write({"mode": "day"}) == 1
    assert store.write({"mode": "night"}) == 2
    # a fresh store recovers the seq so a consumer never sees "older than before"
    store_restarted = AtomicStateFile(str(state_path))
    assert store_restarted.write({"mode": "day"}) == 3
    assert store_restarted.read()["mode"] == "day"


def test_no_temp_file_left_after_write(tmp_path):
    p = tmp_path / "state.json"
    s = AtomicStateFile(str(p))
    s.write({"mode": "day"})
    assert not (tmp_path / "state.json.tmp").exists()
    assert (tmp_path / "state.json").exists()


def test_read_none_when_missing(tmp_path):
    s = AtomicStateFile(str(tmp_path / "nope.json"))
    assert s.read() is None
    # write on a bad/corrupt file still starts from seq 0 (best-effort recover)
    (tmp_path / "corrupt.json").write_text("not json")
    assert AtomicStateFile(str(tmp_path / "corrupt.json")).read() is None
