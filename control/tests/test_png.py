import numpy as np

from dofus_control.png import decode_png, encode_png, write_png


def test_png_roundtrip(tmp_path):
    img = np.random.default_rng(0).integers(0, 255, (17, 23, 3), dtype=np.uint8)
    assert (decode_png(encode_png(img)) == img).all()
    p = tmp_path / "x.png"
    write_png(str(p), img)
    assert p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    rgba = np.zeros((2, 2, 4), np.uint8)
    assert decode_png(encode_png(rgba)).shape == (2, 2, 4)
