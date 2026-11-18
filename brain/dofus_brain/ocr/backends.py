"""OCR backends.  All return a list of OcrLine (text, box in pixels of the given image, confidence).

- VisionOCR: Apple Vision (VNRecognizeTextRequest, accurate, fr-FR + en-US) via PyObjC. macOS only.
- TesseractOCR: pytesseract if installed (fallback on other OSes).
- TemplateOCR: glyph template matching. Deterministic, no dependencies; good for HUD digits
  (map coords, PA/PM/HP) once glyphs are captured from the real client, and exact on the
  synthetic fixtures (which are rendered with the same font).
- MockOCR: returns canned text; for tests.
"""
from __future__ import annotations

import logging
import platform
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import numpy as np
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)


@dataclass
class OcrLine:
    text: str
    box: tuple[int, int, int, int]  # x0, y0, x1, y1 pixels in the input image
    conf: float = 1.0


class OcrBackend(Protocol):
    name: str

    def recognize(self, img: np.ndarray) -> list[OcrLine]: ...


def text_of(lines: list[OcrLine]) -> str:
    return "\n".join(l.text for l in lines)


# ---------------------------------------------------------------- Apple Vision
class VisionOCR:
    """Apple Vision text recognition (macOS 10.15+; French needs macOS 13+ for best results).

    level: "accurate" (default, ~100-300 ms on M1 for a full 1280x800 frame) or "fast"
    (~20-60 ms, weaker on small/stylised text). For HUD numbers prefer crop + upscale x2-3
    with `accurate` and language correction off.
    """

    name = "vision"

    def __init__(self, languages: list[str] | None = None, level: str = "accurate",
                 language_correction: bool = False, min_text_height: float = 0.0,
                 custom_words: list[str] | None = None):
        import Vision  # type: ignore  # pyobjc-framework-Vision
        from Foundation import NSData  # type: ignore  # noqa: F401  (pyobjc core)

        self._Vision = Vision
        self.languages = languages or ["fr-FR", "en-US"]
        if level not in ("accurate", "fast"):
            raise ValueError("level must be 'accurate' or 'fast'")
        self.level = level
        self.language_correction = language_correction
        self.min_text_height = min_text_height
        self.custom_words = custom_words or []

    @classmethod
    def supported_languages(cls, level: str = "accurate") -> list[str]:
        import Vision  # type: ignore

        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelFast if level == "fast"
                                 else Vision.VNRequestTextRecognitionLevelAccurate)
        langs, _err = req.supportedRecognitionLanguagesAndReturnError_(None)
        return [str(x) for x in (langs or [])]

    def recognize(self, img: np.ndarray) -> list[OcrLine]:
        import io

        from Foundation import NSData  # type: ignore

        Vision = self._Vision
        h, w = img.shape[:2]
        if h == 0 or w == 0:
            return []
        buf = io.BytesIO()
        Image.fromarray(np.ascontiguousarray(img)).save(buf, format="PNG")
        raw = buf.getvalue()
        data = NSData.dataWithBytes_length_(raw, len(raw))

        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelFast if self.level == "fast"
                                 else Vision.VNRequestTextRecognitionLevelAccurate)
        req.setRecognitionLanguages_(self.languages)
        req.setUsesLanguageCorrection_(self.language_correction)
        if self.min_text_height:
            req.setMinimumTextHeight_(float(self.min_text_height))
        if self.custom_words:
            req.setCustomWords_(self.custom_words)
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
        ok, err = handler.performRequests_error_([req], None)
        if not ok:
            log.warning("Vision OCR failed: %s", err)
            return []
        results: list[OcrLine] = []
        for obs in req.results() or []:
            cands = obs.topCandidates_(1)
            if not cands:
                continue
            c = cands[0]
            bb = obs.boundingBox()  # normalised, origin bottom-left
            x0 = bb.origin.x * w
            y1 = (1 - bb.origin.y) * h
            x1 = x0 + bb.size.width * w
            y0 = y1 - bb.size.height * h
            results.append(OcrLine(str(c.string()), (int(x0), int(y0), int(round(x1)), int(round(y1))),
                                   float(c.confidence())))
        results.sort(key=lambda l: (l.box[1], l.box[0]))
        return results


# ---------------------------------------------------------------- tesseract
class TesseractOCR:
    name = "tesseract"

    def __init__(self, lang: str = "fra+eng"):
        import pytesseract  # type: ignore

        pytesseract.get_tesseract_version()  # raises if the binary is missing
        self._t = pytesseract
        self.lang = lang

    def recognize(self, img: np.ndarray) -> list[OcrLine]:
        d = self._t.image_to_data(Image.fromarray(img), lang=self.lang, output_type=self._t.Output.DICT)
        lines: dict[tuple, list[int]] = {}
        for i, t in enumerate(d["text"]):
            if t.strip():
                lines.setdefault((d["block_num"][i], d["par_num"][i], d["line_num"][i]), []).append(i)
        out = []
        for idxs in lines.values():
            text = " ".join(d["text"][i] for i in idxs)
            x0 = min(d["left"][i] for i in idxs)
            y0 = min(d["top"][i] for i in idxs)
            x1 = max(d["left"][i] + d["width"][i] for i in idxs)
            y1 = max(d["top"][i] + d["height"][i] for i in idxs)
            conf = float(np.mean([float(d["conf"][i]) for i in idxs])) / 100.0
            out.append(OcrLine(text, (x0, y0, x1, y1), conf))
        out.sort(key=lambda l: (l.box[1], l.box[0]))
        return out


# ---------------------------------------------------------------- templates
DEFAULT_CHARSET = (
    "0123456789,-./:()%"
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "?!'"  # ASCII only: the Pillow-bundled synthetic font has no accented glyphs
)
NORM = (16, 24)  # (w, h) of the normalised glyph used for matching


def synthetic_font(size: int = 22) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """The font used by the synthetic fixtures (bundled with Pillow, so identical everywhere)."""
    return ImageFont.load_default(size=size)


def draw_tracked_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font, fill, tracking: int = 3) -> int:
    """Draw text char by char with extra spacing (so glyphs never touch). Returns end x."""
    x, y = xy
    for ch in text:
        if ch == " ":
            x += int(font.getlength(" ")) + tracking * 2
            continue
        draw.text((x, y), ch, font=font, fill=fill)
        x += int(round(font.getlength(ch))) + tracking
    return x


@dataclass
class _Glyph:
    char: str
    bitmap: np.ndarray  # NORM-sized float32 0..1
    w: int
    h: int
    top: int     # top of ink relative to baseline (negative = above)
    bottom: int  # bottom of ink relative to baseline


def _norm(bitmap: np.ndarray) -> np.ndarray:
    im = Image.fromarray((bitmap * 255).astype(np.uint8)).resize(NORM, Image.BILINEAR)
    return np.asarray(im, dtype=np.float32) / 255.0


class TemplateOCR:
    """Glyph template matcher for light text on a darker background (or `invert=True`).

    Segmentation: rows -> lines by horizontal projection, columns -> glyphs by vertical
    projection (glyphs must not touch; true for HUD digits), wide gaps -> spaces.
    Matching: normalised bitmap correlation + size and baseline-offset penalties.
    """

    name = "template"

    def __init__(self, glyphs: list[_Glyph], threshold: int = 128, invert: bool = False):
        self.glyphs = glyphs
        self.threshold = threshold
        self.invert = invert
        self._bank = np.stack([g.bitmap.ravel() for g in glyphs]) if glyphs else np.zeros((0, NORM[0] * NORM[1]))

    # -- construction
    @classmethod
    def from_font(cls, font=None, charset: str = DEFAULT_CHARSET, sizes: tuple[int, ...] = (22, 18), **kw) -> "TemplateOCR":
        """Templates rendered from `font` (or the synthetic font at each of `sizes`)."""
        fonts = [font] if font is not None else [synthetic_font(s) for s in sizes]
        glyphs = []
        for font, ch in ((f, c) for f in fonts for c in charset):
            canvas = Image.new("L", (80, 80), 0)
            d = ImageDraw.Draw(canvas)
            d.text((20, 20), ch, font=font, fill=255, anchor="ls")  # baseline at y=20
            arr = np.asarray(canvas) >= 128
            ys, xs = np.nonzero(arr)
            if len(xs) == 0:
                continue
            crop = arr[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1].astype(np.float32)
            glyphs.append(_Glyph(ch, _norm(crop), crop.shape[1], crop.shape[0], int(ys.min()) - 20, int(ys.max()) - 20))
        return cls(glyphs, **kw)

    @classmethod
    def from_dir(cls, directory: Path, **kw) -> "TemplateOCR":
        """Load glyph PNGs captured from the real client. File name = char code point
        (e.g. `0030.png` for '0', `002c.png` for ','), image = white glyph on black, with the
        baseline at the bottom row, or `<code>_b<row>.png` giving the baseline row explicitly
        (glyphs with descenders). `python -m dofus_brain.calibrate glyphs` writes these."""
        glyphs = []
        for p in sorted(Path(directory).glob("*.png")):
            try:
                ch = chr(int(p.stem.split("_")[0], 16))
            except ValueError:
                continue
            arr = np.asarray(Image.open(p).convert("L")) >= 128
            ys, xs = np.nonzero(arr)
            if len(xs) == 0:
                continue
            crop = arr[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1].astype(np.float32)
            base = arr.shape[0] - 1
            if "_b" in p.stem:
                base = int(p.stem.split("_b")[1])
            glyphs.append(_Glyph(ch, _norm(crop), crop.shape[1], crop.shape[0], int(ys.min()) - base, int(ys.max()) - base))
        return cls(glyphs, **kw)

    # -- recognition
    def _binary(self, img: np.ndarray) -> np.ndarray:
        g = img.mean(axis=-1) if img.ndim == 3 else img.astype(np.float32)
        return (g < self.threshold) if self.invert else (g >= self.threshold)

    def recognize(self, img: np.ndarray) -> list[OcrLine]:
        if img.size == 0 or not self.glyphs:
            return []
        b = self._binary(img)
        rows = b.any(axis=1)
        out: list[OcrLine] = []
        for y0, y1 in _runs(rows, min_gap=2):
            line = b[y0:y1]
            text, conf, x0, x1 = self._read_line(line)
            if text.strip():
                out.append(OcrLine(text.strip(), (x0, y0, x1, y1), conf))
        return out

    def _read_line(self, line: np.ndarray) -> tuple[str, float, int, int]:
        cols = line.any(axis=0)
        segs = _runs(cols, min_gap=1)
        if not segs:
            return "", 0.0, 0, 0
        items = []
        for x0, x1 in segs:
            sub = line[:, x0:x1]
            ys = np.nonzero(sub.any(axis=1))[0]
            items.append((x0, x1, int(ys.min()), int(ys.max())))
        # baseline = most common ink bottom (most glyphs sit on it)
        bottoms = [it[3] for it in items]
        baseline = max(set(bottoms), key=bottoms.count)
        height = max(it[3] for it in items) - min(it[2] for it in items) + 1
        gaps = [b[0] - a[1] for a, b in zip(segs, segs[1:])]
        med = float(np.median(gaps)) if gaps else 0.0
        space_gap = max(2.2 * med, 0.45 * height, 4)
        chars, confs = [], []
        prev_x1 = None
        for x0, x1, t, bot in items:
            if prev_x1 is not None and x0 - prev_x1 > space_gap:
                chars.append(" ")
            prev_x1 = x1
            crop = line[t: bot + 1, x0:x1].astype(np.float32)
            ch, c = self._match(crop, t - baseline, bot - baseline)
            chars.append(ch)
            confs.append(c)
        return "".join(chars), float(np.mean(confs)), segs[0][0], segs[-1][1]

    def _match(self, crop: np.ndarray, top: int, bottom: int) -> tuple[str, float]:
        v = _norm(crop).ravel()
        diff = np.abs(self._bank - v).mean(axis=1)  # 0 = identical
        h, w = crop.shape
        scores = []
        for i, g in enumerate(self.glyphs):
            size_pen = (abs(g.w - w) / max(g.w, w)) * 0.5 + (abs(g.h - h) / max(g.h, h)) * 0.5
            base_pen = (abs(g.top - top) + abs(g.bottom - bottom)) / max(4.0, float(max(g.h, h)))
            scores.append(diff[i] + 0.6 * size_pen + 0.6 * base_pen)
        k = int(np.argmin(scores))
        return self.glyphs[k].char, float(max(0.0, 1.0 - scores[k]))


def _runs(mask: np.ndarray, min_gap: int = 1) -> list[tuple[int, int]]:
    """[start, end) runs of True, merging runs separated by gaps < min_gap."""
    idx = np.nonzero(mask)[0]
    if len(idx) == 0:
        return []
    runs = []
    start = prev = int(idx[0])
    for i in idx[1:]:
        i = int(i)
        if i - prev > min_gap:
            runs.append((start, prev + 1))
            start = i
        prev = i
    runs.append((start, prev + 1))
    return runs


# ---------------------------------------------------------------- mock
class MockOCR:
    """Canned OCR. `responder(img) -> str | list[OcrLine]` or a fixed string/list."""

    name = "mock"

    def __init__(self, responder: Callable[[np.ndarray], str | list[OcrLine]] | str | list[str] = ""):
        self.responder = responder
        self.calls = 0

    def recognize(self, img: np.ndarray) -> list[OcrLine]:
        self.calls += 1
        r = self.responder(img) if callable(self.responder) else self.responder
        if isinstance(r, str):
            r = r.splitlines()
        h, w = img.shape[:2]
        out = []
        for i, item in enumerate(r):
            if isinstance(item, OcrLine):
                out.append(item)
            else:
                n = max(1, len(r))
                out.append(OcrLine(item, (0, i * h // n, w, (i + 1) * h // n), 0.99))
        return out


# ---------------------------------------------------------------- factory
def make_ocr(cfg: dict | None = None, mock: bool = False, backend: str | None = None) -> OcrBackend:
    """backend: auto | vision | tesseract | template | mock. auto = Vision on macOS, else
    tesseract if installed, else templates. `mock=True` (synthetic fixtures) forces template."""
    cfg = cfg or {}
    backend = backend or cfg.get("backend", "auto")
    if mock:
        backend = "template"
    if backend in ("auto", "vision") and platform.system() == "Darwin":
        try:
            return VisionOCR(cfg.get("languages"), level=cfg.get("level", "accurate"))
        except Exception as e:  # pragma: no cover - mac only
            if backend == "vision":
                raise
            log.warning("Apple Vision OCR unavailable (%s); pip install -e 'brain[mac]'", e)
    elif backend == "vision":
        raise RuntimeError("Apple Vision OCR needs macOS + pyobjc-framework-Vision (pip install -e 'brain[mac]')")
    if backend in ("auto", "tesseract"):
        try:
            return TesseractOCR()
        except Exception as e:
            if backend == "tesseract":
                raise RuntimeError(f"tesseract unavailable: {e}") from e
    if backend == "mock":
        return MockOCR()
    tdir = cfg.get("template_dir")
    if tdir and not mock:
        from ..paths import brain_home

        p = Path(tdir)
        p = p if p.is_absolute() else brain_home() / p
        if p.is_dir() and any(p.glob("*.png")):
            return TemplateOCR.from_dir(p)
    return TemplateOCR.from_font()
