"""Console entry point: run the stub data source against the manager."""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import logging

from .client import HttpManagerClient
from .config import Config
from .producer import Producer


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hardwarescreen-stubdata")
    ap.add_argument("--config", default="stubdata.yaml")
    ap.add_argument("--manager-url", default=None)
    ap.add_argument("--poll", type=float, default=None)
    ap.add_argument("--once", action="store_true", help="emit a single setup+update, then exit")
    ap.add_argument("--log", default="INFO")
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    cfg = Config.load(args.config)
    if args.manager_url:
        cfg.manager_url = args.manager_url
    if args.poll:
        cfg.poll_s = args.poll

    producer = Producer(cfg, HttpManagerClient(cfg.manager_url))
    if args.once:
        producer.start()
        producer.step()
        return 0
    producer.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
