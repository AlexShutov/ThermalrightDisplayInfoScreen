"""Tests for the injectable trcc theme switcher (no subprocess / no trcc)."""
# SPDX-License-Identifier: MIT
from __future__ import annotations

from manager.config import Config
from manager.trcc import TrccThemeSwitcher


def test_switcher_resolves_theme_and_runs_command():
    cfg = Config(theme_cmd=["trcc", "display", "play", "{theme}"])
    calls: list[list[str]] = []

    def fake_run(args):
        calls.append(list(args))
        return 0

    TrccThemeSwitcher(cfg, run=fake_run)("night")
    assert calls == [["trcc", "display", "play", "d029"]]


def test_switcher_noop_without_cmd():
    cfg = Config(theme_cmd=None)
    ran = []

    def fake_run(args):
        ran.append(list(args))

    TrccThemeSwitcher(cfg, run=fake_run)("day")
    assert ran == []


def test_switcher_custom_resolver():
    cfg = Config(theme_cmd=["trcc", "theme", "{theme}"])
    calls: list[list[str]] = []

    def fake_run(args):
        calls.append(list(args))

    TrccThemeSwitcher(cfg, run=fake_run, resolver=lambda m: "T-" + m.upper())("night")
    assert calls == [["trcc", "theme", "T-NIGHT"]]


def test_switcher_replaces_literal_placeholder_with_braces():
    # A command part with extra braces must not break substitution (no str.format).
    cfg = Config(theme_cmd=["trcc", "body={theme}", '{"k":"v"}'])
    calls: list[list[str]] = []

    def fake_run(args):
        calls.append(list(args))

    TrccThemeSwitcher(cfg, run=fake_run)("day")
    assert calls == [["trcc", "body=d037", '{"k":"v"}']]
