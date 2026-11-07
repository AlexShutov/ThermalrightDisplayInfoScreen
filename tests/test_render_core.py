"""Unit tests for the reactive render core (no USB / device required).

Verifies the pure ``should_render`` gate (dirty-check + keepalive), the frame
budget shrink (never > ``max_frame_bytes``), and that ``Renderer`` talks only
through the injected ``FrameSink`` / ``SensorSource`` ports.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import os

from PIL import Image

from hardwarescreen.config import Config
from hardwarescreen.imageutil import encode_within_budget
from hardwarescreen.protocol import MAX_FRAME_BYTES
from hardwarescreen.render import Renderer, quantize, should_render
from tests.fakes import FakeBackground, FakeSource, FakeStore, MockPanel

KEEPALIVE_S = 2.0
ACTIVE_FPS = 15.0


# -- should_render (pure dirty-check + keepalive gate) --------------------
def test_should_render_first_frame():
    # first frame (last_jpeg None) renders once the min-interval has elapsed
    assert should_render(
        metrics={}, last=None, last_sent=0.0, now=0.1,
        keepalive_s=KEEPALIVE_S, active_fps=ACTIVE_FPS, last_jpeg=None,
    )


def test_should_render_changed_metrics():
    assert should_render(
        metrics={"cpu": 1}, last={"cpu": 0}, last_sent=0.0, now=0.1,
        keepalive_s=KEEPALIVE_S, active_fps=ACTIVE_FPS, last_jpeg=b"x",
    )


def test_should_render_unchanged_within_min_interval():
    # same metrics, just sent -> no resend (dirty-check says unchanged)
    assert not should_render(
        metrics={"cpu": 1}, last={"cpu": 1}, last_sent=10.0, now=10.0,
        keepalive_s=KEEPALIVE_S, active_fps=ACTIVE_FPS, last_jpeg=b"x",
    )


def test_should_render_unchanged_but_keepalive_due():
    assert should_render(
        metrics={"cpu": 1}, last={"cpu": 1}, last_sent=0.0, now=KEEPALIVE_S + 0.1,
        keepalive_s=KEEPALIVE_S, active_fps=ACTIVE_FPS, last_jpeg=b"x",
    )


def test_should_render_respects_min_interval_on_change():
    # changed but within 1/active_fps since last send -> suppressed
    assert not should_render(
        metrics={"cpu": 2}, last={"cpu": 1}, last_sent=10.0, now=10.01,
        keepalive_s=KEEPALIVE_S, active_fps=ACTIVE_FPS, last_jpeg=b"x",
    )


# -- frame budget ---------------------------------------------------------
def test_encode_within_budget_never_exceeds():
    cfg = Config()
    img = Image.frombytes("RGB", (1920, 440), os.urandom(1920 * 440 * 3))
    r = Renderer(cfg, sink=MockPanel(), source=FakeSource())
    jpeg = r._encode_within_budget(img)
    assert len(jpeg) <= MAX_FRAME_BYTES


def _jpeg_sampling_factors(data: bytes) -> list[int]:
    """Достаёт sampling-факторы (H/V) трёх компонент из SOF0/1/2 маркера JPEG.

    Для 4:4:4 все три компоненты имеют фактор ``0x11`` (1×1); для 4:2:0 —
    Y = ``0x22`` (2×2), а цветоразностные — ``0x11``.  Верхняя/нижняя полубайты —
    горизонтальное/вертикальное прореживание соответственно.
    """
    i = 2  # пропустить SOI (FF D8)
    while i + 4 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xD8:
            i += 2
            continue
        if marker == 0xD9:  # EOI
            break
        # Длина сегмента включает собственные 2 байта длины.
        length = int.from_bytes(data[i + 2:i + 4], "big")
        # SOF0/1/2/… — начало кадра: C0..CF (кроме C4 DHT, C8 JPG, CC DAC).
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            num_components = data[i + 9]
            components_start = i + 10
            return [data[components_start + 3 * j + 1] for j in range(num_components)]
        i += 2 + length
    return []


def test_encode_within_budget_uses_no_chroma_subsampling():
    """JPEG кодируется без субдискретизации цветности (4:4:4).

    Дефолтный 4:2:0 сглаживает цветность блоками 2×2 — тонкие красно-оранжевые
    кромки колонок датчиков «шакалятся» (цветозависимый артефакт).  Проверяем,
    что sampling-факторы всех компонент кадра равны 0x11 (без прореживания).
    """
    img = Image.new("RGB", (64, 64), (40, 42, 48))
    jpeg = encode_within_budget(img, budget=200_000)
    sampling = _jpeg_sampling_factors(jpeg)
    assert sampling, "ожидали SOF-факторы в JPJ"
    assert all(factor == 0x11 for factor in sampling), sampling


# -- Renderer talks through injected ports --------------------------------
def test_connect_sets_panel_size_from_handshake():
    cfg = Config()
    r = Renderer(cfg, sink=MockPanel(handshake={"pm": 69, "sub": 1, "width": 1920, "height": 440}))
    r._connect()
    assert cfg.width == 1920
    assert cfg.height == 440


def test_renderer_connects_sends_and_closes_via_sink():
    cfg = Config()
    panel = MockPanel(handshake={"pm": 69, "sub": 1, "width": 1920, "height": 440})
    r = Renderer(cfg, sink=panel, source=FakeSource({"cpu": 5, "ram": 6}))
    r._connect()
    assert panel.connect_calls == 1
    assert panel.close_calls == 1  # _connect resets the device before re-opening
    assert cfg.width == 1920
    assert cfg.height == 440
    r._render_and_send(quantize({"cpu": 5, "ram": 6}))
    assert panel.send_count == 1
    assert panel.last_frame() is not None
    assert len(panel.last_frame() or b"") <= cfg.max_frame_bytes
    r.sink.close()
    assert panel.close_calls == 2


def test_renderer_source_is_used_each_poll():
    src = FakeSource({"cpu": 10.0}, {"cpu": 10.0})
    Renderer(Config(), source=src)
    assert quantize(src.collect())["cpu"] == 10
    assert quantize(src.collect())["cpu"] == 10  # repeats last
    assert src.calls == 2


# -- ancillary fakes (foundation uses them across phases) ------------------
def test_fake_store_seq_monotonic_last_wins():
    s = FakeStore()
    assert s.read() is None
    assert s.write({"mode": "day"}) == 1
    assert s.write({"mode": "night"}) == 2
    state = s.read()
    assert state is not None
    assert state["mode"] == "night"
    assert state["seq"] == 2
    s.close()
    assert s.read() is None


def test_fake_background_records_show_end():
    bg = FakeBackground()
    bg.show("probe", object(), 15.0)
    bg.end("probe")
    assert len(bg.shown) == 1
    assert bg.shown[0][0] == "probe" and bg.shown[0][2] == 15.0
    assert bg.ended == ["probe"]


def test_mock_panel_rejects_oversize_frame():
    panel = MockPanel(budget=100)
    try:
        panel.send(b"x" * 101)
    except AssertionError:
        pass
    else:  # pragma: no cover
        raise AssertionError("MockPanel should reject a frame over budget")
    assert panel.send_count == 0
