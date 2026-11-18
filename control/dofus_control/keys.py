"""Key names (protocol §2.3) -> macOS virtual keycodes and CGEvent modifier flags.

macOS virtual keycodes are *physical* key positions (ANSI layout). The letter a
keycode produces depends on the active keyboard layout: keycode 0 is "a" on a US
layout but "q" on a French AZERTY layout. ``layout="auto"`` (the default) reads
the active macOS layout at runtime (UCKeyTranslate). ``resolve_key(..., layout="azerty")``
uses AZERTY positions for letters and punctuation so that pressing "a" hits the
key labelled A on an AZERTY Mac keyboard. In addition, every printable key event
also carries the intended character via CGEventKeyboardSetUnicodeString, so
Cocoa text fields receive the right character either way. To *type text* use the
``type`` action (unicode string API), which is layout independent.
"""
from __future__ import annotations

from dataclasses import dataclass

from .errors import InvalidAction

# ANSI (US) physical layout, kVK_* from HIToolbox/Events.h
US_KEYCODES: dict[str, int] = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26,
    "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35,
    "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45,
    "m": 46, ".": 47, "`": 50,
}

# French AZERTY (Mac) letter/punctuation positions. Digits stay on keycodes 18..29
# (the physical number row; on AZERTY they need shift to produce digits, but games
# bind the physical key, which is what we press).
AZERTY_OVERRIDES: dict[str, int] = {
    "a": 12, "z": 13, "q": 0, "w": 6, "m": 41,
    ",": 46, ";": 43, ":": 47, "=": 44,
    # number row WITHOUT shift (what spell bars show on AZERTY): & é " ' ( § è ! ç à
    "&": 18, "é": 19, '"': 20, "'": 21, "(": 23, "§": 22, "è": 26, "!": 28, "ç": 25, "à": 29,
}

NAMED_KEYS: dict[str, int] = {
    "enter": 36, "return": 36,
    "tab": 48,
    "space": 49,
    "backspace": 51,
    "escape": 53, "esc": 53,
    "delete": 117, "forwarddelete": 117,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
    "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
    "keypadenter": 76,
}

# CGEventFlags (CGEventTypes.h)
FLAG_SHIFT = 0x00020000
FLAG_CTRL = 0x00040000
FLAG_ALT = 0x00080000
FLAG_CMD = 0x00100000
FLAG_FN = 0x00800000

MODIFIER_FLAGS: dict[str, int] = {
    "shift": FLAG_SHIFT, "ctrl": FLAG_CTRL, "alt": FLAG_ALT, "cmd": FLAG_CMD,
}
MODIFIER_KEYCODES: dict[str, int] = {"cmd": 55, "shift": 56, "alt": 58, "ctrl": 59}
MODIFIER_ALIASES = {
    "command": "cmd", "meta": "cmd", "super": "cmd",
    "control": "ctrl", "option": "alt", "opt": "alt",
}
#: Canonical press order for modifiers.
MODIFIER_ORDER = ("ctrl", "alt", "shift", "cmd")

KEYCODE_K = 40  # same physical key on US and AZERTY

_SHIFTED_US = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8",
    "(": "9", ")": "0", "_": "-", "+": "=", "{": "[", "}": "]", "|": "\\", ":": ";",
    '"': "'", "<": ",", ">": ".", "?": "/", "~": "`",
}

LAYOUTS = ("auto", "us", "azerty")

_AUTO: tuple[dict[str, int], dict[str, int]] | None = None


def detected_keymap() -> tuple[dict[str, int], dict[str, int]]:
    """(plain, shifted) char -> keycode of the active macOS layout; empty off macOS. Cached."""
    global _AUTO
    if _AUTO is None:
        import sys

        _AUTO = ({}, {})
        if sys.platform == "darwin":
            from .macos.layout import current_keymap

            _AUTO = current_keymap()
    return _AUTO


def set_detected_keymap(plain: dict[str, int] | None, shifted: dict[str, int] | None = None) -> None:
    """Override (or with None, reset) the cached layout detection; used by tests."""
    global _AUTO
    _AUTO = None if plain is None else (dict(plain), dict(shifted or {}))


@dataclass(frozen=True)
class KeySpec:
    keycode: int
    char: str | None      # character to attach as the unicode string (None for named keys)
    shift: bool = False   # shift must be held (uppercase letters, US shifted symbols)


def layout_table(layout: str) -> dict[str, int]:
    layout = (layout or "us").lower()
    if layout == "auto":
        plain = detected_keymap()[0]
        return dict(plain) if plain else dict(US_KEYCODES)
    if layout not in LAYOUTS:
        raise ValueError(f"unknown keyboard layout {layout!r}; use one of {LAYOUTS}")
    table = dict(US_KEYCODES)
    if layout == "azerty":
        # keep letters and digits (most letters share positions with US), drop US
        # punctuation positions (they differ on AZERTY), then apply the overrides
        table = {k: v for k, v in table.items() if k.isalnum()}
        table.update(AZERTY_OVERRIDES)
    return table


def resolve_key(name: str, layout: str = "us") -> KeySpec:
    """Translate a protocol key name into a KeySpec. Raises InvalidAction."""
    if not isinstance(name, str) or name == "":
        raise InvalidAction(f"invalid key name {name!r}")
    low = name.lower()
    if len(name) > 1:
        if low in NAMED_KEYS:
            return KeySpec(NAMED_KEYS[low], None)
        raise InvalidAction(f"unknown key name {name!r}")
    if name == " ":
        return KeySpec(NAMED_KEYS["space"], " ")
    if (layout or "us").lower() == "auto":
        plain, shifted = detected_keymap()
        if plain:
            if name.isdigit():  # physical number row, like the azerty table (games bind the key)
                return KeySpec(US_KEYCODES[name], name)
            if name in plain:
                return KeySpec(plain[name], name)
            if name in shifted:
                return KeySpec(shifted[name], name, shift=True)
            if name.isalpha() and low in plain:
                return KeySpec(plain[low], name, shift=True)
            raise InvalidAction(f"no key produces {name!r} on the current layout; use a 'type' action for text")
        layout = "us"
    table = layout_table(layout)
    if name in table:
        return KeySpec(table[name], name)
    if name.isalpha() and low in table:  # uppercase letter
        return KeySpec(table[low], name, shift=True)
    if layout.lower() == "us" and name in _SHIFTED_US:
        return KeySpec(table[_SHIFTED_US[name]], name, shift=True)
    raise InvalidAction(f"no keycode for {name!r} on layout {layout!r}; use a 'type' action for text")


def normalize_modifiers(mods) -> tuple[str, ...]:
    """Validate and order modifier names; raises InvalidAction."""
    if mods is None:
        return ()
    if isinstance(mods, str):
        mods = [mods]
    out = set()
    for m in mods:
        if not isinstance(m, str):
            raise InvalidAction(f"invalid modifier {m!r}")
        m2 = MODIFIER_ALIASES.get(m.lower(), m.lower())
        if m2 not in MODIFIER_FLAGS:
            raise InvalidAction(f"unknown modifier {m!r}; use cmd, ctrl, alt, shift")
        out.add(m2)
    return tuple(m for m in MODIFIER_ORDER if m in out)


def modifier_flags(mods) -> int:
    flags = 0
    for m in normalize_modifiers(mods):
        flags |= MODIFIER_FLAGS[m]
    return flags


def is_kill_hotkey(keycode: int, flags: int) -> bool:
    """ctrl+alt+cmd+K (shift allowed or not)."""
    need = FLAG_CTRL | FLAG_ALT | FLAG_CMD
    return int(keycode) == KEYCODE_K and (int(flags) & need) == need


def utf16_len(text: str) -> int:
    """Length in UTF-16 code units (what CGEventKeyboardSetUnicodeString expects)."""
    return len(text.encode("utf-16-le")) // 2
