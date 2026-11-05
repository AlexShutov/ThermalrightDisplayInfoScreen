#!/usr/bin/env python3
"""Measure sustained FPS the LY panel accepts, per frame size."""
import io
import os
import struct
import time

import usb.core
from PIL import Image, ImageDraw

VID, PID = 0x0416, 0x5408
EP_OUT, EP_IN = 0x09, 0x81
CHUNK, HDR, USB_WRITE = 512, 16, 4096
DATA = CHUNK - HDR
HS = bytes([0x02, 0xFF]) + bytes(6) + bytes([0x01]) + bytes(8)
HS_PAYLOAD = HS + bytes(2032)


def handshake(dev):
    dev.write(EP_OUT, HS_PAYLOAD, timeout=1000)
    resp = bytes(dev.read(EP_IN, 512, timeout=1000).tobytes())
    return resp[0] == 3 and resp[1] == 0xFF and resp[8] == 1


def build_frame(payload):
    total = len(payload)
    n = total // DATA + 1
    last = total % DATA
    chunks = bytearray(n * CHUNK)
    for i in range(n):
        off = i * CHUNK
        dlen = last if i == n - 1 else DATA
        chunks[off], chunks[off + 1] = 0x01, 0xFF
        struct.pack_into("<I", chunks, off + 2, total)
        struct.pack_into("<H", chunks, off + 6, dlen)
        chunks[off + 8] = 1
        struct.pack_into("<H", chunks, off + 9, n)
        struct.pack_into("<H", chunks, off + 11, i)
        src = i * DATA
        chunks[off + HDR:off + HDR + dlen] = payload[src:src + dlen]
    padded = (n + 3) // 4 * 4
    return bytes(chunks) + bytes(padded * CHUNK - len(chunks))


def jpeg(size, quality):
    w, h = 1920, 400
    img = Image.frombytes("RGB", (w, h), os.urandom(w * h * 3))
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, 560, 200], fill=(0, 0, 0))
    buf = io.BytesIO()
    # adjust quality until size close enough
    for q in range(quality, 101):
        buf.seek(0); buf.truncate(0)
        img.save(buf, "JPEG", quality=q)
        if len(buf.getvalue()) >= size:
            break
    return buf.getvalue()


def main():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print(f"Device {VID:04x}:{PID:04x} not found")
        return 1
    dev.set_configuration()
    assert handshake(dev), "handshake failed"

    for size in (260 * 1024, 340 * 1024, 420 * 1024):
        payload = jpeg(size * 2, 50)[:size] if False else jpeg(size, 60)
        frame = build_frame(payload)
        mk = f"max({len(payload)/1024:.0f}KB)"
        # warmup one
        dev.write(EP_OUT, frame[:4096], timeout=5000)
        # timed run ~2s
        end = time.perf_counter() + 2.0
        count = 0
        ack_times = []
        while time.perf_counter() < end:
            pos = 0
            t0 = time.perf_counter()
            while pos < len(frame):
                n = min(USB_WRITE, len(frame) - pos)
                dev.write(EP_OUT, frame[pos:pos + n], timeout=5000)
                pos += USB_WRITE
            try:
                dev.read(EP_IN, 512, timeout=1000)
            except Exception:
                pass
            ack_times.append((time.perf_counter() - t0) * 1000)
            count += 1
        fps = count / ((ack_times[-1] / 1000) if ack_times else 1)
        avg = sum(ack_times) / len(ack_times) if ack_times else 0
        print(f"size~{len(payload)/1024:.0f}KB: {count} frames/2s = {fps:.1f} fps, "
              f"avg frame {avg:.1f}ms, max {max(ack_times):.1f}ms")


if __name__ == "__main__":
    raise SystemExit(main())