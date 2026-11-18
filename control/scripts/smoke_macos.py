#!/usr/bin/env python3
"""End-to-end smoke test of dofus_control on a real Mac, using TextEdit.

    python control/scripts/smoke_macos.py              # automatic steps
    python control/scripts/smoke_macos.py --interactive  # + hotkey / mouse-yank tests

Prints PASS / FAIL / SKIP per step and a summary. It moves windows, clicks and
types into a NEW TextEdit document. Don't touch the mouse while it runs
(that trips the kill switch on purpose). Abort any time: ctrl+alt+cmd+K or
slam the mouse into the top-left screen corner.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

RESULTS: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str = "") -> None:
    RESULTS.append((name, status, detail))
    print(f"[{status:4}] {name}" + (f" - {detail}" if detail else ""), flush=True)


def step(name: str):
    """Decorator: run fn(), PASS if it returns truthy/None-with-detail, FAIL on exception."""
    def deco(fn):
        try:
            out = fn()
            if out is None or out is True:
                record(name, "PASS")
            elif isinstance(out, tuple):
                record(name, out[0], out[1])
            elif isinstance(out, str):
                record(name, "PASS", out)
            else:
                record(name, "FAIL", repr(out))
        except SkipStep as e:
            record(name, "SKIP", str(e))
        except Exception as e:
            record(name, "FAIL", f"{type(e).__name__}: {e}")
            if os.environ.get("SMOKE_TRACE"):
                traceback.print_exc()
        return fn
    return deco


class SkipStep(Exception):
    pass


def osa(*lines: str) -> str:
    args = ["osascript"]
    for ln in lines:
        args += ["-e", ln]
    r = subprocess.run(args, capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        raise RuntimeError(f"osascript: {r.stderr.strip()}")
    return r.stdout.strip()


def textedit_text() -> str:
    return osa('tell application "TextEdit" to get text of front document')


def wait_for(pred, timeout=3.0, interval=0.05):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(interval)
    return pred()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interactive", action="store_true", help="also test hotkey and user-mouse detection")
    ap.add_argument("--no-markers", action="store_true", help="skip the marker-window calibration test")
    ap.add_argument("--keep", action="store_true", help="leave the test document open")
    args = ap.parse_args()

    if sys.platform != "darwin":
        print("This script must run on macOS.")
        return 2

    import platform

    print(f"macOS {platform.mac_ver()[0]}, Python {sys.version.split()[0]}, machine {platform.machine()}")

    state: dict = {}

    @step("import dofus_control + macOS backend")
    def _():
        import dofus_control  # noqa: F401
        from dofus_control.macos import backend, capture  # noqa: F401
        state["sck"] = capture.SCK_SCREENSHOT_AVAILABLE
        return f"ScreenCaptureKit screenshots: {capture.SCK_SCREENSHOT_AVAILABLE}"

    from dofus_control import Controller, HumanProfile, KillSwitchTripped, Rect
    from dofus_control.errors import WindowCovered, WindowNotFrontmost

    ctl = Controller("TextEdit", profile=HumanProfile(seed=7))
    state["ctl"] = ctl

    @step("permissions")
    def _():
        st = ctl.permissions()
        d = st.as_dict()
        if not st.ok:
            ctl.request_permissions()
            return ("FAIL", f"{d} -> prompts requested; grant them, restart the terminal, rerun")
        return f"{d}"

    @step("kill hotkey event tap")
    def _():
        if ctl.hotkey_via_event_tap:
            return "listen-only CGEventTap running"
        return ("WARN", "tap unavailable (grant Input Monitoring); using key-state polling fallback")

    @step("list windows")
    def _():
        ws = ctl.list_windows()
        named = sum(1 for w in ws if w.title)
        return f"{len(ws)} windows, {named} with titles"

    @step("open a new TextEdit document")
    def _():
        osa('tell application "TextEdit" to activate', 'tell application "TextEdit" to make new document')
        w = wait_for(ctl.find_window, 5.0)
        if not w:
            raise RuntimeError("TextEdit window not found")
        state["win"] = w
        return f"window {w.window_id} title={w.title!r} frame={w.frame} title_bar={w.title_bar_height:g}"

    target = Rect(220, 160, 800, 500)

    @step("align_window (content rect)")
    def _():
        w = ctl.align_window(target)
        state["win"] = w
        if not w.content.approx_equal(target, 1.0):
            return ("FAIL", f"content {w.content} != {target}")
        return f"content={w.content} attempts={ctl.last_align.attempts}"

    @step("align_window move + resize + back")
    def _():
        t2 = Rect(300, 220, 700, 420)
        w2 = ctl.align_window(t2)
        w3 = ctl.align_window(target)
        ok = w2.content.approx_equal(t2, 1) and w3.content.approx_equal(target, 1)
        return True if ok else ("FAIL", f"{w2.content} / {w3.content}")

    @step("title bar height sanity (capture top row is not title bar)")
    def _():
        w = state["win"]
        return f"title_bar={w.title_bar_height:g} pt (expected 28 for TextEdit on Big Sur+)"

    @step("capture window (frontmost)")
    def _():
        ctl.b.activate(state["win"].pid)
        time.sleep(0.4)
        t0 = time.perf_counter()
        fr = ctl.capture()
        dt = (time.perf_counter() - t0) * 1000
        state["frame1"] = fr
        exp_w = round(target.w * fr.scale)
        exp_h = round(target.h * fr.scale)
        from dofus_control.png import write_png

        path = os.path.join(tempfile.gettempdir(), "dofus_control_smoke_window.png")
        write_png(path, fr.image)
        if abs(fr.width - exp_w) > 2 or abs(fr.height - exp_h) > 2:
            return ("FAIL", f"{fr.width}x{fr.height} px, expected {exp_w}x{exp_h} (scale {fr.scale:g})")
        if fr.image.std() < 1:
            return ("FAIL", "image is uniform (blank capture? permission?)")
        return f"{fr.width}x{fr.height} px scale={fr.scale:g} in {dt:.0f} ms -> {path}"

    @step("capture window while covered by Finder")
    def _():
        osa('tell application "Finder" to activate', 'tell application "Finder" to make new Finder window')
        time.sleep(0.6)
        finder = Controller("Finder", start_kill_sources=False)
        try:
            fw = wait_for(finder.find_window, 3.0)
            if fw is None:
                raise SkipStep("no Finder window")
            finder.align_window(Rect(target.x - 20, target.y - 20, target.w + 40, target.h + 40))
            time.sleep(0.4)
            state["finder_pid"] = fw.pid
            cov = ctl.find_window()
            from dofus_control.geometry import covering_windows

            covered = covering_windows(ctl.list_windows(), cov.window_id, cov.content)
            fr = ctl.capture()
            a = state["frame1"].image.astype(int)
            b = fr.image.astype(int)
            if a.shape != b.shape:
                return ("FAIL", f"shape changed {a.shape} -> {b.shape}")
            diff = abs(a - b).mean()
            msg = f"covered_by={[w.owner for w in covered]} mean diff vs uncovered={diff:.1f}"
            return ("PASS", msg) if diff < 12 else ("FAIL", msg + " (captured the covering window?)")
        finally:
            finder.close()

    @step("keep_on_top tick re-activates TextEdit")
    def _():
        raised = ctl._kot_tick()
        ok = wait_for(lambda: ctl.b.frontmost_pid() == state["win"].pid, 2.0)
        return ("PASS", f"raised={raised}") if ok else ("FAIL", f"raised={raised}, frontmost={ctl.b.frontmost_pid()}")

    @step("close the Finder window")
    def _():
        osa('tell application "Finder" to close front window')
        ctl.b.activate(state["win"].pid)
        time.sleep(0.4)

    @step("capture_display(0)")
    def _():
        fr = ctl.capture_display(0)
        b = fr.display_bounds
        return f"{fr.width}x{fr.height} px, display {b}, scale={fr.scale:g}"

    @step("click + type into TextEdit, read back via AppleScript")
    def _():
        w = ctl.find_window()
        c = w.content
        ctl.execute({"kind": "click", "x": c.w / 2, "y": c.h / 2})
        ctl.execute({"kind": "type", "text": "dofus_control smoke 123 éà"})
        got = wait_for(lambda: "smoke 123" in textedit_text(), 2.0)
        txt = textedit_text()
        return ("PASS", repr(txt)) if got and "éà" in txt else ("FAIL", repr(txt))

    @step("key: cmd+a then type replaces the text; enter makes a newline")
    def _():
        ctl.execute({"kind": "key", "key": "a", "modifiers": ["cmd"]})
        ctl.execute({"kind": "type", "text": "replaced"})
        ctl.execute({"kind": "key", "key": "enter"})
        ctl.execute({"kind": "type", "text": "line2"})
        # escape dismisses the inline predictive-text candidate, which otherwise can block
        # TextEdit's AppleScript; compare case-insensitively (macOS auto-capitalisation)
        ctl.execute({"kind": "key", "key": "escape"})
        txt = wait_for(lambda: textedit_text() if "line2" in textedit_text().lower() else "", 2.0)
        return ("PASS", repr(txt)) if txt.strip().lower() == "replaced\nline2" else ("FAIL", repr(txt))

    @step("scroll and drag execute without error")
    def _():
        c = ctl.find_window().content
        ctl.execute({"kind": "scroll", "x": c.w / 2, "y": c.h / 2, "dy": 2})
        ctl.execute({"kind": "drag", "x": 20, "y": 20, "to_x": 200, "to_y": 25})
        ctl.execute({"kind": "move", "x": c.w / 2, "y": c.h / 2})

    @step("refuses input when TextEdit is not frontmost")
    def _():
        osa('tell application "Finder" to activate')
        time.sleep(0.5)
        try:
            ctl.execute({"kind": "click", "x": 50, "y": 50})
        except (WindowNotFrontmost, WindowCovered) as e:
            return f"raised {type(e).__name__}"
        finally:
            ctl.b.activate(state["win"].pid)
            time.sleep(0.4)
        return ("FAIL", "click was sent to a non-frontmost app")

    @step("kill() blocks execute; reset_kill() re-enables")
    def _():
        hits = []
        ctl.on_kill(hits.append)
        ctl.kill("smoke test")
        try:
            ctl.execute({"kind": "key", "key": "escape"})
            return ("FAIL", "execute ran while killed")
        except KillSwitchTripped:
            pass
        ctl.reset_kill()
        ctl.execute({"kind": "wait", "ms": 10})
        return True if hits == ["smoke test"] else ("FAIL", f"callbacks={hits}")

    if not args.no_markers:
        @step("calibrate_slot against a self-drawn marker window")
        def _():
            import AppKit

            app = AppKit.NSApplication.sharedApplication()
            app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
            slot = Rect(260, 200, 640, 400)
            screen_h = AppKit.NSScreen.screens()[0].frame().size.height
            win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                AppKit.NSMakeRect(slot.x, screen_h - slot.y - slot.h, slot.w, slot.h),
                AppKit.NSWindowStyleMaskBorderless, AppKit.NSBackingStoreBuffered, False,
            )
            win.setLevel_(AppKit.NSFloatingWindowLevel)
            win.setBackgroundColor_(AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.15, 0.15, 0.18, 1.0))
            cv = win.contentView()
            s = 12
            spec = [  # (x, y) in the content view's bottom-left coordinates
                (0, slot.h - s, (1, 0, 1)),            # TL magenta
                (slot.w - s, slot.h - s, (0, 1, 1)),   # TR cyan
                (0, 0, (1, 1, 0)),                     # BL yellow
                (slot.w - s, 0, (0, 1, 0)),            # BR green
            ]
            for x, y, (r, g, b) in spec:
                v = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(x, y, s, s))
                v.setWantsLayer_(True)
                v.layer().setBackgroundColor_(AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0).CGColor())
                cv.addSubview_(v)
            win.orderFrontRegardless()
            AppKit.NSRunLoop.currentRunLoop().runUntilDate_(AppKit.NSDate.dateWithTimeIntervalSinceNow_(0.8))
            try:
                rect = ctl.calibrate_slot()
            finally:
                win.orderOut_(None)
                AppKit.NSRunLoop.currentRunLoop().runUntilDate_(AppKit.NSDate.dateWithTimeIntervalSinceNow_(0.1))
            det = ctl.last_calibration
            ok = rect.approx_equal(slot, 1.0)
            msg = f"found {rect}, drew {slot}, marker {det.marker_px:g}px, warnings={det.warnings}"
            back = ctl.find_window() is not None
            msg += f", TextEdit restored={back}"
            return ("PASS", msg) if ok and back else ("FAIL", msg)

    if args.interactive:
        @step("hotkey ctrl+alt+cmd+K (press it within 10 s)")
        def _():
            ctl.reset_kill()
            print("    -> press ctrl+alt+cmd+K now", flush=True)
            if ctl.killswitch.wait(10):
                r = ctl.kill_reason
                ctl.reset_kill()
                return ("PASS", r) if "hotkey" in (r or "") else ("FAIL", r)
            return ("FAIL", "no trip within 10 s")

        @step("user mouse movement trips the kill switch (move the mouse when told)")
        def _():
            ctl.reset_kill()
            slow = HumanProfile(seed=1, move_ms=(4000, 4000), jitter_pt=0)
            ctl.profile = slow
            ctl._driver.p = slow
            c = ctl.find_window().content
            print("    -> in 1 s a slow 4 s mouse move starts; MOVE YOUR MOUSE during it", flush=True)
            time.sleep(1)
            try:
                ctl.execute({"kind": "move", "x": 5, "y": 5})
                ctl.execute({"kind": "move", "x": c.w - 5, "y": c.h - 5})
            except KillSwitchTripped as e:
                ctl.reset_kill()
                return ("PASS", e.reason)
            return ("FAIL", "not tripped (did you move the mouse?)")

        @step("top-left corner trips the kill switch (slam the mouse there within 10 s)")
        def _():
            ctl.reset_kill()
            print("    -> move the mouse to the top-left screen corner", flush=True)
            if ctl.killswitch.wait(10):
                r = ctl.kill_reason
                ctl.reset_kill()
                return ("PASS", r) if "corner" in (r or "") else ("FAIL", r)
            return ("FAIL", "no trip within 10 s")

    if not args.keep:
        try:
            osa('tell application "TextEdit" to close front document saving no')
        except Exception:
            pass
    ctl.close()

    fails = [r for r in RESULTS if r[1] == "FAIL"]
    print("\n==== summary ====")
    for name, status, _ in RESULTS:
        print(f"{status:4} {name}")
    print(f"{len(RESULTS) - len(fails)}/{len(RESULTS)} not failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
