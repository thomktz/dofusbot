"""Small local Zaap/Thrift service used by the AstraBot-style Dofus launch.

The official launcher is not a public registration endpoint. Dofus expects a
Zaap service to remain available for the whole session and calls connect,
settings, userInfo, and auth_getGameToken on it. This deliberately implements
only that narrow read-only launch contract; authentication material is supplied
by the caller through environment variables, never guessed or logged.
"""
from __future__ import annotations

import json
import logging
import socketserver
import threading
from typing import Any

from thrift.Thrift import TApplicationException, TMessageType, TType
from thrift.protocol import TBinaryProtocol
from thrift.server import TServer
from thrift.transport import TSocket, TTransport

log = logging.getLogger(__name__)


class _BoundServerSocket(TSocket.TServerSocket):
    """Bind before starting the thread, like AstraBot's launcher server."""

    def listen(self) -> None:
        if self.handle is None:
            super().listen()


class _Handler:
    def __init__(self, token: str | None = None, user_info: str | None = None, haapi_key: str | None = None):
        self.token = token
        self.user_info = user_info or "{}"
        self.haapi_key = haapi_key
        self.sessions: set[str] = set()

    def call(self, name: str, args: dict[int, Any]) -> tuple[int, Any]:
        if name == "connect":
            session = str(args.get(4, ""))
            if not session:
                raise ValueError("missing Zaap session hash")
            self.sessions.add(session)
            return TType.STRING, session
        if name == "settings_get":
            key = args.get(2)
            return TType.STRING, {"autoConnectType": '"2"', "language": '"fr"', "connectionPort": '"5555"'}.get(key, 'null')
        if name == "userInfo_get":
            self._check(args.get(1))
            return TType.STRING, self.user_info
        if name in ("auth_getGameToken", "auth_getGameTokenWithWindowId"):
            self._check(args.get(1))
            # AstraBot creates this short-lived token when the client asks,
            # rather than reusing the browser-login token.
            api_key = self.haapi_key or __import__("os").environ.get("DOFUS_HAAPI_KEY")
            if api_key:
                from .ankama_auth import create_game_token
                return TType.STRING, create_game_token(api_key)
            if not self.token:
                raise RuntimeError("DOFUS_GAME_TOKEN is not configured; authenticate through the launcher first")
            return TType.STRING, self.token
        if name in ("updater_isUpdateAvailable", "zaapMustUpdate_get"):
            return TType.BOOL, False
        if name in ("release_restartOnExit", "release_exitAndRepair", "settings_set"):
            return TType.VOID, None
        if name == "zaapVersion_get":
            return TType.STRING, '"3.15.15"'
        raise LookupError(name)

    def _check(self, session: Any) -> None:
        if str(session or "") not in self.sessions:
            raise ValueError("unknown Zaap session")


def _read_args(proto: Any) -> dict[int, Any]:
    out: dict[int, Any] = {}
    proto.readStructBegin()
    while True:
        _, typ, field = proto.readFieldBegin()
        if typ == TType.STOP:
            break
        if typ == TType.STRING:
            out[field] = proto.readString()
        elif typ == TType.I32:
            out[field] = proto.readI32()
        elif typ == TType.BOOL:
            out[field] = proto.readBool()
        else:
            proto.skip(typ)
        proto.readFieldEnd()
    proto.readStructEnd()
    return out


class _Processor:
    def __init__(self, handler: _Handler):
        self.handler = handler

    def process(self, iprot: Any, oprot: Any) -> bool:
        name, kind, seqid = iprot.readMessageBegin()
        args = _read_args(iprot)
        iprot.readMessageEnd()
        try:
            typ, value = self.handler.call(name, args)
            oprot.writeMessageBegin(name, TMessageType.REPLY, seqid)
            oprot.writeStructBegin(f"{name}_result")
            if typ != TType.VOID:
                oprot.writeFieldBegin("success", typ, 0)
                if typ == TType.STRING: oprot.writeString(value)
                elif typ == TType.BOOL: oprot.writeBool(value)
                oprot.writeFieldEnd()
            oprot.writeFieldStop(); oprot.writeStructEnd(); oprot.writeMessageEnd(); oprot.trans.flush()
        except Exception as exc:
            log.warning("Zaap %s failed: %s", name, exc)
            oprot.writeMessageBegin(name, TMessageType.EXCEPTION, seqid)
            TApplicationException(TApplicationException.INTERNAL_ERROR, str(exc)).write(oprot)
            oprot.writeMessageEnd(); oprot.trans.flush()
        return True


class ZaapEmulator:
    def __init__(self, port: int = 26116, token: str | None = None, user_info: str | None = None,
                 haapi_key: str | None = None):
        self.port = port
        self.token, self.user_info, self.haapi_key = token, user_info, haapi_key
        self._server: Any | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._server is not None:
            return
        # Per-instance credentials are required for one manager to host
        # several accounts; fall back only for the legacy single-account run.
        token = self.token or __import__("os").environ.get("DOFUS_GAME_TOKEN")
        info = self.user_info or __import__("os").environ.get("DOFUS_USER_INFO")
        handler = _Handler(token, info, self.haapi_key)
        transport = _BoundServerSocket(host="127.0.0.1", port=self.port)
        transport.listen()
        self._server = TServer.TThreadedServer(
            _Processor(handler), transport,
            TTransport.TBufferedTransportFactory(),
            TBinaryProtocol.TBinaryProtocolFactory(),
        )
        self._thread = threading.Thread(target=self._server.serve, name="zaap-emulator", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            # thrift's Python TServer has no public stop() method; closing the
            # listening transport makes serve() return on the next accept.
            with __import__("contextlib").suppress(Exception):
                self._server.serverTransport.close()
            self._server = None
