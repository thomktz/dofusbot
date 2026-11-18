"""Crop / upscale / preprocessing helpers. Small HUD text OCRs much better after a 2-3x
Lanczos upscale; light-on-dark text sometimes benefits from inversion for tesseract."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageOps


def parse_rect(spec: str) -> tuple[float, float, float, float]:
    parts = [float(p) for p in spec.replace(" ", "").split(",")]
    if len(parts) != 4:
        raise ValueError("crop must be x,y,w,h")
    return parts[0], parts[1], parts[2], parts[3]


def crop(img: np.ndarray, rect: tuple[float, float, float, float], fractional: bool | None = None) -> np.ndarray:
    """Crop (x, y, w, h). Fractions of the image if all values <= 1 (or fractional=True), else pixels."""
    h, w = img.shape[:2]
    x, y, cw, ch = rect
    if fractional is None:
        fractional = all(0 <= v <= 1 for v in rect)
    if fractional:
        x, y, cw, ch = x * w, y * h, cw * w, ch * h
    x0, y0 = max(0, int(round(x))), max(0, int(round(y)))
    x1, y1 = min(w, int(round(x + cw))), min(h, int(round(y + ch)))
    return img[y0:y1, x0:x1]


def upscale(img: np.ndarray, factor: float = 2.0) -> np.ndarray:
    if factor == 1 or img.size == 0:
        return img
    im = Image.fromarray(img)
    im = im.resize((max(1, int(im.width * factor)), max(1, int(im.height * factor))), Image.LANCZOS)
    return np.asarray(im)


def preprocess(img: np.ndarray, gray: bool = False, invert: bool = False, autocontrast: bool = False,
               threshold: int | None = None) -> np.ndarray:
    im = Image.fromarray(img)
    if gray or threshold is not None:
        im = im.convert("L")
    if autocontrast:
        im = ImageOps.autocontrast(im)
    if invert:
        im = ImageOps.invert(im.convert("RGB") if im.mode not in ("L", "RGB") else im)
    if threshold is not None:
        im = im.point(lambda v: 255 if v >= threshold else 0)
    im = im.convert("RGB")
    return np.asarray(im)


def load_image(path: str) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"))
