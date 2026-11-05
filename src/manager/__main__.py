"""Console entry point: run the Theme Manager HTTP service (uvicorn)."""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import logging

import uvicorn

from .api import create_app
from .config import Config
from .store import AtomicStateFile


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hardwarescreen-manager")
    ap.add_argument("--config", default="manager.yaml")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--log", default="INFO")
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    cfg = Config.load(args.config)
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port

    store = AtomicStateFile(cfg.state_file)
    app = create_app(cfg, store)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level=args.log.lower())
    return 0
