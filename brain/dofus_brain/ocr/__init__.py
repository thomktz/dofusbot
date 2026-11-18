"""OCR backends (Apple Vision, tesseract, glyph templates, mock) + helpers + CLI
(`python -m dofus_brain.ocr image.png [--crop x,y,w,h]`)."""
from .backends import (  # noqa: F401
    DEFAULT_CHARSET, MockOCR, OcrBackend, OcrLine, TemplateOCR, TesseractOCR, VisionOCR,
    draw_tracked_text, make_ocr, synthetic_font, text_of,
)
from .helpers import crop, load_image, parse_rect, preprocess, upscale  # noqa: F401
