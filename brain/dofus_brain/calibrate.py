"""Calibration helpers for the real client (and the mock).

    python -m dofus_brain.calibrate grid     [--image shot.png | --mock] [--out grid.png]
        draws the 560-cell grid (config/grid.json) over a capture: diamonds should sit on the
        game's cells. Tune ox, oy, cw, ch and re-run. --labels draws cell ids.
    python -m dofus_brain.calibrate regions  [--image shot.png | --mock] [--out regions.png]
        draws every crop of config/regions.json with its name, plus what OCR reads in it.
    python -m dofus_brain.calibrate sample X Y [--image shot.png]
        prints the RGB at window point (X, Y) (for the palette in regions.json).
    python -m dofus_brain.calibrate glyphs --region map_coords --text "4,-18" [--image shot.png]
        cuts the glyphs of a crop whose text you know into config/glyphs/ (TemplateOCR).

Without --image/--mock it captures the live Dofus window through dofus_control.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .config import load_json
from .game.grid import Grid
from .paths import config_dir


def _capture(a: argparse.Namespace) -> tuple[np.ndarray, float]:
    if a.image:
        return np.asarray(Image.open(a.image).convert("RGB")), float(a.scale)
    if a.mock:
        from .mock_control import MockController

        f = MockController().capture()
        return f.image, f.scale
    from .mock_control import make_controller

    ctl, is_mock = make_controller(mock=False)
    if is_mock:
        print("dofus_control not available: falling back to the mock capture", file=sys.stderr)
    f = ctl.capture()
    return f.image, float(f.scale)


def draw_grid(img: np.ndarray, grid: Grid, scale: float, labels: bool = False) -> Image.Image:
    im = Image.fromarray(img).convert("RGB")
    d = ImageDraw.Draw(im, "RGBA")
    for c in range(grid.n_cells):
        pts = [(x * scale, y * scale) for x, y in grid.diamond(c)]
        d.polygon(pts, outline=(255, 0, 255, 200))
        if labels:
            x, y = grid.center_px(c, scale)
            d.text((x - 8, y - 5), str(c), fill=(255, 255, 0, 255))
    for c in (0, 13, 546, 559):
        x, y = grid.center_px(c, scale)
        d.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(255, 0, 0, 255))
    return im


def draw_regions(img: np.ndarray, regions_cfg: dict, with_ocr: bool = True) -> Image.Image:
    from .ocr import make_ocr, text_of
    from .perception.regions import Regions

    r = Regions(regions_cfg)
    im = Image.fromarray(img).convert("RGB")
    d = ImageDraw.Draw(im, "RGBA")
    ocr = make_ocr(regions_cfg.get("ocr", {})) if with_ocr else None
    for name, reg in r.regions.items():
        x0, y0, x1, y1 = reg.box(im.width, im.height)
        d.rectangle((x0, y0, x1, y1), outline=(0, 255, 255, 255), width=2)
        label = name
        if ocr is not None and name not in ("game_area", "hud_bar", "dialog_box"):
            t = text_of(ocr.recognize(reg.crop(img))).replace("\n", " | ")[:40]
            label += f": {t}" if t else ""
            print(f"{name:16s} {t}")
        d.rectangle((x0, y0, x0 + 7 * len(label) + 4, y0 + 14), fill=(0, 0, 0, 160))
        d.text((x0 + 2, y0 + 1), label, fill=(0, 255, 255, 255))
    return im


def cut_glyphs(img: np.ndarray, regions_cfg: dict, region: str, text: str, out: Path, threshold: int = 128) -> int:
    from .ocr.backends import _runs
    from .perception.regions import Regions

    crop = Regions(regions_cfg).crop(img, region)
    b = crop.mean(axis=-1) >= threshold
    rows = _runs(b.any(axis=1), 2)
    if not rows:
        raise SystemExit("no ink found in region (try --threshold)")
    y0, y1 = max(rows, key=lambda r: r[1] - r[0])
    line = b[y0:y1]
    segs = _runs(line.any(axis=0), 1)
    chars = [c for c in text if c != " "]
    if len(segs) != len(chars):
        raise SystemExit(f"found {len(segs)} glyphs but text has {len(chars)} non-space chars")
    bottoms = []
    for x0, x1 in segs:
        ys = np.nonzero(line[:, x0:x1].any(axis=1))[0]
        bottoms.append(int(ys.max()))
    baseline = max(set(bottoms), key=bottoms.count)
    out.mkdir(parents=True, exist_ok=True)
    for (x0, x1), ch in zip(segs, chars):
        if line[baseline + 1:, x0:x1].any():  # descender: keep it, store baseline row in the name
            Image.fromarray((line[:, x0:x1] * 255).astype(np.uint8)).save(out / f"{ord(ch):04x}_b{baseline}.png")
        else:
            Image.fromarray((line[: baseline + 1, x0:x1] * 255).astype(np.uint8)).save(out / f"{ord(ch):04x}.png")
    return len(segs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m dofus_brain.calibrate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["grid", "regions", "sample", "glyphs"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--image")
    ap.add_argument("--scale", type=float, default=1.0, help="capture pixels per point for --image (2 on Retina)")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--labels", action="store_true")
    ap.add_argument("--region")
    ap.add_argument("--text")
    ap.add_argument("--threshold", type=int, default=128)
    a = ap.parse_args(argv)
    img, scale = _capture(a)
    if a.what == "grid":
        g = Grid.from_config(load_json("grid"))
        out = Path(a.out or "calib_grid.png")
        draw_grid(img, g, scale, a.labels).save(out)
        print(f"wrote {out} (grid ox={g.ox} oy={g.oy} cw={g.cw} ch={g.ch}, scale={scale})")
    elif a.what == "regions":
        out = Path(a.out or "calib_regions.png")
        draw_regions(img, load_json("regions")).save(out)
        print(f"wrote {out}")
    elif a.what == "sample":
        x, y = float(a.args[0]), float(a.args[1])
        px = img[int(y * scale), int(x * scale)]
        print(f"window point ({x},{y}) -> RGB {list(map(int, px))}")
    elif a.what == "glyphs":
        if not (a.region and a.text):
            ap.error("glyphs needs --region and --text")
        n = cut_glyphs(img, load_json("regions"), a.region, a.text, Path(a.out or config_dir() / "glyphs"), a.threshold)
        print(f"saved {n} glyph templates")
    return 0


if __name__ == "__main__":
    sys.exit(main())
