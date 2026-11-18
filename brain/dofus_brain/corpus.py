"""Opt-in, local capture corpus recorder for evaluating vision models.

The recorder is deliberately separate from live perception: it never sends input, and it
writes an image only when it is materially different from the last saved frame for that
predicted screen.  Each PNG has an adjacent JSON file where a human can later fill in
``truth_screen`` before using the images in a model evaluation.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .perception.classifier import Classification
from .util import now_ms


class CorpusRecorder:
    def __init__(self, directory: str | Path, min_interval_s: float = 5.0, min_hash_distance: int = 12):
        self.directory = Path(directory).expanduser()
        self.min_interval_s = max(0.0, float(min_interval_s))
        self.min_hash_distance = max(0, int(min_hash_distance))
        self._last_at: dict[str, float] = {}
        self._last_hash: dict[str, bytes] = {}

    @staticmethod
    def _fingerprint(image: np.ndarray) -> bytes:
        """A small perceptual hash; robust to timestamps and other tiny UI changes."""
        im = Image.fromarray(image).convert("L").resize((32, 18), Image.Resampling.BILINEAR)
        pixels = np.asarray(im, dtype=np.uint8)
        return np.packbits(pixels >= pixels.mean()).tobytes()

    @staticmethod
    def _distance(a: bytes, b: bytes) -> int:
        return sum((x ^ y).bit_count() for x, y in zip(a, b))

    def record(self, image: np.ndarray, scale: float, classification: Classification) -> Path | None:
        screen = classification.screen
        now = time.monotonic()
        fingerprint = self._fingerprint(image)
        if now - self._last_at.get(screen, float("-inf")) < self.min_interval_s:
            return None
        previous = self._last_hash.get(screen)
        if previous is not None and self._distance(previous, fingerprint) < self.min_hash_distance:
            return None

        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = now_ms()
        digest = hashlib.sha256(fingerprint).hexdigest()[:8]
        stem = f"{stamp}_{screen}_{digest}"
        image_path = self.directory / f"{stem}.png"
        Image.fromarray(image).save(image_path, format="PNG")
        metadata: dict[str, Any] = {
            "image": image_path.name,
            "captured_at_ms": stamp,
            "scale": scale,
            "shape": list(image.shape),
            "predicted_screen": screen,
            "classifier": asdict(classification),
            "truth_screen": None,
        }
        image_path.with_suffix(".json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
                                                   encoding="utf-8")
        self._last_at[screen] = now
        self._last_hash[screen] = fingerprint
        return image_path
