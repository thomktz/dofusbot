import numpy as np
import pytest

from dofus_control.geometry import (
    content_from_frame, covering_windows, crop_content, frame_for_content,
    frontmost_pid_from_order, parse_axvalue_description, parse_macos_version,
    point_in_corner, rgba_buffer_to_rgb, screen_to_window, select_window,
    title_bar_from_close_button, window_to_screen, clamp_to_rect,
)
from dofus_control.types import RawWindow, Rect


def W(wid, pid, owner, rect, title=None, layer=0, on=True, alpha=1.0):
    return RawWindow(wid, pid, owner, title, layer, Rect(*rect), alpha, on)


def test_rect_basics():
    r = Rect.parse("10, 20, 300,400")
    assert r == Rect(10, 20, 300, 400)
    assert r.right == 310 and r.bottom == 420 and r.center == (160, 220)
    assert r.contains(10, 20) and not r.contains(310, 20)
    assert r.intersection(Rect(300, 410, 50, 50)) == Rect(300, 410, 10, 10)
    assert Rect(0, 0, 1, 1).intersection(Rect(5, 5, 1, 1)) is None
    assert r.approx_equal(Rect(10.5, 20, 300, 400.5), 1)
    assert not r.approx_equal(Rect(12, 20, 300, 400), 1)
    assert Rect.from_dict(r.as_dict()) == r
    with pytest.raises(ValueError):
        Rect.parse("1,2,3")


def test_frame_content_roundtrip():
    f = Rect(100, 50, 1280, 828)
    c = content_from_frame(f, 28)
    assert c == Rect(100, 78, 1280, 800)
    assert frame_for_content(c, 28) == f
    assert content_from_frame(f, 0) == f


def test_window_screen_conversion():
    c = Rect(100, 78, 1280, 800)
    assert window_to_screen(c, 10, 20) == (110, 98)
    assert screen_to_window(c, 110, 98) == (10, 20)
    assert clamp_to_rect(-5, 900, Rect(0, 0, 1280, 800)) == (0.5, 799.5)


def test_title_bar_from_close_button():
    frame = Rect(100, 50, 800, 600)
    # standard 28pt bar: 14pt button centred at y+14
    assert title_bar_from_close_button(frame, Rect(107, 57, 14, 14)) == 28
    # slightly off estimate snaps to standard
    assert title_bar_from_close_button(frame, Rect(107, 56, 14, 16)) == 28
    # unified toolbar ~52pt
    assert title_bar_from_close_button(frame, Rect(120, 69, 14, 14)) == 52
    assert title_bar_from_close_button(frame, None) is None
    assert title_bar_from_close_button(frame, Rect(0, 500, 14, 14)) is None


def test_select_window_largest_and_filters():
    wins = [
        W(1, 10, "Dofus", (0, 0, 400, 300), "Small"),
        W(2, 10, "Dofus", (0, 0, 1280, 828), "Main"),
        W(3, 10, "Dofus", (0, 0, 2000, 2000), "Overlay", layer=3),   # not normal layer
        W(4, 11, "Dofus Helper", (0, 0, 3000, 2000), "H"),
        W(5, 10, "Dofus", (0, 0, 30, 30), "tiny"),
        W(6, 12, "TextEdit", (0, 0, 500, 500), None),
    ]
    assert select_window(wins, "dofus").window_id == 2   # exact owner match beats larger substring match
    assert select_window(wins, "helper").window_id == 4
    assert select_window(wins, "dofus", "small").window_id == 1
    assert select_window(wins, "textedit", "anything") is None   # missing titles never match
    assert select_window(wins, "textedit").window_id == 6
    assert select_window(wins, "nope") is None


def test_select_prefers_onscreen():
    wins = [W(1, 1, "App", (0, 0, 2000, 2000), on=False), W(2, 1, "App", (0, 0, 500, 500))]
    assert select_window(wins, "app").window_id == 2


def test_covering_windows_and_front():
    wins = [
        W(9, 30, "Chrome", (0, 0, 800, 600)),        # in front, overlapping
        W(8, 31, "Notes", (2000, 0, 300, 300)),      # in front, not overlapping
        W(7, 10, "Dofus", (100, 100, 400, 300), layer=0),
        W(6, 40, "Finder", (0, 0, 3000, 3000)),      # behind
    ]
    cov = covering_windows(wins, 7, Rect(100, 128, 400, 272))
    assert [w.window_id for w in cov] == [9]
    assert covering_windows(wins, 7, Rect(1000, 1000, 10, 10)) == []
    assert covering_windows(wins, 999, Rect(0, 0, 10, 10)) == []
    assert frontmost_pid_from_order(wins) == 30


def test_point_in_corner():
    d = [Rect(0, 0, 1728, 1117), Rect(-1920, -200, 1920, 1080)]
    assert point_in_corner(0, 0, d)
    assert point_in_corner(2, 1, d)
    assert point_in_corner(-1920, -200, d)
    assert not point_in_corner(10, 0, d)
    assert not point_in_corner(800, 500, d)


def test_crop_content():
    img = np.zeros((int(828 * 2), 1280 * 2, 3), np.uint8)
    img[:56] = 255
    crop, scale = crop_content(img, Rect(100, 50, 1280, 828), Rect(100, 78, 1280, 800), expected_scale=2.0)
    assert scale == 2.0
    assert crop.shape == (1600, 2560, 3)
    assert crop.max() == 0
    # non-retina measured
    img1 = np.zeros((828, 1280, 4), np.uint8)
    crop1, s1 = crop_content(img1, Rect(0, 0, 1280, 828), Rect(0, 28, 1280, 800))
    assert s1 == 1.0 and crop1.shape == (800, 1280, 3)


def test_rgba_buffer_to_rgb_orders():
    px = bytes([1, 2, 3, 4, 5, 6, 7, 8, 0, 0, 0, 0])  # 2 px + 4 bytes row padding
    rgb = rgba_buffer_to_rgb(px, 2, 1, 12, "RGBA")
    assert rgb.tolist() == [[[1, 2, 3], [5, 6, 7]]]
    assert rgba_buffer_to_rgb(px, 2, 1, 12, "BGRA").tolist() == [[[3, 2, 1], [7, 6, 5]]]
    assert rgba_buffer_to_rgb(px, 2, 1, 12, "ARGB").tolist() == [[[2, 3, 4], [6, 7, 8]]]
    with pytest.raises(ValueError):
        rgba_buffer_to_rgb(px, 2, 2, 12)


def test_parse_helpers():
    s = "<AXValue 0x6000> {value = x:12.500000 y:-34.000000 type = kAXValueCGPointType}"
    assert parse_axvalue_description(s, "point") == (12.5, -34.0)
    s2 = "<AXValue 0x6000> {value = w:1280.000000 h:828.000000 type = kAXValueCGSizeType}"
    assert parse_axvalue_description(s2, "size") == (1280.0, 828.0)
    assert parse_axvalue_description("garbage", "size") is None
    assert parse_macos_version("14.5") == (14, 5, 0)
    assert parse_macos_version("15.1.1") == (15, 1, 1)
    assert parse_macos_version("") == (0, 0, 0)
