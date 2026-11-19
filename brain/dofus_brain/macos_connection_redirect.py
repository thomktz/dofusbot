"""Spawn Dofus suspended and install the platform connection redirect hook.

On macOS this hook is blocked by Dofus's hardened runtime.  Windows does not
have that specific task-port restriction, so the same launch path can be used
inside the Windows ARM VM (where the client itself runs under Windows x64
emulation).
"""
from __future__ import annotations

import os
import getpass
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
import subprocess


@dataclass
class RedirectedProcess:
    pid: int
    _session: object
    _script: object

    def close(self) -> None:
        for item in (self._script, self._session):
            try:
                item.unload() if item is self._script else item.detach()
            except Exception:
                pass


@dataclass
class DirectProcess:
    """A normally launched Dofus process used by the Network Extension path.

    ``close`` intentionally does not terminate the game.  It matches the
    Frida wrapper's detach-only semantics and prevents a dashboard restart
    from killing a player-owned client.
    """

    pid: int
    _process: subprocess.Popen[bytes]

    def close(self) -> None:
        return None


def spawn_without_injection(command: Sequence[str], environment: dict[str, str]) -> DirectProcess:
    """Launch Dofus normally; networking is intercepted by macOS itself."""
    process = subprocess.Popen(list(command), env=environment)
    return DirectProcess(int(process.pid), process)


def spawn_with_redirect(command: Sequence[str], environment: dict[str, str], proxy_port: int) -> RedirectedProcess:
    """Run Dofus with only its initial port-5555 connect redirected locally."""
    try:
        import frida
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("Frida is required for managed socket mode; install brain[protocol]") from exc
    script_name = "windows_connection_redirect.js" if sys.platform == "win32" else "macos_connection_redirect.js"
    script_path = Path(__file__).with_name(script_name)
    device = frida.get_local_device()
    try:
        pid = device.spawn(list(command), envp=environment)
    except Exception as exc:
        raise RuntimeError(
            "Frida could not spawn Dofus as the desktop user "
            f"{getpass.getuser()!r}: {exc}"
        ) from exc
    try:
        session = device.attach(pid)
        script = session.create_script(script_path.read_text())
        script.load()
        script.post({"port": int(proxy_port)})
        device.resume(pid)
        return RedirectedProcess(int(pid), session, script)
    except Exception as exc:
        try:
            device.kill(pid)
        except Exception:
            pass
        # Frida on macOS needs Apple's task_for_pid authorization.  Elevating
        # the entire dashboard to root is *not* the remedy: Dofus still needs
        # to be spawned in the logged-in WindowServer session.  Give the
        # operator a precise, one-time remediation instead of a bare PID.
        if sys.platform == "darwin" and "unable to access process" in str(exc).lower():
            raise RuntimeError(
                "macOS denied Frida access to the newly launched Dofus process. "
                "Authorize Developer Tools task-port debugging once for this Mac "
                "(DevToolsSecurity), then relaunch with ./run.sh. "
                f"Original Frida error: {exc}"
            ) from exc
        raise
