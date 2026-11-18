import pytest

from dofus_control.actions import check_in_window, validate_action
from dofus_control.errors import InvalidAction
from dofus_control.keys import (
    FLAG_ALT, FLAG_CMD, FLAG_CTRL, FLAG_SHIFT, is_kill_hotkey, modifier_flags,
    normalize_modifiers, resolve_key, utf16_len,
)


def test_named_keys():
    assert resolve_key("enter").keycode == 36
    assert resolve_key("escape").keycode == 53
    assert resolve_key("space").keycode == 49
    assert resolve_key("tab").keycode == 48
    assert resolve_key("backspace").keycode == 51
    assert [resolve_key(k).keycode for k in ("left", "right", "down", "up")] == [123, 124, 125, 126]
    assert [resolve_key(f"f{i}").keycode for i in range(1, 13)] == [122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111]
    assert resolve_key("F5").keycode == 96


def test_chars_us_and_azerty():
    assert resolve_key("a") .keycode == 0
    assert resolve_key("a", "azerty").keycode == 12
    assert resolve_key("q", "azerty").keycode == 0
    assert resolve_key("z", "azerty").keycode == 13
    assert resolve_key("m", "azerty").keycode == 41
    assert resolve_key("k", "azerty").keycode == 40 == resolve_key("k").keycode
    assert resolve_key("1").keycode == 18 == resolve_key("1", "azerty").keycode
    a = resolve_key("A")
    assert a.keycode == 0 and a.shift and a.char == "A"
    assert resolve_key("!").shift and resolve_key("!").keycode == 18
    assert resolve_key("a").char == "a"
    assert resolve_key("enter").char is None
    for bad in ("", "nope", "é"):
        with pytest.raises(InvalidAction):
            resolve_key(bad)


def test_modifiers():
    assert normalize_modifiers(["shift", "cmd", "ctrl", "option"]) == ("ctrl", "alt", "shift", "cmd")
    assert modifier_flags(["cmd", "shift"]) == FLAG_CMD | FLAG_SHIFT
    with pytest.raises(InvalidAction):
        normalize_modifiers(["hyper"])


def test_kill_hotkey():
    assert is_kill_hotkey(40, FLAG_CTRL | FLAG_ALT | FLAG_CMD)
    assert is_kill_hotkey(40, FLAG_CTRL | FLAG_ALT | FLAG_CMD | FLAG_SHIFT | 0x100)
    assert not is_kill_hotkey(40, FLAG_CTRL | FLAG_CMD)
    assert not is_kill_hotkey(41, FLAG_CTRL | FLAG_ALT | FLAG_CMD)


def test_utf16_len():
    assert utf16_len("abc") == 3
    assert utf16_len("é") == 1
    assert utf16_len("😀") == 2


@pytest.mark.parametrize("a", [
    {"kind": "click", "x": 1, "y": 2},
    {"kind": "click", "x": 1.5, "y": 2, "button": "right", "double": True, "reason": "r", "cell": 300},
    {"kind": "move", "x": 1, "y": 2},
    {"kind": "drag", "x": 1, "y": 2, "to_x": 3, "to_y": 4},
    {"kind": "key", "key": "enter"},
    {"kind": "key", "key": "a", "modifiers": ["cmd"]},
    {"kind": "type", "text": "héllo"},
    {"kind": "scroll", "x": 1, "y": 2, "dy": -3},
    {"kind": "wait", "ms": 100},
])
def test_valid_actions(a):
    out = validate_action(a)
    assert out["kind"] == a["kind"]


@pytest.mark.parametrize("a", [
    None, {}, {"kind": "jump"},
    {"kind": "click", "x": 1},
    {"kind": "click", "x": "1", "y": 2},
    {"kind": "click", "x": True, "y": 2},
    {"kind": "click", "x": float("nan"), "y": 2},
    {"kind": "click", "x": 1, "y": 2, "button": "middle"},
    {"kind": "click", "x": 1, "y": 2, "buton": "left"},
    {"kind": "key", "key": "nope"},
    {"kind": "key", "key": "a", "modifiers": ["hyper"]},
    {"kind": "type", "text": 5},
    {"kind": "scroll", "x": 1, "y": 2, "dy": 1.5},
    {"kind": "wait", "ms": -1},
    {"kind": "wait", "ms": 10 ** 9},
])
def test_invalid_actions(a):
    with pytest.raises(InvalidAction):
        validate_action(a)


def test_check_in_window():
    check_in_window(validate_action({"kind": "click", "x": 0, "y": 800}), 1280, 800)
    with pytest.raises(InvalidAction, match="WINDOW POINTS"):
        check_in_window(validate_action({"kind": "click", "x": 2560, "y": 10}), 1280, 800)
    with pytest.raises(InvalidAction):
        check_in_window(validate_action({"kind": "drag", "x": 1, "y": 1, "to_x": 5, "to_y": -1}), 1280, 800)


def test_auto_layout_uses_detected_keymap():
    from dofus_control import keys

    # French AZERTY excerpt: a/q and z/w swapped, digits need shift on the number row
    keys.set_detected_keymap({"a": 12, "q": 0, "z": 13, "w": 6, "&": 18, ",": 46},
                             {"1": 18, "?": 46})
    assert keys.resolve_key("a", "auto") == keys.KeySpec(12, "a")
    assert keys.resolve_key("A", "auto") == keys.KeySpec(12, "A", shift=True)
    assert keys.resolve_key("?", "auto") == keys.KeySpec(46, "?", shift=True)
    assert keys.resolve_key("1", "auto") == keys.KeySpec(18, "1")   # physical row, no shift
    assert keys.resolve_key("enter", "auto").keycode == 36
    keys.set_detected_keymap({}, {})  # detection failed -> US fallback
    assert keys.resolve_key("a", "auto").keycode == 0


@pytest.mark.skipif(__import__("sys").platform != "darwin", reason="reads the real macOS layout")
def test_real_layout_detection_covers_letters():
    from dofus_control.macos.layout import current_keymap

    plain, shifted = current_keymap()
    assert set("abcdefghijklmnopqrstuvwxyz") <= set(plain)
    # US: a=0,q=12 ; AZERTY: a=12,q=0 -- either way they are distinct physical keys
    assert {plain["a"], plain["q"]} == {0, 12}
    assert shifted["A"] == plain["a"]


@pytest.mark.parametrize("char,keycode,digit", [
    ("&", 18, "1"), ("é", 19, "2"), ('"', 20, "3"), ("'", 21, "4"), ("(", 23, "5"),
    ("§", 22, "6"), ("è", 26, "7"), ("!", 28, "8"), ("ç", 25, "9"), ("à", 29, "0"),
])
def test_azerty_number_row_unshifted(char, keycode, digit):
    """Spell bar keys on AZERTY: the unshifted number-row characters hit the same physical
    key as the digit, without shift."""
    from dofus_control.keys import resolve_key

    spec = resolve_key(char, "azerty")
    assert (spec.keycode, spec.shift, spec.char) == (keycode, False, char)
    assert resolve_key(digit, "azerty").keycode == keycode
