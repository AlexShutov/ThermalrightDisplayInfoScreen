"""CLI entry point: `trofeo run` (reactive renderer) / `trofeo supervise`."""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import sys

from . import __version__

_CMDS = {"run", "supervise", "preview"}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("--version", "-V"):
        print(f"trofeo {__version__}")
        return 0
    cmd = argv[0]
    rest = argv[1:]
    if cmd == "run":
        from .render import main as run_main
        return run_main(rest)
    if cmd == "supervise":
        from .supervise import main as sup_main
        return sup_main(rest)
    if cmd == "preview":
        from .render import preview
        return preview(rest)
    print(f"unknown command: {cmd!r} (expected one of {sorted(_CMDS)})", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())