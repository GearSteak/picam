"""Game Boy Camera style filter.

Classic GB Camera sensor was 128x112 with four greys, ordered dither.
We keep that look and optionally tint like the original green LCD.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

# 4×4 Bayer matrix, scaled to 0..63 for four buckets.
_BAYER = np.array(
    [
        [0, 8, 2, 10],
        [12, 4, 14, 6],
        [3, 11, 1, 9],
        [15, 7, 13, 5],
    ],
    dtype=np.float32,
)

# Four Game Boy greys (dark → light), then mapped to greenish LCD palette.
_GREYS = np.array([0, 85, 170, 255], dtype=np.uint8)
_LCD = np.array(
    [
        [15, 56, 15],
        [48, 98, 48],
        [139, 172, 15],
        [155, 188, 15],
    ],
    dtype=np.uint8,
)


def gameboy_frame(image: Image.Image, *, lcd_tint: bool = True, scale: int = 4) -> Image.Image:
    """Resize to 128×112, dither to 4 shades, optionally green LCD colors, then upscale."""
    src = image.convert("RGB")
    small = src.resize((128, 112), Image.Resampling.BILINEAR)
    arr = np.asarray(small, dtype=np.float32)
    # Rec.601 luma
    luma = arr[:, :, 0] * 0.299 + arr[:, :, 1] * 0.587 + arr[:, :, 2] * 0.114

    h, w = luma.shape
    tiled = np.tile(_BAYER, (h // 4 + 1, w // 4 + 1))[:h, :w]
    # Map 0..255 into 4 bands with Bayer threshold jitter.
    threshold = (tiled / 16.0) * 64.0
    level = np.clip(((luma + threshold) / 64.0).astype(np.int32), 0, 3)

    if lcd_tint:
        out = _LCD[level]
    else:
        g = _GREYS[level]
        out = np.stack([g, g, g], axis=-1)

    gb = Image.fromarray(out, mode="RGB")
    if scale <= 1:
        return gb
    return gb.resize((128 * scale, 112 * scale), Image.Resampling.NEAREST)
