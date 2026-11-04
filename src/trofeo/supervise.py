"""USB-lifetime supervisor: wrap an external engine (e.g. trcc-linux) so the
panel recovers after USB loss / power interruption in the car.

State machine: WAIT (device gone) <-> RUN (engine process alive). Poll 1s,
backoff, handles SIGINT/SIGTERM. The supervisor stays alive across USB-churn;
systemd only restarts it if the supervisor itself crashes.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import contextlib
import logging
import signal
import subprocess
import sys
import time

import usb.core

from . import PID, VID

LOG = logging.getLogger("trofeo.supervise")

POLL_S = 1.0
STOP_TIMEOUT_S = 5


def device_present() -> bool:
    return usb.core.find(idVendor=VID, idProduct=PID) is not None


class Engine:
    def __init__(self, cmd: list[str]):
        self.cmd = cmd
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        LOG.info("starting engine: %s", " ".join(self.cmd))
        self.proc = subprocess.Popen(self.cmd, stdout=sys.stdout, stderr=sys.stderr)

    def stop(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            if self.proc.stdin:
                with contextlib.suppress(Exception):
                    self.proc.stdin.close()
            self.proc.terminate()
            try:
                self.proc.wait(timeout=STOP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=STOP_TIMEOUT_S)
        self.proc = None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="trofeo supervise")
    ap.add_argument("--cmd", nargs="+", required=True,
                    help="engine command to run (e.g. 'trcc display serve')")
    ap.add_argument("--poll", type=float, default=POLL_S)
    ap.add_argument("--log", default="INFO")
    args = ap.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    engine = Engine(args.cmd)

    def on_signal(signum, _frame):
        LOG.info("signal %s, shutting down", signum)
        engine.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    LOG.info("supervisor up: watching %04x:%04x", VID, PID)
    while True:
        if device_present():
            if not engine.alive():
                engine.start()
        else:
            if engine.alive():
                engine.stop()
        time.sleep(args.poll)
