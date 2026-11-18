import numpy as np
import pytest

from dofus_control.errors import CalibrationFailed
from dofus_control.markers import connected_components, draw_markers, find_slot, marker_candidates
from dofus_control.types import DEFAULT_MARKERS, Rect


def screen(h=2234, w=3456, seed=0):
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 200, size=(h, w, 3), dtype=np.uint8)  # noisy desktop, no pure colours
    return img


def test_connected_components_basic():
    m = np.zeros((10, 10), bool)
    m[1:3, 1:4] = True
    m[5:9, 5:6] = True
    m[5, 6:8] = True     # attached to the second blob
    m[0, 9] = True
    blobs = sorted(connected_components(m), key=lambda b: (b.y0, b.x0))
    assert len(blobs) == 3
    b0 = [b for b in blobs if b.x0 == 1][0]
    assert (b0.x0, b0.y0, b0.x1, b0.y1, b0.count) == (1, 1, 4, 3, 6)
    b1 = [b for b in blobs if b.x0 == 5][0]
    assert (b1.x1, b1.y1, b1.count) == (8, 9, 6)


def test_connected_components_u_shape_merges():
    m = np.zeros((5, 5), bool)
    m[0:4, 0] = True
    m[0:4, 4] = True
    m[4, :] = True
    blobs = connected_components(m)
    assert len(blobs) == 1 and blobs[0].count == 13


def test_find_slot_retina():
    img = screen()
    # slot at screen points (300, 120) 1280x800, scale 2
    rect_px = Rect(600, 240, 2560, 1600)
    draw_markers(img, rect_px, 24)
    det = find_slot(img, scale_hint=2.0)
    assert det.rect_px == rect_px
    assert det.to_screen(2.0) == Rect(300, 120, 1280, 800)
    assert det.to_screen(2.0, (-1728, 0)) == Rect(-1428, 120, 1280, 800)
    assert det.warnings == []
    assert det.marker_px == 24


def test_find_slot_tolerates_colour_shift_and_decoys():
    img = screen(1200, 1900, seed=1)
    rect_px = Rect(100, 100, 1280, 800)
    draw_markers(img, rect_px, 12)
    # colour-managed capture: slightly off colours
    tl = img[100:112, 100:112].astype(int)
    img[100:112, 100:112] = np.clip(tl + [-20, 15, -25], 0, 255).astype(np.uint8)
    # decoy magenta blob far away (wrong size and not aligned)
    img[1000:1040, 1700:1740] = (255, 0, 255)
    # decoy thin green line (not square)
    img[50:52, 300:700] = (0, 255, 0)
    det = find_slot(img, scale_hint=1.0)
    assert det.rect_px == rect_px


def test_find_slot_zoom_warning():
    img = screen(1000, 1600, seed=2)
    draw_markers(img, Rect(10, 10, 1500, 900), 18)  # 12 css px at 150% zoom on 1x
    det = find_slot(img, scale_hint=1.0)
    assert det.warnings and "zoom" in det.warnings[0]


def test_find_slot_missing_marker():
    img = screen(800, 1200, seed=3)
    draw_markers(img, Rect(10, 10, 1000, 700), 12)
    img[698:710, 998:1010] = (0, 0, 0)  # remove BR
    with pytest.raises(CalibrationFailed, match="br"):
        find_slot(img)


def test_find_slot_not_rectangle():
    img = screen(800, 1200, seed=4)
    draw_markers(img, Rect(10, 10, 1000, 700), 12)
    blk = img[698:710, 998:1010].copy()
    img[698:710, 998:1010] = (0, 0, 0)
    img[640:652, 998:1010] = blk           # BR moved up: bottom edge disagrees
    with pytest.raises(CalibrationFailed):
        find_slot(img)


def test_marker_candidates_filters_hollow():
    img = np.zeros((100, 100, 3), np.uint8)
    img[10:30, 10:30] = (255, 0, 255)
    img[12:28, 12:28] = 0      # hollow square: low fill
    img[50:60, 50:60] = (255, 0, 255)
    c = marker_candidates(img, DEFAULT_MARKERS.tl)
    assert len(c) == 1 and c[0].x0 == 50
