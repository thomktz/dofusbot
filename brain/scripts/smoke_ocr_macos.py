#!/usr/bin/env python3
"""Smoke test of Apple Vision OCR on the Mac (run after `pip install -e 'brain[mac]'`).

    python brain/scripts/smoke_ocr_macos.py                 # synthetic French test card
    python brain/scripts/smoke_ocr_macos.py --image shot.png # + OCR a real Dofus capture
    python brain/scripts/smoke_ocr_macos.py --screen         # + OCR the current screen (screencapture)

Checks: PyObjC/Vision import, supported languages (fr-FR must be there), accurate vs fast
latency, accents, HUD-style coordinates ('-12,34') with crop + upscale, and the fixtures.
Exit code 0 when every check passed.
"""
from __future__ import annotations

import argparse
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dofus_brain.ocr import VisionOCR, crop, load_image, text_of, upscale  # noqa: E402
from dofus_brain.perception.hud import parse_coords  # noqa: E402

MAC_FONTS = ["/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc",
             "/Library/Fonts/Arial.ttf", "/System/Library/Fonts/SFNS.ttf"]
LINES = ["Bonjour aventurier, que cherches-tu ?", "1. Où se trouve la banque d'Astrub ?",
         "2. Je voudrais des pièces d'équipement.", "Ganymède — Quête : « Le cœur du Bouftou »"]
COORDS = "-12,34"


def font(size: int):
    for f in MAC_FONTS:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size=size)


def test_card() -> np.ndarray:
    im = Image.new("RGB", (1280, 400), (40, 34, 28))
    d = ImageDraw.Draw(im)
    for i, t in enumerate(LINES):
        d.text((30, 30 + i * 50), t, font=font(28), fill=(235, 230, 220))
    d.rectangle((20, 300, 220, 350), fill=(20, 20, 20))
    d.text((30, 308), COORDS, font=font(24), fill=(255, 255, 255))  # small HUD-like text
    return np.asarray(im)


def run(ocr: VisionOCR, img: np.ndarray, n: int = 3) -> tuple[list, float]:
    ocr.recognize(img)  # warm-up (first call loads the model)
    t = time.perf_counter()
    for _ in range(n):
        lines = ocr.recognize(img)
    return lines, (time.perf_counter() - t) * 1000 / n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", action="append", default=[])
    ap.add_argument("--screen", action="store_true")
    a = ap.parse_args()
    if platform.system() != "Darwin":
        print("This smoke test needs macOS (Apple Vision).")
        return 2
    ok = True

    def check(name: str, cond: bool, info: str = "") -> None:
        nonlocal ok
        ok &= bool(cond)
        print(f"[{'OK' if cond else 'FAIL'}] {name} {info}")

    try:
        accurate = VisionOCR(["fr-FR", "en-US"], level="accurate")
        fast = VisionOCR(["fr-FR", "en-US"], level="fast")
    except Exception as e:
        print(f"[FAIL] import Vision via PyObjC: {e}\n  -> pip install -e 'brain[mac]'")
        return 1
    langs = VisionOCR.supported_languages("accurate")
    check("fr-FR supported (accurate)", "fr-FR" in langs, f"({len(langs)} languages)")

    card = test_card()
    for name, ocr in (("accurate", accurate), ("fast", fast)):
        lines, ms = run(ocr, card)
        txt = text_of(lines)
        print(f"--- {name}: {ms:.0f} ms / frame 1280x400\n{txt}\n---")
        hits = sum(1 for l in LINES if l.split()[0] in txt)
        check(f"{name}: lines found", hits >= 3, f"{hits}/{len(LINES)}")
        if name == "accurate":
            check("accents (Où, pièces, Ganymède)", all(w in txt for w in ("Où", "pièces", "Ganymède")))
        if lines:
            b = lines[0].box
            check(f"{name}: boxes in pixels", 0 <= b[0] < b[2] <= 1280 and 0 <= b[1] < b[3] <= 400, str(b))

    hud = crop(card, (20, 300, 200, 50), fractional=False)
    for k in (1, 2, 3):
        lines, ms = run(accurate, upscale(hud, k))
        got = parse_coords(text_of(lines))
        check(f"coords crop x{k}", got == (-12, 34), f"-> {got!r} raw={text_of(lines)!r} {ms:.0f} ms")

    for fx in ("world_4_-18.png", "dialog.png"):
        p = ROOT / "fixtures" / fx
        if p.exists():
            lines, ms = run(accurate, load_image(str(p)), 1)
            print(f"fixture {fx}: {ms:.0f} ms -> {text_of(lines)[:200]!r}")

    images = list(a.image)
    if a.screen:
        tmp = Path(tempfile.mkdtemp()) / "screen.png"
        subprocess.run(["screencapture", "-x", str(tmp)], check=False)
        if tmp.exists():
            images.append(str(tmp))
        else:
            print("[WARN] screencapture failed (Screen Recording permission for the terminal?)")
    for p in images:
        img = load_image(p)
        lines, ms = run(accurate, img, 1)
        print(f"\n=== {p} ({img.shape[1]}x{img.shape[0]}) accurate {ms:.0f} ms, {len(lines)} lines")
        for l in lines[:40]:
            print(f"  {l.conf:.2f} {l.box} {l.text}")
    print("\nALL OK" if ok else "\nSOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
