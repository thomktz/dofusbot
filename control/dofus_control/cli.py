"""Command line interface: ``python -m dofus_control <command> ...``.

Every command works with any app (TextEdit, Calculator, Chrome...), which is how
you test before Dofus is installed. Run ``python -m dofus_control -h``.
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time

from .errors import ControlError, KillSwitchTripped
from .types import Rect

DEMO_TEXT = "Hello from dofus_control! This text is typed with humanlike timing."


def _controller(args, **kw):
    from .controller import Controller

    return Controller(
        app_name=getattr(args, "app", None),
        title_contains=getattr(args, "title", None),
        title_bar_height=args.title_bar,
        keyboard_layout=args.layout,
        **kw,
    )


def _need_window(ctl):
    from .errors import WindowNotFound

    win = ctl.find_window()
    if win is None:
        raise WindowNotFound(ctl._not_found_msg())
    return win


def _bring_front(ctl, win, timeout: float = 2.0) -> bool:
    """Activate the target (the terminal is frontmost when you run the CLI)."""
    if ctl.b.frontmost_pid() == win.pid:
        return True
    handle = ctl._ax_handle(win.window_id, win.pid, win.frame)
    ctl.b.activate(win.pid, handle)
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if ctl.b.frontmost_pid() == win.pid:
            time.sleep(0.15)
            return True
        time.sleep(0.05)
    return False


def _print_win(win) -> None:
    print(f"window_id={win.window_id} pid={win.pid} app={win.app!r} title={win.title!r}")
    print(f"  frame   (screen pt) = {win.frame}")
    print(f"  content (screen pt) = {win.content}  title_bar={win.title_bar_height:g}")
    if win.aligned is not None:
        print(f"  aligned = {win.aligned}" + (f"  note: {win.note}" if win.note else ""))


# ------------------------------------------------------------------ commands
def cmd_perms(args) -> int:
    ctl = _controller(args, start_kill_sources=False)
    try:
        if args.request:
            ctl.request_permissions()
            print("Prompts requested. Grant them in System Settings -> Privacy & Security, then restart the terminal.")
        st = ctl.permissions()
        print(json.dumps(st.as_dict(), indent=2))
        return 0 if st.ok else 1
    finally:
        ctl.close()


def cmd_list(args) -> int:
    ctl = _controller(args, start_kill_sources=False)
    try:
        raws = ctl.b.list_windows(include_offscreen=args.all)
        for w in raws:
            if not args.all and w.layer != 0:
                continue
            if args.app and args.app.casefold() not in w.owner.casefold():
                continue
            print(f"{w.window_id:>7} pid={w.pid:<6} layer={w.layer:<3} {w.owner[:24]!r:<26} {str(w.bounds):<28} {w.title!r}")
        if raws and all(w.title is None for w in raws):
            print("(no titles: Screen Recording permission is missing)")
        return 0
    finally:
        ctl.close()


def cmd_find(args) -> int:
    ctl = _controller(args, start_kill_sources=False)
    try:
        win = ctl.find_window()
        if win is None:
            print(ctl._not_found_msg())
            return 1
        if args.json:
            print(json.dumps(win.as_dict(), indent=2))
        else:
            _print_win(win)
        return 0
    finally:
        ctl.close()


def cmd_align(args) -> int:
    ctl = _controller(args, start_kill_sources=False)
    try:
        win = ctl.align_window(Rect.parse(args.rect), content=not args.frame, strict=args.strict)
        _print_win(win)
        if ctl.last_align:
            print(f"  attempts={ctl.last_align.attempts}")
        return 0 if win.aligned else 2
    finally:
        ctl.close()


def cmd_capture(args) -> int:
    from .png import write_png

    ctl = _controller(args, start_kill_sources=False)
    try:
        t0 = time.perf_counter()
        fr = ctl.capture_display(args.display) if args.display is not None else ctl.capture()
        dt = (time.perf_counter() - t0) * 1000
        write_png(args.out, fr.image)
        print(f"wrote {args.out}: {fr.width}x{fr.height} px, scale={fr.scale:g}, {dt:.0f} ms")
        return 0
    finally:
        ctl.close()


def _run_actions(args, actions: list[dict]) -> int:
    ctl = _controller(args, require_frontmost=not args.no_frontmost)
    try:
        win = _need_window(ctl)
        if not args.no_activate and not _bring_front(ctl, win):
            print("warning: could not make the target frontmost")
        for a in actions:
            print("execute", json.dumps(a))
            ctl.execute(a)
        return 0
    except KillSwitchTripped as e:
        print(f"ABORTED: {e}")
        return 3
    finally:
        ctl.close()


def cmd_click(args) -> int:
    a = {"kind": "click", "x": args.x, "y": args.y, "button": "right" if args.right else "left",
         "double": args.double, "exact": args.exact}
    return _run_actions(args, [a])


def cmd_move(args) -> int:
    return _run_actions(args, [{"kind": "move", "x": args.x, "y": args.y, "exact": args.exact}])


def cmd_key(args) -> int:
    mods = [m for m in (args.mods or "").split(",") if m]
    return _run_actions(args, [{"kind": "key", "key": args.key, "modifiers": mods}])


def cmd_type(args) -> int:
    return _run_actions(args, [{"kind": "type", "text": args.text}])


def cmd_scroll(args) -> int:
    return _run_actions(args, [{"kind": "scroll", "x": args.x, "y": args.y, "dy": args.dy}])


def cmd_calibrate(args) -> int:
    ctl = _controller(args, start_kill_sources=False)
    try:
        rect = ctl.calibrate_slot(display=args.display)
        det = ctl.last_calibration
        print(json.dumps({"slot": rect.as_dict(), "marker_px": det.marker_px if det else None,
                          "warnings": det.warnings if det else []}, indent=2))
        return 0
    finally:
        ctl.close()


def _ensure_textedit_document() -> None:
    script = [
        "-e", 'tell application "TextEdit" to activate',
        "-e", 'tell application "TextEdit" to make new document',
    ]
    try:
        subprocess.run(["osascript", *script], check=False, timeout=15, capture_output=True)
    except Exception as e:
        print(f"(could not script TextEdit: {e})")


def cmd_demo(args) -> int:
    if args.app.casefold() == "textedit":
        _ensure_textedit_document()
        time.sleep(1.0)
    ctl = _controller(args)
    ctl.on_kill(lambda reason: print(f"\n*** KILL SWITCH: {reason} ***"))
    try:
        win = None
        for _ in range(50):
            win = ctl.find_window()
            if win is not None:
                break
            time.sleep(0.1)
        if win is None:
            print(ctl._not_found_msg())
            return 1
        print("Safe demo. Press ctrl+alt+cmd+K, move the mouse, or slam it into the top-left corner to abort.")
        hk = "event tap" if ctl.hotkey_via_event_tap else "key-state polling (grant Input Monitoring for the tap)"
        print(f"Hotkey listener: {hk}")
        for i in (3, 2, 1):
            print(f"  starting in {i}...")
            time.sleep(1)
            ctl.killswitch.check()
        target = Rect.parse(args.rect)
        win = ctl.align_window(target)
        _print_win(win)
        if args.keep_on_top:
            ctl.keep_on_top(True)
        _bring_front(ctl, win)
        c = win.content
        ctl.execute({"kind": "click", "x": c.w / 2, "y": c.h / 2, "reason": "focus the text area"})
        ctl.execute({"kind": "type", "text": args.text})
        ctl.execute({"kind": "key", "key": "enter"})
        ctl.execute({"kind": "type", "text": "Second line, then select all with cmd+a."})
        ctl.execute({"kind": "key", "key": "a", "modifiers": ["cmd"]})
        ctl.execute({"kind": "key", "key": "right"})
        print("demo finished")
        return 0
    except KillSwitchTripped as e:
        print(f"ABORTED: {e}")
        return 3
    finally:
        ctl.close()


def cmd_killswitch_test(args) -> int:
    from .controller import Controller

    ctl = Controller(app_name=None, require_frontmost=False)
    try:
        print(f"Kill switch armed for {args.seconds:g}s. Try ctrl+alt+cmd+K or the top-left screen corner.")
        print("hotkey via:", "event tap" if ctl.hotkey_via_event_tap else "key-state polling")
        print("permissions:", ctl.permissions().as_dict())
        if ctl.killswitch.wait(args.seconds):
            print(f"PASS: tripped ({ctl.kill_reason})")
            return 0
        print("no trigger within the time limit")
        return 1
    finally:
        ctl.close()


# ------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m dofus_control", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--title-bar", type=float, default=None, help="override title bar height (points)")
    p.add_argument("--layout", default="auto", choices=["auto", "us", "azerty"], help="keyboard layout for key actions (auto = active macOS layout)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def app_args(sp, required=True, default=None):
        sp.add_argument("--app", required=required, default=default, help="app (owner) name, case-insensitive substring")
        sp.add_argument("--title", default=None, help="window title must contain this")

    def input_args(sp):
        app_args(sp)
        sp.add_argument("--no-activate", action="store_true", help="don't bring the app to the front first")
        sp.add_argument("--no-frontmost", action="store_true", help="don't refuse when the app isn't frontmost")

    s = sub.add_parser("perms", help="show permission status")
    s.add_argument("--request", action="store_true", help="trigger the macOS prompts")
    s.set_defaults(fn=cmd_perms)

    s = sub.add_parser("list", help="list on-screen windows (front to back)")
    s.add_argument("--app", default=None)
    s.add_argument("--all", action="store_true", help="include off-screen and non-normal layers")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("find", help="find the target window")
    app_args(s)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_find)

    s = sub.add_parser("align", help="move/resize the window so its content matches a rect")
    app_args(s)
    s.add_argument("--rect", required=True, help="x,y,w,h in screen points")
    s.add_argument("--frame", action="store_true", help="rect is the whole frame, not the content area")
    s.add_argument("--strict", action="store_true", help="fail if the app refuses the exact size")
    s.set_defaults(fn=cmd_align)

    s = sub.add_parser("capture", help="capture the window content (or a display) to PNG")
    app_args(s, required=False)
    s.add_argument("--out", required=True)
    s.add_argument("--display", type=int, default=None, help="capture this display instead of a window")
    s.set_defaults(fn=cmd_capture)

    for name, fn in (("click", cmd_click), ("move", cmd_move)):
        s = sub.add_parser(name, help=f"{name} at window points")
        input_args(s)
        s.add_argument("--x", type=float, required=True)
        s.add_argument("--y", type=float, required=True)
        s.add_argument("--exact", action="store_true", help="no target jitter")
        if name == "click":
            s.add_argument("--right", action="store_true")
            s.add_argument("--double", action="store_true")
        s.set_defaults(fn=fn)

    s = sub.add_parser("key", help="press a key (protocol key names)")
    input_args(s)
    s.add_argument("--key", required=True)
    s.add_argument("--mods", default="", help="comma list: cmd,ctrl,alt,shift")
    s.set_defaults(fn=cmd_key)

    s = sub.add_parser("type", help="type text")
    input_args(s)
    s.add_argument("--text", required=True)
    s.set_defaults(fn=cmd_type)

    s = sub.add_parser("scroll", help="scroll at window points (dy>0 = down)")
    input_args(s)
    s.add_argument("--x", type=float, required=True)
    s.add_argument("--y", type=float, required=True)
    s.add_argument("--dy", type=int, required=True)
    s.set_defaults(fn=cmd_scroll)

    s = sub.add_parser("calibrate", help="locate the dashboard slot markers (protocol §3)")
    app_args(s, required=False, default="Dofus")
    s.add_argument("--display", type=int, default=None)
    s.set_defaults(fn=cmd_calibrate)

    s = sub.add_parser("demo", help="safe demo: align TextEdit and type humanlike")
    app_args(s, required=False, default="TextEdit")
    s.add_argument("--rect", default="200,150,900,600", help="content rect x,y,w,h (screen points)")
    s.add_argument("--text", default=DEMO_TEXT)
    s.add_argument("--keep-on-top", action="store_true")
    s.set_defaults(fn=cmd_demo)

    s = sub.add_parser("killswitch-test", help="arm the kill switch and wait for a trigger")
    s.add_argument("--seconds", type=float, default=30.0)
    s.set_defaults(fn=cmd_killswitch_test)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        return int(args.fn(args) or 0)
    except ControlError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
