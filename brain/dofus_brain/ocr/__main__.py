"""OCR an image from the command line.

    python -m dofus_brain.ocr shot.png
    python -m dofus_brain.ocr shot.png --crop 0.008,0.05,0.16,0.045 --upscale 3   # fractions
    python -m dofus_brain.ocr shot.png --crop 10,40,200,36 --backend vision --level fast
    python -m dofus_brain.ocr shot.png --region map_coords      # crop from config/regions.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from ..config import load_json
from . import crop, load_image, make_ocr, parse_rect, preprocess, upscale


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m dofus_brain.ocr", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("--crop", help="x,y,w,h (all <= 1 => fractions of the image, else pixels)")
    ap.add_argument("--region", help="name of a region in config/regions.json")
    ap.add_argument("--backend", default=None, help="auto|vision|tesseract|template (default: config)")
    ap.add_argument("--level", default="accurate", choices=["accurate", "fast"], help="Vision recognition level")
    ap.add_argument("--lang", default="fr-FR,en-US", help="Vision languages, comma separated")
    ap.add_argument("--upscale", type=float, default=1.0)
    ap.add_argument("--gray", action="store_true")
    ap.add_argument("--invert", action="store_true")
    ap.add_argument("--autocontrast", action="store_true")
    ap.add_argument("--repeat", type=int, default=1, help="run N times and report mean latency")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--save-crop", help="write the preprocessed crop to this PNG")
    a = ap.parse_args(argv)

    img = load_image(a.image)
    if a.region:
        r = load_json("regions")["regions"][a.region]
        img = crop(img, (r["x"], r["y"], r["w"], r["h"]), fractional=True)
    if a.crop:
        img = crop(img, parse_rect(a.crop))
    img = upscale(img, a.upscale)
    if a.gray or a.invert or a.autocontrast:
        img = preprocess(img, gray=a.gray, invert=a.invert, autocontrast=a.autocontrast)
    if a.save_crop:
        from PIL import Image

        Image.fromarray(img).save(a.save_crop)

    cfg = dict(load_json("regions").get("ocr", {}))
    cfg["languages"] = a.lang.split(",")
    cfg["level"] = a.level
    t0 = time.perf_counter()
    ocr = make_ocr(cfg, backend=a.backend)
    t_init = time.perf_counter() - t0
    times = []
    lines = []
    for _ in range(max(1, a.repeat)):
        t = time.perf_counter()
        lines = ocr.recognize(img)
        times.append(time.perf_counter() - t)
    ms = 1000 * sum(times) / len(times)
    if a.json:
        print(json.dumps({"backend": ocr.name, "init_ms": round(t_init * 1000, 1), "ms": round(ms, 1),
                          "size": [int(img.shape[1]), int(img.shape[0])],
                          "lines": [{"text": l.text, "box": list(l.box), "conf": round(l.conf, 3)} for l in lines]},
                         ensure_ascii=False, indent=2))
    else:
        print(f"# backend={ocr.name} size={img.shape[1]}x{img.shape[0]} init={t_init*1000:.0f}ms "
              f"recognize={ms:.1f}ms (mean of {len(times)})")
        for l in lines:
            print(f"{l.conf:.2f}  {l.box}  {l.text}")
        if not lines:
            print("(no text)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
