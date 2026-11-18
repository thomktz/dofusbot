"""Image inputs for VLMs: numpy RGB arrays, PIL images, paths or bytes -> base64 JPEG/PNG,
downscaled so the longest side <= max_side (image prefill cost grows ~quadratically:
Qwen2.5-VL uses ~1 token per 28x28 px patch, so 1280x800 ≈ 1300 tokens, 640x400 ≈ 330)."""
from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Union

import numpy as np
from PIL import Image

ImageInput = Union[np.ndarray, Image.Image, str, Path, bytes]


def to_pil(img: ImageInput) -> Image.Image:
    if isinstance(img, Image.Image):
        return img.convert("RGB")
    if isinstance(img, np.ndarray):
        return Image.fromarray(img.astype(np.uint8)).convert("RGB")
    if isinstance(img, (str, Path)):
        return Image.open(img).convert("RGB")
    if isinstance(img, (bytes, bytearray)):
        return Image.open(io.BytesIO(img)).convert("RGB")
    raise TypeError(f"unsupported image type {type(img)}")


def encode_image(img: ImageInput, max_side: int = 1024, fmt: str = "JPEG", quality: int = 85) -> tuple[str, tuple[int, int]]:
    im = to_pil(img)
    if max(im.size) > max_side:
        k = max_side / max(im.size)
        im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)
    buf = io.BytesIO()
    if fmt.upper() == "JPEG":
        im.save(buf, format="JPEG", quality=quality)
    else:
        im.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii"), im.size
