"""Our own LY USB transport (pure Python via pyusb + libusb).

Makes the core path self-contained and portable without `trcc`. The endpoints
are auto-detected on the device (write bulk OUT, read bulk IN) rather than
hardcoded to a class constant.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import logging
import time

import usb.core
import usb.util

from . import PID, VID
from .protocol import (
    HANDSHAKE_PAYLOAD,
    HANDSHAKE_READ_SIZE,
    HANDSHAKE_TIMEOUT_MS,
    READ_TIMEOUT_MS,
    USB_WRITE,
    WRITE_TIMEOUT_MS,
    build_frame,
    parse_handshake,
)

LOG = logging.getLogger("trofeo.ly")


def find(vid: int = VID, pid: int = PID):
    return usb.core.find(idVendor=vid, idProduct=pid)


class LyDisplay:
    """A connected Trofeo Vision panel (stateful: open -> handshake -> send)."""

    def __init__(self, vid: int = VID, pid: int = PID):
        self.vid, self.pid = vid, pid
        self.dev = None
        self.ep_out: int = 0x09
        self.ep_in: int = 0x81
        self.info: dict | None = None

    # -- lifecycle ------------------------------------------------------
    def present(self) -> bool:
        return find(self.vid, self.pid) is not None

    def open(self) -> None:
        dev = find(self.vid, self.pid)
        if dev is None:
            raise RuntimeError(f"device {self.vid:04x}:{self.pid:04x} not present")
        self.dev = dev
        try:
            dev.set_configuration()
        except Exception as e:  # already set
            LOG.debug("set_configuration: %s", e)
        outs, ins = [], []
        cfg = dev.get_active_configuration()
        for itf in cfg:
            for ep in itf.endpoints():
                addr = ep.bEndpointAddress
                if ep.bmAttributes & 0x3 == usb.util.ENDPOINT_TYPE_BULK:
                    if usb.util.endpoint_direction(addr) == usb.util.ENDPOINT_OUT:
                        outs.append(addr)
                    else:
                        ins.append(addr)
        if outs:
            self.ep_out = outs[0]
        if ins:
            self.ep_in = ins[0]
        LOG.debug("endpoints: OUT=0x%02x IN=0x%02x", self.ep_out, self.ep_in)

    def handshake(self) -> dict:
        self._check_open()
        self.dev.write(self.ep_out, HANDSHAKE_PAYLOAD, timeout=HANDSHAKE_TIMEOUT_MS)
        resp = bytes(self.dev.read(self.ep_in, HANDSHAKE_READ_SIZE, timeout=HANDSHAKE_TIMEOUT_MS).tobytes())
        self.info = parse_handshake(resp)
        LOG.info("handshake PM=%d SUB=%d size=%dx%d", self.info["pm"], self.info["sub"],
                 self.info["width"], self.info["height"])
        return self.info

    def send(self, payload: bytes) -> bytes:
        """Send a raw frame payload (e.g. JPEG) and return the ACK bytes."""
        self._check_open()
        frame = build_frame(payload)
        total = len(frame)
        pos = 0
        while pos < total:
            n = min(USB_WRITE, total - pos)
            self.dev.write(self.ep_out, frame[pos:pos + n], timeout=WRITE_TIMEOUT_MS)
            pos += USB_WRITE
        try:
            ack = bytes(self.dev.read(self.ep_in, HANDSHAKE_READ_SIZE, timeout=READ_TIMEOUT_MS).tobytes())
        except Exception as e:
            LOG.warning("no ACK: %s", e)
            ack = b""
        return ack

    def close(self) -> None:
        if self.dev is not None:
            try:
                usb.util.dispose_resources(self.dev)
            except Exception:
                pass
            self.dev = None

    # -- helpers --------------------------------------------------------
    def _check_open(self) -> None:
        if self.dev is None:
            raise RuntimeError("device not open")

    def connect(self, retries: int = 5, delay_s: float = 1.0) -> dict:
        """Open + handshake with retry/backoff (reconnect from WAIT)."""
        for _ in range(retries):
            if self.present():
                try:
                    self.open()
                    return self.handshake()
                except Exception as e:
                    LOG.warning("connect attempt failed: %s", e)
                    self.close()
            time.sleep(delay_s)
        raise RuntimeError("could not connect to the panel")