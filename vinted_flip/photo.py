"""Photo quality scoring.

The whole thesis of the flip is "good item, bad photo". This module scores a
listing photo 0–100 using only Pillow, so there is no heavy CV dependency:

- brightness: mean luminance. Dark bedroom-floor shots score badly.
- contrast:   luminance standard deviation. Flat, washed-out shots score badly.
- sharpness:  variance of the edge-filtered image. Blurry shots score badly.
- clutter:    edge density in the border region of the frame. A clean plain
              background has quiet borders; a messy carpet/wardrobe shot does not.

A score under ~45 is a genuinely poor photo — the kind a clean reshoot on a
plain background visibly improves.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageFilter, ImageStat


@dataclass
class PhotoScore:
    total: float  # 0 (terrible) .. 100 (great)
    brightness: float
    contrast: float
    sharpness: float
    clutter: float
    raw_mean: float = 0.0  # raw luminance 0..255, for labelling

    @property
    def problems(self) -> list[str]:
        out = []
        if self.raw_mean < 80:
            out.append("too dark")
        elif self.raw_mean > 235:
            out.append("blown out")
        if self.contrast < 35:
            out.append("flat/washed out")
        if self.sharpness < 30:
            out.append("blurry")
        if self.clutter < 40:
            out.append("cluttered background")
        return out


def _scale(value: float, lo: float, hi: float) -> float:
    """Map value onto 0..100 where lo -> 0 and hi -> 100, clamped."""
    if hi == lo:
        return 50.0
    return max(0.0, min(100.0, (value - lo) / (hi - lo) * 100.0))


def score_photo(data: bytes) -> PhotoScore:
    img = Image.open(io.BytesIO(data)).convert("L")
    # Normalise size so the metrics are comparable across thumbnails.
    img.thumbnail((320, 320))
    stat = ImageStat.Stat(img)
    mean = stat.mean[0]
    stddev = stat.stddev[0]

    # Brightness: anything from 110 up to a bright white-background shot (~200)
    # is ideal; penalise dark shots hard and truly blown-out ones gently.
    if mean < 110:
        brightness = _scale(mean, 30, 110)
    elif mean <= 200:
        brightness = 100.0
    else:
        brightness = _scale(255 - mean, 5, 55)

    # Contrast: stddev under ~25 looks flat, 55+ is plenty.
    contrast = _scale(stddev, 15, 60)

    # Sharpness: variance of edges. Blurry photos have weak edges everywhere.
    edges = img.filter(ImageFilter.FIND_EDGES)
    edge_stat = ImageStat.Stat(edges)
    sharpness = _scale(edge_stat.stddev[0], 5, 40)

    # Clutter: edge activity in the outer 15% border of the frame. A product
    # shot on a plain background has almost no edges near the frame edge.
    w, h = edges.size
    bx, by = max(1, int(w * 0.15)), max(1, int(h * 0.15))
    border_boxes = [
        (0, 0, w, by),  # top
        (0, h - by, w, h),  # bottom
        (0, by, bx, h - by),  # left
        (w - bx, by, w, h - by),  # right
    ]
    border_means = []
    for box in border_boxes:
        region = edges.crop(box)
        if region.size[0] and region.size[1]:
            border_means.append(ImageStat.Stat(region).mean[0])
    border_activity = sum(border_means) / len(border_means) if border_means else 0.0
    # Low border activity = clean background = high clutter score.
    clutter = _scale(45 - border_activity, 0, 45)

    total = (
        brightness * 0.30 + contrast * 0.15 + sharpness * 0.25 + clutter * 0.30
    )
    return PhotoScore(
        total=round(total, 1),
        brightness=round(brightness, 1),
        contrast=round(contrast, 1),
        sharpness=round(sharpness, 1),
        clutter=round(clutter, 1),
        raw_mean=round(mean, 1),
    )
