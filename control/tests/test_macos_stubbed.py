"""Exercise the macOS modules' Python logic with stub PyObjC modules.

This cannot prove the real PyObjC calls are right (that's scripts/smoke_macos.py),
but it catches typos, wrong tuple unpacking and control-flow bugs on Linux.
"""
import importlib
import sys
import types

import numpy as np
import pytest

from dofus_control.types import Rect


class Stub(types.ModuleType):
    """Unknown k* constants -> their name (strings), unknown functions -> recorder."""

    def __init__(self, name):
        super().__init__(name)
        self.calls = []

    def __getattr__(self, attr):
        if attr.startswith("__"):
            raise AttributeError(attr)
        if attr.startswith("k"):
            return attr

        def fn(*a, **k):
            self.calls.append((attr, a))
            return None

        return fn


@pytest.fixture
def mac(monkeypatch):
    mods = {n: Stub(n) for n in ("Quartz", "HIServices", "AppKit", "ScreenCaptureKit")}
    for n, m in mods.items():
        monkeypatch.setitem(sys.modules, n, m)
    q = mods["Quartz"]
    # numeric constants used in arithmetic / dict keys
    for i, n in enumerate(["kCGEventMouseMoved", "kCGEventLeftMouseDown", "kCGEventLeftMouseUp",
                           "kCGEventRightMouseDown", "kCGEventRightMouseUp", "kCGEventLeftMouseDragged",
                           "kCGEventRightMouseDragged"]):
        setattr(q, n, 100 + i)
    q.kCGMouseButtonLeft, q.kCGMouseButtonRight = 0, 1
    q.kCGWindowListExcludeDesktopElements, q.kCGWindowListOptionAll, q.kCGWindowListOptionOnScreenOnly = 16, 0, 1
    q.kCGWindowImageBoundsIgnoreFraming, q.kCGWindowImageBestResolution = 1, 8
    q.kCGEventKeyDown = 10
    q.kCGEventTapDisabledByTimeout, q.kCGEventTapDisabledByUserInput = 0xFFFFFFFE, 0xFFFFFFFF
    q.CGEventMaskBit = lambda t: 1 << t
    mods["HIServices"].kAXValueTypeCGPoint, mods["HIServices"].kAXValueTypeCGSize = 1, 2
    names = ["dofus_control.macos", "dofus_control.macos.windows", "dofus_control.macos.capture",
             "dofus_control.macos.events", "dofus_control.macos.permissions", "dofus_control.macos.backend"]
    for n in names:
        sys.modules.pop(n, None)
    yield mods
    for n in names:
        sys.modules.pop(n, None)


def test_list_windows_parsing(mac):
    q = mac["Quartz"]
    q.CGWindowListCopyWindowInfo = lambda opts, wid: [
        {"kCGWindowBounds": {"X": 10, "Y": 20, "Width": 300, "Height": 200}, "kCGWindowNumber": 5,
         "kCGWindowOwnerPID": 77, "kCGWindowOwnerName": "TextEdit", "kCGWindowLayer": 0,
         "kCGWindowAlpha": 1.0, "kCGWindowIsOnscreen": True, "kCGWindowName": "Doc"},
        {"kCGWindowBounds": {"X": 0, "Y": 0, "Width": 1728, "Height": 25}, "kCGWindowNumber": 6,
         "kCGWindowOwnerPID": 1, "kCGWindowOwnerName": "Window Server", "kCGWindowLayer": 25},
    ]
    w = importlib.import_module("dofus_control.macos.windows")
    ws = w.list_windows()
    assert ws[0].bounds == Rect(10, 20, 300, 200) and ws[0].title == "Doc" and ws[0].pid == 77
    assert ws[1].title is None and ws[1].layer == 25 and ws[1].on_screen is False


def test_ax_frame_and_matching(mac):
    hi = mac["HIServices"]
    frames = {"w1": ((0, 0), (100, 100)), "w2": ((10, 20), (300, 200)), "btn": ((17, 27), (14, 14))}
    attrs = {"app": {"AXWindows": ["w1", "w2"]}, "w1": {}, "w2": {"AXCloseButton": "btn", "AXTitle": "Doc"}}

    def copy(elem, attr, out):
        assert out is None
        if attr == "AXPosition" and elem in frames:
            return 0, ("pt", frames[elem][0])
        if attr == "AXSize" and elem in frames:
            return 0, ("sz", frames[elem][1])
        v = attrs.get(elem, {}).get(attr)
        return (0, v) if v is not None else (-25212, None)

    hi.AXUIElementCopyAttributeValue = copy
    hi.AXValueGetValue = lambda v, t, out: (True, v[1])
    hi.AXUIElementCreateApplication = lambda pid: "app"
    set_calls = []
    hi.AXValueCreate = lambda t, v: (t, v)
    hi.AXUIElementSetAttributeValue = lambda e, a, v: set_calls.append((e, a, v)) or 0
    mac["AppKit"].NSWindow = types.SimpleNamespace(
        frameRectForContentRect_styleMask_=lambda r, m: types.SimpleNamespace(size=types.SimpleNamespace(height=328)))
    mac["AppKit"].NSMakeRect = lambda *a: a
    w = importlib.import_module("dofus_control.macos.windows")
    win = w.find_ax_window(77, Rect(10, 20, 300, 200))
    assert win == "w2"
    assert w.element_frame(win) == Rect(10, 20, 300, 200)
    assert w.ax_title(77, Rect(10, 20, 300, 200)) == "Doc"
    assert w.title_bar_height(win, Rect(10, 20, 300, 200)) == 28.0
    assert w.set_position(win, 5, 6) and w.set_size(win, 7, 8)
    assert set_calls[0] == ("w2", "AXPosition", (1, (5.0, 6.0)))
    assert set_calls[1] == ("w2", "AXSize", (2, (7.0, 8.0)))


def test_axvalue_fallback_parse(mac):
    hi = mac["HIServices"]

    def bad(*a):
        raise TypeError("nope")

    hi.AXValueGetValue = bad
    w = importlib.import_module("dofus_control.macos.windows")
    v = "<AXValue 0x1> {value = x:3.000000 y:4.000000 type = kAXValueCGPointType}"
    assert w._decode_pair(v, 1, "point") == (3.0, 4.0)


def test_frontmost_pid(mac):
    hi = mac["HIServices"]
    hi.AXUIElementCreateSystemWide = lambda: "sys"
    hi.AXUIElementCopyAttributeValue = lambda e, a, o: (0, "frontapp")
    hi.AXUIElementGetPid = lambda e, o: (0, 4242)
    w = importlib.import_module("dofus_control.macos.windows")
    assert w.frontmost_pid() == 4242


def test_events_post(mac):
    q = mac["Quartz"]
    posted = []
    q.CGEventCreateMouseEvent = lambda src, t, pt, b: {"t": t, "pt": pt, "b": b, "f": {}}
    q.CGEventCreateKeyboardEvent = lambda src, kc, down: {"kc": kc, "down": down, "f": {}}
    q.CGEventSetIntegerValueField = lambda ev, field, v: ev["f"].__setitem__(field, v)
    q.CGEventSetFlags = lambda ev, fl: ev.__setitem__("flags", fl)
    q.CGEventKeyboardSetUnicodeString = lambda ev, n, s: ev.__setitem__("u", (n, s))
    q.CGEventPost = lambda tap, ev: posted.append((tap, ev))
    q.CGPointMake = lambda x, y: (x, y)
    e = importlib.import_module("dofus_control.macos.events")
    e.post_mouse("down", 10, 20, "right", 2)
    tap, ev = posted[-1]
    assert tap == "kCGHIDEventTap" and ev["t"] == q.kCGEventRightMouseDown and ev["b"] == 1
    assert ev["f"]["kCGMouseEventClickState"] == 2 and ev["f"]["kCGEventSourceUserData"] == e.EVENT_MAGIC
    e.post_text("😀", True)
    assert posted[-1][1]["u"] == (2, "😀") and posted[-1][1]["flags"] == 0
    e.post_key(36, False, 0x100000, None)
    assert posted[-1][1]["kc"] == 36 and posted[-1][1]["flags"] == 0x100000 and "u" not in posted[-1][1]
    q.CGEventCreateScrollWheelEvent2 = lambda src, unit, n, a, b, c: {"scroll": a, "f": {}}
    q.CGEventSetLocation = lambda ev, pt: ev.__setitem__("loc", pt)
    e.post_scroll(1, 2, -1)
    assert posted[-1][1]["scroll"] == -1 and posted[-1][1]["loc"] == (1.0, 2.0)


def test_hotkey_tap_callback(mac):
    q = mac["Quartz"]
    q.CGEventGetIntegerValueField = lambda ev, f: ev["kc"]
    q.CGEventGetFlags = lambda ev: ev["flags"]
    e = importlib.import_module("dofus_control.macos.events")
    import threading
    hit = threading.Event()
    tap = e.HotkeyTap(hit.set)
    ev = {"kc": 40, "flags": 0x40000 | 0x80000 | 0x100000}
    assert tap._callback(None, q.kCGEventKeyDown, ev, None) is ev
    assert hit.wait(1)
    hit.clear()
    tap._callback(None, q.kCGEventKeyDown, {"kc": 40, "flags": 0x100000}, None)
    assert not hit.wait(0.1)
    # tap creation failing -> start() False
    q.CGEventTapCreate = lambda *a: None
    t2 = e.HotkeyTap(lambda: None)
    assert t2.start() is False and "Input Monitoring" in t2.error
    t2.stop()


def test_permissions(mac):
    mac["HIServices"].AXIsProcessTrusted = lambda: True
    mac["Quartz"].CGPreflightScreenCaptureAccess = lambda: False
    mac["Quartz"].CGPreflightListenEventAccess = lambda: True
    p = importlib.import_module("dofus_control.macos.permissions")
    st = p.status()
    assert st.accessibility and not st.screen_recording and st.input_monitoring
    asked = []
    mac["HIServices"].AXIsProcessTrustedWithOptions = lambda d: asked.append(d)
    p.request()
    assert asked == [{"kAXTrustedCheckOptionPrompt": True}]


def test_capture_wait_and_displays(mac, monkeypatch):
    q = mac["Quartz"]
    q.CGGetActiveDisplayList = lambda n, a, b: (0, (7, 3), 2)
    q.CGMainDisplayID = lambda: 3
    q.CGDisplayBounds = lambda d: types.SimpleNamespace(
        origin=types.SimpleNamespace(x=0 if d == 3 else 1728, y=0),
        size=types.SimpleNamespace(width=1728, height=1117))
    q.CGDisplayCopyDisplayMode = lambda d: "mode"
    q.CGDisplayModeGetPixelWidth = lambda m: 3456
    q.CGDisplayModeGetWidth = lambda m: 1728
    c = importlib.import_module("dofus_control.macos.capture")
    ds = c.displays()
    assert [d.display_id for d in ds] == [3, 7] and ds[0].is_main and ds[0].scale == 2.0
    assert c._wait(lambda h: h("ok", None), "x") == "ok"

    class Err:
        def localizedDescription(self):
            return "The user declined TCCs"

        def code(self):
            return -3801

    from dofus_control.errors import CaptureFailed, PermissionMissing
    with pytest.raises(PermissionMissing):
        c._wait(lambda h: h(None, Err()), "x")
    monkeypatch.setattr(c, "TIMEOUT_S", 0.05)
    with pytest.raises(CaptureFailed, match="timed out"):
        c._wait(lambda h: None, "x")


def test_capture_window_backs_off_from_stalled_sck(mac, monkeypatch):
    q = mac["Quartz"]
    q.CGPreflightScreenCaptureAccess = lambda: True
    c = importlib.import_module("dofus_control.macos.capture")
    monkeypatch.setattr(c, "SCK_SCREENSHOT_AVAILABLE", True)
    monkeypatch.setattr(c, "_sck_window_retry_after", 0.0)

    class Filter:
        @classmethod
        def alloc(cls):
            return cls()

        def initWithDesktopIndependentWindow_(self, window):
            return self

    monkeypatch.setattr(c, "SCK", types.SimpleNamespace(SCContentFilter=Filter))
    attempts = []

    def stalled(window_id):
        attempts.append(window_id)
        from dofus_control.errors import CaptureFailed
        raise CaptureFailed("timed out")

    monkeypatch.setattr(c, "_sc_window", stalled)
    monkeypatch.setattr(c, "_cg_capture_window", lambda window_id: np.zeros((2, 2, 3), dtype=np.uint8))
    frame = Rect(0, 0, 100, 100)
    assert c.capture_window(9, frame).shape == (2, 2, 3)
    assert c.capture_window(9, frame).shape == (2, 2, 3)
    assert attempts == [9]


def test_cgimage_to_rgb(mac):
    q = mac["Quartz"]
    q.CGImageGetWidth = lambda img: 2
    q.CGImageGetHeight = lambda img: 1
    holder = {}

    def create(buf, w, h, bpc, bpr, cs, info):
        holder["buf"] = buf
        return "ctx"

    def draw(ctx, rect, img):
        holder["buf"][:] = bytes([1, 2, 3, 255, 4, 5, 6, 255])

    q.CGBitmapContextCreate = create
    q.CGContextDrawImage = draw
    q.CGRectMake = lambda *a: a
    c = importlib.import_module("dofus_control.macos.capture")
    out = c.cgimage_to_rgb("img")
    assert out.tolist() == [[[1, 2, 3], [4, 5, 6]]] and out.dtype == np.uint8


def test_backend_constructs(mac):
    b = importlib.import_module("dofus_control.macos.backend")
    from dofus_control.backend import Backend
    assert isinstance(b.MacBackend(), Backend)
