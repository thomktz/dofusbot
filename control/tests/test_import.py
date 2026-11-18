import importlib
import pathlib
import py_compile
import sys

import pytest

import dofus_control
from dofus_control import Controller, PlatformNotSupported

PKG = pathlib.Path(dofus_control.__file__).parent


def test_public_api():
    for name in ("Controller", "Rect", "WindowInfo", "Frame", "PermissionStatus", "KillSwitchTripped",
                 "PermissionMissing", "WindowNotFound", "AlignmentFailed", "DEFAULT_MARKERS"):
        assert hasattr(dofus_control, name)


def test_no_pyobjc_imported():
    assert "Quartz" not in sys.modules and "AppKit" not in sys.modules
    assert "dofus_control.macos.backend" not in sys.modules


def test_all_files_compile():
    root = PKG.parent
    files = list(PKG.rglob("*.py")) + list((root / "scripts").glob("*.py")) + list((root / "tests").glob("*.py"))
    assert len(files) > 15
    for f in files:
        py_compile.compile(str(f), doraise=True)


@pytest.mark.skipif(sys.platform == "darwin", reason="non-mac behaviour")
def test_controller_needs_macos():
    with pytest.raises(PlatformNotSupported):
        Controller("TextEdit")


def test_cli_parser():
    from dofus_control.cli import build_parser

    p = build_parser()
    a = p.parse_args(["align", "--app", "TextEdit", "--rect", "1,2,3,4"])
    assert a.cmd == "align" and a.rect == "1,2,3,4"
    a = p.parse_args(["--layout", "azerty", "key", "--app", "X", "--key", "a", "--mods", "cmd,shift"])
    assert a.layout == "azerty" and a.mods == "cmd,shift"
    a = p.parse_args(["demo"])
    assert a.app == "TextEdit"
    for cmd in (["perms"], ["list"], ["find", "--app", "x"], ["capture", "--out", "f.png"],
                ["click", "--app", "x", "--x", "1", "--y", "2"], ["type", "--app", "x", "--text", "hi"],
                ["calibrate"], ["killswitch-test"]):
        assert p.parse_args(cmd).fn


@pytest.mark.skipif(sys.platform == "darwin", reason="non-mac behaviour")
def test_cli_reports_platform_error(capsys):
    from dofus_control.cli import main

    assert main(["perms"]) == 1
    assert "macOS" in capsys.readouterr().err
