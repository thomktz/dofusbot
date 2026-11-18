"""Small Astra-style transparent TCP bridge for an active Dofus game socket.

It deliberately handles only the game connection.  The connection/login proxy
will redirect the official selected-server endpoint to this listener; after
that this bridge can forward client/server frames and append validated frames
from :class:`ManagedTransport` to the server side.
"""
from __future__ import annotations

import socket
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from .managed_transport import ManagedTransport, ProtocolCodec, frame


def _payload(packet: bytes) -> bytes:
    """Return the protobuf payload from one varint-framed packet."""
    for index, byte in enumerate(packet):
        if not byte & 0x80:
            return packet[index + 1:]
    raise ValueError("truncated frame header")


def _take_frames(buffer: bytearray) -> list[bytes]:
    """Remove complete length-prefixed frames, retaining incomplete bytes."""
    out: list[bytes] = []
    while buffer:
        value = 0
        shift = 0
        header = 0
        for index, byte in enumerate(buffer):
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                header = index + 1
                break
            shift += 7
            if shift > 63:
                raise ValueError("invalid Dofus frame length")
        if not header or len(buffer) < header + value:
            break
        out.append(bytes(buffer[:header + value]))
        del buffer[:header + value]
    return out


@dataclass
class GameBridge:
    client: socket.socket
    server: socket.socket
    transport: ManagedTransport
    codec: ProtocolCodec
    on_close: Callable[[], None] | None = None
    on_frame: Callable[[bool, bytes], None] | None = None
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    _closed: bool = field(default=False, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def start(self) -> None:
        self.transport.attach(self.session_id, self.codec, self.send_to_server)
        threading.Thread(target=self._forward, args=(self.client, self.server), daemon=True).start()
        threading.Thread(target=self._forward, args=(self.server, self.client), daemon=True).start()

    def _forward(self, source: socket.socket, destination: socket.socket) -> None:
        buffer = bytearray()
        try:
            while not self._closed:
                data = source.recv(8192)
                if not data:
                    break
                buffer.extend(data)
                for frame in _take_frames(buffer):
                    if source is self.client:
                        read_uid = getattr(self.codec, "client_uid", None)
                        if callable(read_uid):
                            try:
                                self.transport.observe_uid(read_uid(_payload(frame)))
                            except Exception:
                                # An unfamiliar packet is still relayed
                                # transparently; UID observation is advisory.
                                pass
                    if self.on_frame:
                        self.on_frame(source is self.server, frame)
                    with self._lock:
                        destination.sendall(frame)
        except (OSError, ValueError):
            pass
        finally:
            self.close()

    def send_to_server(self, framed_payload: bytes) -> None:
        if self._closed:
            raise RuntimeError("managed game proxy session is closed")
        with self._lock:
            self.server.sendall(framed_payload)
        if self.on_frame:
            self.on_frame(False, framed_payload)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for sock in (self.client, self.server):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        self.transport.detach()
        if self.on_close:
            self.on_close()


class ManagedGameProxy:
    """One-shot listener created for the selected game-server endpoint."""

    def __init__(self, transport: ManagedTransport, codec: ProtocolCodec,
                 on_transport_change: Callable[[], None] | None = None,
                 on_bridge_ready: Callable[[GameBridge], None] | None = None,
                 on_frame: Callable[[bool, bytes], None] | None = None):
        self.transport, self.codec = transport, codec
        self.on_transport_change = on_transport_change
        self.on_bridge_ready = on_bridge_ready
        self.on_frame = on_frame
        self.listener: socket.socket | None = None
        self.bridge: GameBridge | None = None

    def start(self, target: tuple[str, int]) -> int:
        listener = socket.create_server(("127.0.0.1", 0), backlog=1)
        self.listener = listener
        port = int(listener.getsockname()[1])
        threading.Thread(target=self._accept_once, args=(listener, target), daemon=True).start()
        return port

    def _accept_once(self, listener: socket.socket, target: tuple[str, int]) -> None:
        try:
            client, _ = listener.accept()
            server = socket.create_connection(target, timeout=15)
            self.bridge = GameBridge(client, server, self.transport, self.codec,
                                     on_close=self.stop, on_frame=self.on_frame)
            self.bridge.start()
            if self.on_bridge_ready:
                self.on_bridge_ready(self.bridge)
            if self.on_transport_change:
                self.on_transport_change()
        except OSError as exc:
            self.transport.detach(f"game proxy connection failed: {exc}")
        finally:
            try:
                listener.close()
            except OSError:
                pass

    def stop(self) -> None:
        if self.listener:
            try:
                self.listener.close()
            except OSError:
                pass
            self.listener = None
        if self.bridge:
            bridge, self.bridge = self.bridge, None
            bridge.close()
        if self.on_transport_change:
            self.on_transport_change()


class ManagedConnectionProxy:
    """Astra-style login bridge which rewrites SelectServerResponse locally.

    Dofus first connects to the fixed connection server on port 5555.  Frida
    redirects that connection to this listener.  When the server chooses a
    game shard, the response is changed to a one-shot local game listener;
    that second listener is the bridge which owns the writable session.
    """

    def __init__(self, transport: ManagedTransport, codec: ProtocolCodec,
                 target: tuple[str, int] = ("dofus2-co-production.ankama-games.com", 5555),
                 on_transport_change: Callable[[], None] | None = None,
                 transport_for_account: Callable[[int], ManagedTransport] | None = None,
                 on_account_identified: Callable[[int, str, ManagedTransport], None] | None = None,
                 on_protocol_frame: Callable[[int, bool, bytes], None] | None = None):
        self.transport, self.codec, self.target = transport, codec, target
        self.on_transport_change = on_transport_change
        # AstraBot identifies the account from the login response, *before*
        # it rewrites that account's selected game-server endpoint.  These two
        # hooks give every account an isolated transport without coupling this
        # low-level bridge to dashboard/session state.
        self.transport_for_account = transport_for_account
        self.on_account_identified = on_account_identified
        self.on_protocol_frame = on_protocol_frame
        self.listener: socket.socket | None = None
        self.game_proxy: ManagedGameProxy | None = None  # legacy/current convenience only
        self._game_proxies: set[ManagedGameProxy] = set()
        self._clients: set[socket.socket] = set()
        self._lock = threading.Lock()
        self._closed = False

    def start(self, listen_port: int = 0) -> int:
        """Listen for the Dofus connection-server flow.

        ``listen_port=0`` preserves the Frida-era ephemeral listener.  A
        macOS ``NETransparentProxyProvider`` cannot be told a new port after
        a flow is claimed, so the supported no-injection path gives us one
        configured, loopback-only port instead.
        """
        self.listener = socket.create_server(("127.0.0.1", int(listen_port)), backlog=16)
        port = int(self.listener.getsockname()[1])
        threading.Thread(target=self._accept_forever, daemon=True).start()
        return port

    def _accept_forever(self) -> None:
        assert self.listener is not None
        while not self._closed:
            try:
                client, _ = self.listener.accept()
            except OSError:
                break
            with self._lock:
                self._clients.add(client)
            threading.Thread(target=self._serve_client, args=(client,), daemon=True).start()

    def _serve_client(self, client: socket.socket) -> None:
        try:
            server = socket.create_connection(self.target, timeout=15)
            # This mutable session state belongs to one login connection.
            session: dict[str, object] = {"account_id": None, "transport": self.transport, "game_proxy": None}
            threading.Thread(target=self._forward, args=(client, server, False, session), daemon=True).start()
            threading.Thread(target=self._forward, args=(server, client, True, session), daemon=True).start()
        except OSError as exc:
            self.transport.detach(f"connection proxy failed: {exc}")
            try:
                client.close()
            except OSError:
                pass
            with self._lock:
                self._clients.discard(client)

    @staticmethod
    def _login_message(packet: bytes):
        """Decode a connection-frame envelope, or return ``None`` unchanged.

        The connection protocol is separate from the obfuscated game protocol;
        importing it lazily keeps the server usable when Astra's optional
        generated descriptors are unavailable.
        """
        try:
            from pathlib import Path
            import importlib
            import sys
            proto = Path("/private/tmp/AstraBot/DBDofusUnity/datas/protos/non_obf/connection")
            if str(proto) not in sys.path:
                sys.path.insert(0, str(proto))
            login = importlib.import_module("login_message_pb2")
            message = login.LoginMessage()
            message.ParseFromString(_payload(packet))
            return message
        except Exception:
            return None

    @classmethod
    def account_id_from_packet(cls, packet: bytes) -> int | None:
        """Return the authoritative account id from an identification success."""
        message = cls._login_message(packet)
        try:
            identification = message.response.identification
            if message.response.HasField("identification") and identification.HasField("success"):
                return int(identification.success.account_id)
        except (AttributeError, ValueError, TypeError):
            pass
        return None

    def _rewrite_select_server(self, packet: bytes, session: dict[str, object]) -> bytes:
        """Swap the official shard endpoint for an ephemeral local bridge."""
        try:
            message = self._login_message(packet)
            if message is None:
                return packet
            if not (message.HasField("response") and message.response.HasField("selectServer")
                    and message.response.selectServer.HasField("success")):
                return packet
            success = message.response.selectServer.success
            if not success.ports:
                return packet
            endpoint = (str(success.host), int(success.ports[0]))
            transport = session["transport"]
            if not isinstance(transport, ManagedTransport):
                return packet
            account_id = session.get("account_id")

            def on_bridge_ready(bridge: GameBridge) -> None:
                if isinstance(account_id, int) and self.on_account_identified:
                    self.on_account_identified(account_id, bridge.session_id, transport)

            def on_frame(from_server: bool, raw_frame: bytes) -> None:
                if isinstance(account_id, int) and self.on_protocol_frame:
                    self.on_protocol_frame(account_id, from_server, raw_frame)

            game_proxy = ManagedGameProxy(transport, self.codec, self.on_transport_change,
                                          on_bridge_ready, on_frame)
            with self._lock:
                self._game_proxies.add(game_proxy)
                self.game_proxy = game_proxy
            session["game_proxy"] = game_proxy
            local_port = game_proxy.start(endpoint)
            success.host = "127.0.0.1"
            success.ports[0] = local_port
            return frame(message.SerializeToString())
        except Exception:
            # A connection frame that is not yet understood must remain fully
            # transparent; the passive observer still sees the live session.
            return packet

    def _forward(self, source: socket.socket, destination: socket.socket, from_server: bool,
                 session: dict[str, object]) -> None:
        buffer = bytearray()
        try:
            while not self._closed:
                data = source.recv(8192)
                if not data:
                    break
                buffer.extend(data)
                for packet in _take_frames(buffer):
                    if from_server:
                        account_id = self.account_id_from_packet(packet)
                        if account_id is not None:
                            session["account_id"] = account_id
                            if self.transport_for_account:
                                session["transport"] = self.transport_for_account(account_id)
                        packet = self._rewrite_select_server(packet, session)
                    destination.sendall(packet)
        except (OSError, ValueError):
            pass
        finally:
            game_proxy = session.get("game_proxy")
            if isinstance(game_proxy, ManagedGameProxy):
                game_proxy.stop()
                with self._lock:
                    self._game_proxies.discard(game_proxy)
            for sock in (source, destination):
                try:
                    sock.close()
                except OSError:
                    pass
            with self._lock:
                self._clients.discard(source)
                self._clients.discard(destination)

    def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.listener:
            try:
                self.listener.close()
            except OSError:
                pass
            self.listener = None
        with self._lock:
            clients, proxies = list(self._clients), list(self._game_proxies)
            self._clients.clear()
            self._game_proxies.clear()
        for client in clients:
            try:
                client.close()
            except OSError:
                pass
        for game_proxy in proxies:
            game_proxy.stop()
