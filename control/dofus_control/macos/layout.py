"""Read the active keyboard layout: character -> virtual keycode (no modifiers).

Uses Carbon's TISCopyCurrentKeyboardLayoutInputSource + UCKeyTranslate through
ctypes (PyObjC does not wrap the TextInputSources API). Lets ``key`` actions hit
the physical key that produces the requested character on *any* layout (US,
AZERTY, "French - numerical", QWERTZ...).
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging

log = logging.getLogger(__name__)

_kUCKeyActionDisplay = 3
_kUCKeyTranslateNoDeadKeysBit = 0
_SHIFT_STATE = (0x02 << 8) >> 8  # shiftKey >> 8 for UCKeyTranslate modifierKeyState


def _load():
    carbon = ctypes.cdll.LoadLibrary(ctypes.util.find_library("Carbon"))
    cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
    carbon.TISCopyCurrentKeyboardLayoutInputSource.restype = ctypes.c_void_p
    carbon.TISGetInputSourceProperty.restype = ctypes.c_void_p
    carbon.TISGetInputSourceProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    carbon.LMGetKbdType.restype = ctypes.c_uint8
    carbon.UCKeyTranslate.restype = ctypes.c_int32
    carbon.UCKeyTranslate.argtypes = [
        ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p,
    ]
    cf.CFDataGetBytePtr.restype = ctypes.c_void_p
    cf.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    prop = ctypes.c_void_p.in_dll(carbon, "kTISPropertyUnicodeKeyLayoutData")
    return carbon, cf, prop


def current_keymap() -> tuple[dict[str, int], dict[str, int]]:
    """Return (plain, shifted): char -> keycode for the active layout. Empty dicts on failure."""
    plain: dict[str, int] = {}
    shifted: dict[str, int] = {}
    try:
        carbon, cf, prop = _load()
        src = carbon.TISCopyCurrentKeyboardLayoutInputSource()
        if not src:
            return plain, shifted
        try:
            data = carbon.TISGetInputSourceProperty(src, prop)
            if not data:
                return plain, shifted
            layout = cf.CFDataGetBytePtr(data)
            kbd = carbon.LMGetKbdType()
            buf = (ctypes.c_uint16 * 4)()
            for state, table in ((0, plain), (_SHIFT_STATE, shifted)):
                for code in range(128):
                    if 65 <= code <= 92:  # keypad: prefer the main block
                        continue
                    dead = ctypes.c_uint32(0)
                    n = ctypes.c_ulong(0)
                    err = carbon.UCKeyTranslate(layout, code, _kUCKeyActionDisplay, state, kbd,
                                                1 << _kUCKeyTranslateNoDeadKeysBit, ctypes.byref(dead),
                                                4, ctypes.byref(n), buf)
                    if err == 0 and n.value == 1:
                        ch = chr(buf[0])
                        if ch.isprintable() and ch not in table:
                            table[ch] = code
        finally:
            cf.CFRelease(src)
    except Exception as e:  # pragma: no cover - depends on the OS
        log.warning("keyboard layout detection failed (%s); falling back to US", e)
    return plain, shifted
