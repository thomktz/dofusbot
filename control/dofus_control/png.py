"""Minimal dependency-free PNG writer/reader for RGB uint8 arrays (CLI `capture`)."""
from __future__ import annotations

import struct
import zlib

import numpy as np


def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def encode_png(rgb: np.ndarray, level: int = 6) -> bytes:
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] not in (3, 4):
        raise ValueError("expected HxWx3 or HxWx4 uint8")
    h, w, c = rgb.shape
    colour_type = 2 if c == 3 else 6
    raw = np.empty((h, 1 + w * c), dtype=np.uint8)
    raw[:, 0] = 0  # filter: none
    raw[:, 1:] = rgb.reshape(h, w * c)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, colour_type, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw.tobytes(), level))
        + _chunk(b"IEND", b"")
    )


def write_png(path: str, rgb: np.ndarray) -> None:
    with open(path, "wb") as f:
        f.write(encode_png(np.ascontiguousarray(rgb)))


def decode_png(data: bytes) -> np.ndarray:
    """Decode PNGs written by encode_png (8-bit RGB/RGBA, filter 0 only)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos = 8
    idat = b""
    w = h = ct = 0
    while pos < len(data):
        (n,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if tag == b"IHDR":
            w, h, bd, ct, _, _, _ = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
    c = 3 if ct == 2 else 4
    raw = np.frombuffer(zlib.decompress(idat), dtype=np.uint8).reshape(h, 1 + w * c)
    if raw[:, 0].any():
        raise ValueError("only filter type 0 supported")
    return raw[:, 1:].reshape(h, w, c).copy()
