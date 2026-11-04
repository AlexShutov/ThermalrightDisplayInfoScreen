# hardware_protocol.md — device + wire facts (measured, do not re-derive)

- Device: VID `0x0416` PID `0x5408`, `USBDISPLAY`, LY bulk.
- Endpoints: write EP `0x09` OUT, read EP `0x81` IN (auto-detected; do not hardcode `_EP_WRITE`).
- Handshake: write 2048 -> read 512. Validate `resp[0]==3, resp[1]==0xFF, resp[8]==1`.
  `PM = 64 + max(resp[20], 1)` (clamp raw<=3 to 1), `SUB = resp[22] + 1`.
  `PM==69` -> Trofeo Vision 11.3 (marketed 1920x400; handshake reports 1920x440).
- Frame send: payload -> 512-byte chunks (16-byte header + 496 data). Header:
  `b0=0x01, b1=0xFF, u32@2=total_size, u16@6=data_len, b8=0x01(LY), u16@9=num_chunks, u16@11=idx`.
  Pad chunk count to multiple of 4. Write 4096-byte writes on `0x09`, then 512-byte ACK on `0x81`.
- Frame-size ceiling: JPEG <= 450000 bytes. Above ~450 KB the panel silently freezes/drops
  (measured: Q~80/443 KB shows; Q~88/553 KB freezes). Never exceed — enforce quality/scale shrink.
  Do NOT rely on the ACK to detect a drop (ACK returns even for dropped frames).
- Frame-rate ceiling: ~18 FPS (write+ACK round-trip ~55 ms), ~independent of frame size in budget.
  Vendor design `idle 2 / active 15 FPS`.
- Revert-to-logo: panel returns to logo ~2-3 s after last frame -> keepalive every ~1.5-2 s.

## Containment rules
- USB lifecycle only in `src/trofeo/supervise.py`. Don't scatter it.
- `max_frame_bytes = 450000` everywhere; keepalive default `keepalive_s = 2.0`.
- Usb contention: thin client `trofeo run` holds the device -> trcc daemon gets `Access denied`.
  Stop the client to free USB.