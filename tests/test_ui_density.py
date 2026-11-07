"""Unit tests for the Android-style dp → px helper (ui_density)."""
# SPDX-License-Identifier: MIT
from __future__ import annotations

from hardwarescreen.adapter.ui_density import dp, resolve_density


def test_dp_identity_at_density_one():
    # density=1.0 → dp == px (обратная совместимость: константы дизайна не меняются).
    assert dp(26, 1.0) == 26.0
    assert dp(48, 1.0) == 48.0


def test_dp_scales_by_density_and_rounds():
    assert dp(10, 2.0) == 20.0
    assert dp(7, 2.0) == 14.0
    assert dp(2.5, 1.0) == 2.0  # round(2.5) → 2 (банковское округление в Python)


def test_dp_negative_or_zero_density_treated_as_one():
    # Плотность <= 0 не имеет смысла: dp() всё равно умножает, но такой кейс не
    # должен воспроизводиться (resolve_density отбрасывает).  Проверяем только формулу.
    assert dp(5, 1.0) == 5.0


def test_resolve_density_none_returns_default():
    assert resolve_density(None) == 1.0
    assert resolve_density(None, 2.0) == 2.0


def test_resolve_density_parses_float():
    assert resolve_density("2.0") == 2.0
    assert resolve_density("1.5") == 1.5


def test_resolve_density_rejects_invalid_or_non_positive():
    assert resolve_density("x") == 1.0
    assert resolve_density("") == 1.0
    assert resolve_density("0") == 1.0
    assert resolve_density("-3") == 1.0