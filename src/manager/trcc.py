"""Optional bridge from the manager to trcc: subprocess theme switcher.

``/theme`` persists the mode in the bridge and, when a ``theme_cmd`` is configured,
runs a CLI to make trcc actually switch the day/night background. The command is a
list template (``{theme}`` -> the mode's theme id); the runner is injected so the
whole switch is testable headless with a recording fake. None configured -> no-op.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence

from .config import Config

RunFn = Callable[[Sequence[str]], object]


class TrccThemeSwitcher:
    def __init__(
        self,
        cfg: Config,
        *,
        run: RunFn | None = None,
        resolver: Callable[[str], str] | None = None,
    ):
        self._cfg = cfg
        self._run = run or _default_run
        # resolver(mode) -> theme id; injectable so a test can pin theme ids.
        self._resolver = resolver or (lambda m: str(cfg.themes.get(m, "")))

    def __call__(self, mode: str) -> None:
        cmd = self._cfg.theme_cmd
        if not cmd:
            return
        theme = self._resolver(mode)
        # Literal "{theme}" substitution (no str.format, so command parts may
        # contain other braces/JSON without raising).
        resolved = [part.replace("{theme}", theme) for part in cmd]
        self._run(resolved)


def _default_run(args: Sequence[str]) -> object:
    return subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
        timeout=10.0,
    )
