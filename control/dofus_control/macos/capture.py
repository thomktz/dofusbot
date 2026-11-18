"""Screen capture: ScreenCaptureKit (macOS 14+) with CGWindowListCreateImage fallback.

* Window capture uses ``SCContentFilter(desktopIndependentWindow:)``, which renders
  the window by itself, so it works while the window is covered by other windows
  (not when minimised or on another Space... SCK can often still capture those, CG cannot).
* Display capture uses ``SCContentFilter(display:excludingWindows:)``.
* Every CGImage is redrawn into an sRGB RGBA bitmap so the numpy array has a known
  byte order and colours are comparable with CSS colours (marker calibration).

The SCK completion handlers run on an internal dispatch queue, so we simply block
on a threading.Event: no run loop is needed (and none must be required: this is
called from worker threads too). Never call these from inside an SCK handler.
"""
from __future__ import annotations

import logging
import platform
import threading
import time

import numpy as np
import Quartz

from ..errors import CaptureFailed, PermissionMissing
from ..geometry import parse_macos_version, rgba_buffer_to_rgb
from ..types import DisplayInfo, Rect

log = logging.getLogger(__name__)

try:  # optional: pyobjc-framework-ScreenCaptureKit
    import ScreenCaptureKit as SCK
except Exception:  # pragma: no cover - depends on the installed PyObjC
    SCK = None

MACOS_VERSION = parse_macos_version(platform.mac_ver()[0])
SCK_SCREENSHOT_AVAILABLE = (
    SCK is not None
    and MACOS_VERSION >= (14, 0, 0)
    and hasattr(SCK, "SCScreenshotManager")
)
TIMEOUT_S = 5.0
_SHAREABLE_TTL_S = 2.0
# ScreenCaptureKit can become temporarily unresponsive while its CoreGraphics fallback
# remains healthy.  Do not pay its full timeout on every frame in that state.
_SCK_WINDOW_BACKOFF_S = 30.0
_sck_window_retry_after = 0.0


# ------------------------------------------------------------------ CGImage -> numpy
def cgimage_to_rgb(image) -> np.ndarray:
    """Draw ``image`` into an sRGB RGBA8 bitmap we own and return HxWx3 uint8 RGB."""
    if image is None:
        raise CaptureFailed("capture returned no image")
    w = int(Quartz.CGImageGetWidth(image))
    h = int(Quartz.CGImageGetHeight(image))
    if w == 0 or h == 0:
        raise CaptureFailed("capture returned an empty image (permission missing or window not drawable?)")
    bpr = w * 4
    buf = bytearray(bpr * h)
    cs = Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB)
    ctx = Quartz.CGBitmapContextCreate(
        buf, w, h, 8, bpr, cs, Quartz.kCGImageAlphaPremultipliedLast  # memory: R,G,B,A
    )
    if ctx is None:
        raise CaptureFailed("CGBitmapContextCreate failed")
    Quartz.CGContextSetBlendMode(ctx, Quartz.kCGBlendModeCopy)
    Quartz.CGContextDrawImage(ctx, Quartz.CGRectMake(0, 0, w, h), image)
    Quartz.CGContextFlush(ctx)
    del ctx
    return rgba_buffer_to_rgb(bytes(buf), w, h, bpr, "RGBA")


# ------------------------------------------------------------------ displays
def displays() -> list[DisplayInfo]:
    """Active displays, main display first."""
    err, ids, count = Quartz.CGGetActiveDisplayList(16, None, None)
    if err != 0 or not ids:
        ids = [Quartz.CGMainDisplayID()]
    main = Quartz.CGMainDisplayID()
    ids = sorted(list(ids)[: int(count) if count else len(ids)], key=lambda d: 0 if d == main else 1)
    out = []
    for i, did in enumerate(ids):
        b = Quartz.CGDisplayBounds(did)
        bounds = Rect(float(b.origin.x), float(b.origin.y), float(b.size.width), float(b.size.height))
        scale = 1.0
        mode = Quartz.CGDisplayCopyDisplayMode(did)
        if mode is not None:
            pw = Quartz.CGDisplayModeGetPixelWidth(mode)
            ww = Quartz.CGDisplayModeGetWidth(mode)
            if ww:
                scale = float(pw) / float(ww)
        out.append(DisplayInfo(index=i, display_id=int(did), bounds=bounds, scale=scale, is_main=(did == main)))
    return out


# ------------------------------------------------------------------ SCK plumbing
def _wait(call, what: str):
    """Run ``call(handler)`` and block until the completion handler fires."""
    done = threading.Event()
    box: dict = {}

    def handler(result, error):
        box["result"] = result
        box["error"] = error
        done.set()

    call(handler)
    if not done.wait(TIMEOUT_S):
        raise CaptureFailed(f"ScreenCaptureKit {what} timed out after {TIMEOUT_S:.0f}s")
    if box.get("error") is not None:
        err = box["error"]
        msg = str(err.localizedDescription()) if hasattr(err, "localizedDescription") else str(err)
        code = err.code() if hasattr(err, "code") else None
        # SCStreamErrorUserDeclined = -3801
        if code == -3801 or "declin" in msg.lower() or "permission" in msg.lower():
            raise PermissionMissing("screen_recording", msg)
        raise CaptureFailed(f"ScreenCaptureKit {what} failed: {msg} (code {code})")
    return box.get("result")


_shareable_lock = threading.Lock()
_shareable: tuple[float, object] | None = None


def shareable_content(fresh: bool = False):
    global _shareable
    with _shareable_lock:
        if not fresh and _shareable and time.monotonic() - _shareable[0] < _SHAREABLE_TTL_S:
            return _shareable[1]
        content = _wait(
            lambda h: SCK.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
                True, False, h
            ),
            "getShareableContent",
        )
        if content is None:
            raise CaptureFailed("SCShareableContent returned nothing")
        _shareable = (time.monotonic(), content)
        return content


def _sc_window(window_id: int):
    for fresh in (False, True):
        for w in shareable_content(fresh).windows():
            if int(w.windowID()) == int(window_id):
                return w
    raise CaptureFailed(f"window {window_id} not in ScreenCaptureKit's shareable content")


def _sc_display(display_id: int):
    for fresh in (False, True):
        for d in shareable_content(fresh).displays():
            if int(d.displayID()) == int(display_id):
                return d
    raise CaptureFailed(f"display {display_id} not in ScreenCaptureKit's shareable content")


def _screenshot(filt, width_px: int, height_px: int):
    cfg = SCK.SCStreamConfiguration.alloc().init()
    cfg.setWidth_(int(width_px))
    cfg.setHeight_(int(height_px))
    cfg.setShowsCursor_(False)
    if hasattr(cfg, "setColorSpaceName_"):
        # without this SCK returns display-native (P3) pixels tagged as untagged DeviceRGB,
        # so CSS colours like the slot markers come back shifted (#FF00FF -> 234,51,247)
        cfg.setColorSpaceName_(Quartz.kCGColorSpaceSRGB)
    if hasattr(cfg, "setIgnoreShadowsSingleWindow_"):
        cfg.setIgnoreShadowsSingleWindow_(True)
    if hasattr(cfg, "setCaptureResolution_") and hasattr(SCK, "SCCaptureResolutionBest"):
        cfg.setCaptureResolution_(SCK.SCCaptureResolutionBest)
    if hasattr(cfg, "setScalesToFit_"):
        cfg.setScalesToFit_(False)
    return _wait(
        lambda h: SCK.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(filt, cfg, h),
        "captureImage",
    )


def _filter_size_px(filt, fallback_points: Rect, fallback_scale: float) -> tuple[int, int]:
    scale = fallback_scale
    w_pt, h_pt = fallback_points.w, fallback_points.h
    try:
        if hasattr(filt, "pointPixelScale"):
            scale = float(filt.pointPixelScale()) or scale
        if hasattr(filt, "contentRect"):
            r = filt.contentRect()
            if r.size.width > 0 and r.size.height > 0:
                w_pt, h_pt = float(r.size.width), float(r.size.height)
    except Exception:
        log.debug("filter geometry unavailable", exc_info=True)
    return max(1, int(round(w_pt * scale))), max(1, int(round(h_pt * scale)))


# ------------------------------------------------------------------ public
def _check_permission() -> None:
    try:
        if not Quartz.CGPreflightScreenCaptureAccess():
            raise PermissionMissing("screen_recording")
    except AttributeError:  # pragma: no cover - macOS < 10.15
        pass


def capture_window(window_id: int, frame: Rect, display_scale: float = 2.0) -> np.ndarray:
    """RGB image of the whole window (title bar included), capture pixels."""
    global _sck_window_retry_after
    _check_permission()
    if SCK_SCREENSHOT_AVAILABLE and time.monotonic() >= _sck_window_retry_after:
        try:
            filt = SCK.SCContentFilter.alloc().initWithDesktopIndependentWindow_(_sc_window(window_id))
            w_px, h_px = _filter_size_px(filt, frame, display_scale)
            image = cgimage_to_rgb(_screenshot(filt, w_px, h_px))
            _sck_window_retry_after = 0.0
            return image
        except PermissionMissing:
            raise
        except Exception as e:
            _sck_window_retry_after = time.monotonic() + _SCK_WINDOW_BACKOFF_S
            log.warning("ScreenCaptureKit window capture failed (%s); using CGWindowListCreateImage for %.0fs", e,
                        _SCK_WINDOW_BACKOFF_S)
    return _cg_capture_window(window_id)


def _cg_capture_window(window_id: int) -> np.ndarray:
    fn = getattr(Quartz, "CGWindowListCreateImage", None)
    if fn is None:
        raise CaptureFailed("CGWindowListCreateImage unavailable and ScreenCaptureKit failed")
    img = fn(
        Quartz.CGRectNull,
        Quartz.kCGWindowListOptionIncludingWindow,
        int(window_id),
        Quartz.kCGWindowImageBoundsIgnoreFraming | Quartz.kCGWindowImageBestResolution,
    )
    return cgimage_to_rgb(img)


def capture_display(info: DisplayInfo, exclude_window_ids: tuple[int, ...] = ()) -> np.ndarray:
    _check_permission()
    if SCK_SCREENSHOT_AVAILABLE:
        try:
            disp = _sc_display(info.display_id)
            excluded = []
            if exclude_window_ids:
                ids = {int(i) for i in exclude_window_ids}
                excluded = [w for w in shareable_content().windows() if int(w.windowID()) in ids]
            filt = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(disp, excluded)
            w_px, h_px = _filter_size_px(filt, info.bounds, info.scale)
            return cgimage_to_rgb(_screenshot(filt, w_px, h_px))
        except PermissionMissing:
            raise
        except Exception as e:
            log.warning("ScreenCaptureKit display capture failed (%s); trying CGDisplayCreateImage", e)
    fn = getattr(Quartz, "CGDisplayCreateImage", None)
    if fn is None:
        raise CaptureFailed("CGDisplayCreateImage unavailable and ScreenCaptureKit failed")
    return cgimage_to_rgb(fn(info.display_id))
