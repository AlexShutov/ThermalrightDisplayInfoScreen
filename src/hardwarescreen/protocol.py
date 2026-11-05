"""LY bulk protocol for the Thermalright Trofeo Vision LCD.

Measured facts for VID 0x0416 PID 0x5408 (Trofeo Vision 11.3):
- Handshake: write 2048 bytes -> read 512 bytes. Validate resp[0]==3,
  resp[1]==0xFF, resp[8]==1. PM = 64 + max(resp[20],1); SUB = resp[22]+1.
- Frame: payload -> 512-byte chunks (16-byte header + 496 data). Pad chunk
  count to a multiple of 4. Write in 4096-byte writes, then read a 512-byte ACK.
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import struct

CHUNK = 512
HDR = 16
DATA = CHUNK - HDR  # 496
USB_WRITE = 4096

# LY handshake: byte[8]=1 (LY) vs 2 (LY1). Our panel is LY -> 1.
_HS_HEAD = bytes([0x02, 0xFF]) + bytes(6) + bytes([0x01]) + bytes(8)
HANDSHAKE_PAYLOAD = _HS_HEAD + bytes(2032)
HANDSHAKE_READ_SIZE = 512
HANDSHAKE_TIMEOUT_MS = 1000
WRITE_TIMEOUT_MS = 5000
READ_TIMEOUT_MS = 1000

# Measured ceiling: the panel silently drops/freezes above ~450 KB.
MAX_FRAME_BYTES = 450_000


def build_frame(payload: bytes, cmd: int = 1) -> bytes:
    """Chunk `payload` into a LY frame (512-byte blocks, mult-of-4 chunk count)."""
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
        chunks[off + 8] = cmd
        struct.pack_into("<H", chunks, off + 9, num_chunks)
        struct.pack_into("<H", chunks, off + 11, i)
        src = i * DATA
        chunks[off + HDR:off + HDR + data_len] = payload[src:src + data_len]
    padded = (num_chunks + 3) // 4 * 4
    total_bytes = padded * CHUNK
    return bytes(chunks) + bytes(total_bytes - len(chunks))


def parse_handshake(resp: bytes) -> dict:
    """Interpret the 512-byte handshake reply."""
    if len(resp) < 37 or resp[0] != 3 or resp[1] != 0xFF or resp[8] != 1:
        raise ValueError(
            f"invalid handshake: len={len(resp)} [0]={resp[0]} [1]={resp[1]} [8]={resp[8]}"
        )
    raw = resp[20]
    if raw <= 3:
        raw = 1
    pm = 64 + raw
    sub = resp[22] + 1
    width = int.from_bytes(resp[24:28], "little")
    height = int.from_bytes(resp[28:32], "little") - resp[44]
    return {"pm": pm, "sub": sub, "width": width, "height": height}
