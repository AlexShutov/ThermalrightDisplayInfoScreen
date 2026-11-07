"""Shared JPEG frame helpers: encode within the device frame budget."""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import io

from PIL import Image

from .protocol import MAX_FRAME_BYTES

QUALITY_LADDER = (85, 75, 65, 55, 45)

# JPEG без субдискретизации цветности (4:4:4): Pillow по умолчанию кодирует 4:2:0,
# из-за чего тонкие цветные кромки (особенно красно-оранжевые) «шакалятся» —
# цветность сглаживается блоками 2×2.  Для тонкой заливки датчиков это заметно.
JPEG_CHROMA_SUBSAMPLING = 0


def encode_within_budget(img: Image.Image, budget: int | None = MAX_FRAME_BYTES) -> bytes:
    """JPEG-encode, shrinking quality/scale until it fits ``budget`` bytes."""
    budget = budget or MAX_FRAME_BYTES
    for q in QUALITY_LADDER:
        jpeg = _jpeg(img, q)
        if len(jpeg) <= budget:
            return jpeg
    scale = img
    for _ in range(8):  # fall back to downscaling
        scale = scale.resize((int(scale.width * 0.85), int(scale.height * 0.85)))
        for q in (70, 60, 50):
            jpeg = _jpeg(scale, q)
            if len(jpeg) <= budget:
                return jpeg
    raise RuntimeError("could not encode within frame budget")


def _jpeg(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, subsampling=JPEG_CHROMA_SUBSAMPLING)
    return buf.getvalue()
