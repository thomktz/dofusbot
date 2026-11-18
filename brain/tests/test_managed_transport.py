import pytest

from dofus_brain.astra_codec import AstraCodec, AstraRuntimeUnavailable
from dofus_brain.managed_transport import GameIntent, ManagedTransport, TransportState, encode_varint, frame
from dofus_brain.managed_proxy import _take_frames


class Codec:
    def __init__(self):
        self.calls = []

    def encode(self, intent: GameIntent, uid: int) -> bytes:
        self.calls.append((intent, uid))
        return b"encoded"


def test_varint_and_framing_match_astra_length_prefix():
    assert encode_varint(300) == b"\xac\x02"
    assert frame(b"abc") == b"\x03abc"


def test_transport_requires_a_live_proxy_session():
    transport = ManagedTransport()
    with pytest.raises(RuntimeError, match="not connected"):
        transport.reply(42)


def test_transport_routes_typed_dialogue_action_through_attached_codec():
    codec, outbound = Codec(), []
    transport = ManagedTransport()
    transport.attach("game-1", codec, outbound.append, uid=9)
    transport.reply(24901)
    assert transport.status()["state"] == TransportState.READY
    assert codec.calls == [(GameIntent("npc_dialog_reply", {"reply_id": 24901}), 10)]
    assert outbound == [b"\x07encoded"]
    assert transport.uid == 10


def test_transport_routes_dialog_leave_through_attached_codec():
    codec, sent = Codec(), []
    transport = ManagedTransport()
    transport.attach("session", codec, sent.append, uid=9)
    transport.leave_dialog()
    assert codec.calls == [(GameIntent("dialog_leave", {}), 10)]
    assert sent == [b"\x07encoded"]
    assert transport.uid == 10


def test_transport_does_not_advance_client_uid_for_astra_gameplay_requests():
    class AstraStyleCodec(Codec):
        def request_uid(self, _intent: GameIntent, _next_uid: int) -> int:
            return -1

    codec, sent = AstraStyleCodec(), []
    transport = ManagedTransport()
    transport.attach("session", codec, sent.append, uid=9)
    transport.attack_monster(-42)
    assert codec.calls == [(GameIntent("attack_monster", {"monster_group_id": -42}), -1)]
    assert transport.uid == 9


def test_transport_routes_protocol_map_change_after_a_confirmed_move():
    class AstraStyleCodec(Codec):
        def request_uid(self, _intent: GameIntent, _next_uid: int) -> int:
            return -1

    codec, sent = AstraStyleCodec(), []
    transport = ManagedTransport()
    transport.attach("session", codec, sent.append)
    transport.change_map(123456)
    assert codec.calls == [(GameIntent("map_change", {"map_id": 123456, "auto_pilot": False}), -1)]
    assert sent == [b"\x07encoded"]


def test_transport_routes_native_go_to_with_auto_pilot_enabled():
    class AstraStyleCodec(Codec):
        def request_uid(self, _intent: GameIntent, _next_uid: int) -> int:
            return -1

    codec, sent = AstraStyleCodec(), []
    transport = ManagedTransport()
    transport.attach("session", codec, sent.append)
    transport.go_to_map(123456)
    assert codec.calls == [(GameIntent("map_change", {"map_id": 123456, "auto_pilot": True}), -1)]
    assert sent == [b"\x07encoded"]


def test_transport_exposes_verified_invitation_declines():
    class AstraStyleCodec(Codec):
        def request_uid(self, _intent: GameIntent, _next_uid: int) -> int: return -1
    codec, sent = AstraStyleCodec(), []
    transport = ManagedTransport(); transport.attach("session", codec, sent.append)
    transport.refuse_party_invitation(9)
    transport.refuse_friendly_fight(12)
    assert codec.calls == [
        (GameIntent("party_invitation_refuse", {"party_id": 9}), -1),
        (GameIntent("friendly_fight_answer", {"fight_id": 12, "accept": False}), -1),
    ]


def test_astra_codec_reports_a_missing_local_runtime_without_import_side_effects(tmp_path):
    codec = AstraCodec(tmp_path / "no-astra")
    with pytest.raises(AstraRuntimeUnavailable, match="not found"):
        codec.encode(GameIntent("npc_dialog_reply", {"reply_id": 1}), 2)


def test_proxy_frame_splitter_retains_partial_and_returns_complete_frames():
    data = bytearray(frame(b"one") + frame(b"two")[:2])
    assert _take_frames(data) == [frame(b"one")]
    assert data == bytearray(frame(b"two")[:2])
    data.extend(frame(b"two")[2:])
    assert _take_frames(data) == [frame(b"two")]
    assert data == bytearray()
