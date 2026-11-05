#!/usr/bin/env python3
"""Probe: Thermalright Trofeo Vision LY (0416:5408).

Handshake + JPEG frame-size ladder, LY bulk protocol ported from
Lexonight1/thermalright-trcc-linux `ly_lcd.py`.
"""
import io
import struct
import sys
import time

import usb.core
import usb.util
from PIL import Image, ImageDraw

VID, PID = 0x0416, 0x5408
EP_OUT, EP_IN = 0x09, 0x81
CHUNK = 512
HDR = 16
DATA = CHUNK - HDR  # 496
USB_WRITE = 4096

# LY handshake: 2048 bytes -> 512-byte reply
HS = bytes([0x02, 0xFF]) + bytes(6) + bytes([0x01]) + bytes(8)
HS_PAYLOAD = HS + bytes(2032)


def find():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        sys.exit("Device 0416:5408 not found")
    try:
        dev.set_configuration()
    except Exception as e:
        print("set_configuration:", e)
    return dev


def handshake(dev):
    dev.write(EP_OUT, HS_PAYLOAD, timeout=1000)
    resp = dev.read(EP_IN, 512, timeout=1000)
    if len(resp) < 37 or resp[0] != 3 or resp[1] != 0xFF or resp[8] != 1:
        sys.exit(f"handshake invalid: len={len(resp)} [0]={resp[0]} [1]={resp[1]} [8]={resp[8]}")
    raw = resp[20]
    if raw <= 3:
        raw = 1
    pm = 64 + raw
    sub = resp[22] + 1
    w = int.from_bytes(resp[24:28], "little")
    h = int.from_bytes(resp[28:32], "little") - resp[44]
    print(f"OK handshake: PM={pm} SUB={sub} reply_size={w}x{h}")
    return pm, sub, w, h


def build_frame(payload):
    total_size = len(payload)
    num_chunks = total_size // DATA + 1
    last_data = total_size % DATA
    chunks = bytearray(num_chunks * CHUNK)
    for i in range(num_chunks):
        off = i * CHUNK
        is_last = i == num_chunks - 1
        data_len = last_data if is_last else DATA
        chunks[off] = 0x01
        chunks[off + 1] = 0xFF
        struct.pack_into("<I", chunks, off + 2, total_size)
        struct.pack_into("<H", chunks, off + 6, data_len)
        chunks[off + 8] = 1  # LY
        struct.pack_into("<H", chunks, off + 9, num_chunks)
        struct.pack_into("<H", chunks, off + 11, i)
        src = i * DATA
        chunks[off + HDR:off + HDR + data_len] = payload[src:src + data_len]
    padded = (num_chunks + 3) // 4 * 4
    total_bytes = padded * CHUNK
    return bytes(chunks) + bytes(total_bytes - len(chunks))


def send_frame(dev, img, quality):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    payload = buf.getvalue()
    frame = build_frame(payload)
    total = len(frame)
    pos = 0
    while pos < total:
        n = min(USB_WRITE, total - pos)
        dev.write(EP_OUT, frame[pos:pos + n], timeout=5000)
        pos += USB_WRITE
    try:
        ack = dev.read(EP_IN, 512, timeout=1000)
        ack_head = bytes(ack.tobytes()[:8]).hex(" ")
    except Exception as e:
        ack_head = f"no-ack {e}"
    return len(payload), total, ack_head


def make_frame(w, h, label, tag):
    """Detailed (noisy) frame so the JPEG is large enough to probe the size limit."""
    import os
    img = Image.frombytes("RGB", (w, h), os.urandom(w * h * 3))
    d = ImageDraw.Draw(img)
    # put an opaque label block so the frame number is readable over the noise
    d.rectangle([20, 20, 560, 200], fill=(0, 0, 0))
    d.text((40, 40), f"{label}", fill=(255, 255, 255))
    d.text((40, 110), f"w={w} h={h}  {tag}", fill=(0, 255, 120))
    return img


def main():
    dev = find()
    try:
        pm, sub, w, h = handshake(dev)
    except SystemExit as e:
        print(e)
        return
    if not (0 < w <= 4000 and 0 < h <= 4000):
        print(f"reply size {w}x{h} looks wrong; falling back to 1920x400")
        w, h = 1920, 400
    # The 11.3 panel is advertised as 1920x400; the reply reports 1920x440.
    if h == 440:
        print("note: reply reports 1920x440 (marketed 1920x400) - rendering at 1920x400")
        h = 400

    ladder = [50, 60, 70, 80, 88, 95]
    print("Sending NOISE frame ladder. Watch the screen for the label + 'KB'.")
    print("(cycling until Ctrl+C)")
    while True:
        for i, q in enumerate(ladder):
            img = make_frame(w, h, f"FRAME {i+1}  Q={q}", "hardwarescreen noise probe")
            size, frame_size, ack = send_frame(dev, img, q)
            kb = size / 1024
            print(f"  #{i+1} Q={q}: jpeg={kb:.0f}KB usb_frame={frame_size}B ack[{ack}]")
            time.sleep(2.0)

    print("Ladder done. Which frame numbers showed on the screen?")


if __name__ == "__main__":
    main()