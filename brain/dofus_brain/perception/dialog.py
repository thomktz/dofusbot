"""Dialog reader: OCR of the NPC dialog box; options are lines starting with 'N.' or 'N)'."""
from __future__ import annotations

import re

import numpy as np

from ..game.state import Dialog, DialogOption
from ..ocr.backends import OcrBackend
from .regions import Regions

OPTION_RE = re.compile(r"^\s*(\d{1,2})\s*[.)\-:]\s*(.+)$")


def parse_options(lines: list[str]) -> list[DialogOption]:
    out = []
    for ln in lines:
        m = OPTION_RE.match(ln)
        if m:
            out.append(DialogOption(int(m.group(1)), m.group(2).strip()))
    return out


class DialogReader:
    def __init__(self, ocr: OcrBackend, regions: Regions):
        self.ocr = ocr
        self.r = regions

    def read(self, img: np.ndarray, scale: float = 1.0) -> Dialog:
        npc_lines = self.ocr.recognize(self.r.crop(img, "dialog_npc"))
        text_lines = self.ocr.recognize(self.r.crop(img, "dialog_text"))
        opt_crop = self.r.crop(img, "dialog_options")
        ox, oy = self.r.offset(img, "dialog_options")
        opt_lines = self.ocr.recognize(opt_crop)
        options, points = [], {}
        for ln in opt_lines:
            m = OPTION_RE.match(ln.text)
            if not m:
                if options:  # continuation of a wrapped option
                    options[-1].text += " " + ln.text.strip()
                continue
            idx = int(m.group(1))
            options.append(DialogOption(idx, m.group(2).strip()))
            x0, y0, x1, y1 = ln.box
            points[idx] = (((ox + (x0 + x1) / 2) / scale), ((oy + (y0 + y1) / 2) / scale))
        npc = " ".join(l.text for l in npc_lines).strip() or None
        text = " ".join(l.text for l in text_lines).strip()
        return Dialog(npc=npc, text=text, options=options, option_points=points)
