import sys
from pathlib import Path

from dofus_brain.managed_proxy import ManagedConnectionProxy
from dofus_brain.managed_transport import ManagedTransport, frame
from dofus_brain.network_scan import _pcap_streams, parse_capture, parse_streams
from dofus_brain.world.account_manager import AccountManager


def _identification_success(account_id: int) -> bytes:
    sys.path.insert(0, "/private/tmp/AstraBot/DBDofusUnity/datas/protos/non_obf/connection")
    import login_message_pb2 as login

    message = login.LoginMessage()
    message.response.identification.success.account_id = account_id
    return frame(message.SerializeToString())


def test_proxy_extracts_account_from_authoritative_login_response():
    assert ManagedConnectionProxy.account_id_from_packet(_identification_success(123456)) == 123456
    assert ManagedConnectionProxy.account_id_from_packet(frame(b"not a login message")) is None


def test_identified_account_gets_a_distinct_reserved_transport():
    manager = AccountManager()
    first = manager.transport_for_identified_account(11)
    second = manager.transport_for_identified_account(22)
    assert isinstance(first, ManagedTransport)
    assert first is manager.transport(11)
    assert second is manager.transport(22)
    assert first is not second


def test_bridge_stream_decoder_matches_the_passive_pcap_decoder():
    capture = Path("/Users/thomaskientz/dofus-agent/captures/network/session-20261116-231804.pcap")
    if not capture.exists():
        return
    server, client, packets = _pcap_streams(capture)
    from_bridge = parse_streams(server, client, packets)
    from_capture = parse_capture(capture)
    assert from_bridge.map_id == from_capture.map_id
    assert [(group.entity_id, group.cell) for group in from_bridge.groups] == [
        (group.entity_id, group.cell) for group in from_capture.groups
    ]
