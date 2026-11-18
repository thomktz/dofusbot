"""End-to-end: real uvicorn server in --mock (MockController + MockLLM), real websocket client."""
import asyncio
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
import websockets

from dofus_brain.config import BrainConfig
from dofus_brain.mock_control import MockController
from dofus_brain.planner import PlannedStep, coords_changed
from dofus_brain.server import create_app


class EdgeClickPlanner:
    """Trivial test planner: always wants to click the right edge of the map."""

    name = "test-edge"

    def __init__(self):
        self.goal = None

    def set_goal(self, g):
        self.goal = g

    async def propose(self, state, ctx):
        if state.screen != "world" or state.map.coords is None:
            return None
        cell = ctx.grid.edge_cells("right")[9]
        x, y = ctx.grid.center(cell)
        return PlannedStep({"kind": "click", "x": x, "y": y, "cell": cell, "reason": "test"}, task="edge",
                           verify=coords_changed())

    def on_result(self, r, s):
        pass


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def server(config_dir):
    ctl = MockController()
    cfg = BrainConfig.load(config_dir)
    app = create_app(mock=True, controller=ctl, planner=EdgeClickPlanner(), config=cfg,
                     agent_overrides={"loop_hz": 20, "status_interval_s": 0.5, "action_timeout_s": 2})
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield app, ctl, port
    srv.should_exit = True
    th.join(5)


class WS:
    def __init__(self, ws):
        self.ws = ws
        self.msgs: list[dict] = []
        self.n = 0

    async def send(self, type_, payload=None, id_=None):
        self.msgs.clear()  # only messages that arrive after this request count
        m = {"type": type_, "ts": int(time.time() * 1000), "payload": payload or {}}
        if id_:
            m["id"] = id_
        await self.ws.send(json.dumps(m))

    async def wait_for(self, pred, timeout=5.0):
        import sys, traceback; _t0 = time.monotonic()
        try:
            return await self._wait_for(pred, timeout)
        finally:
            d = time.monotonic() - _t0
            if d > 0.5: print(f"SLOW {d:.2f}s line {traceback.extract_stack()[-2].lineno}", file=sys.stderr)

    async def _wait_for(self, pred, timeout=5.0):
        end = time.monotonic() + timeout
        for m in self.msgs:
            if pred(m):
                return m
        while time.monotonic() < end:
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=max(0.01, end - time.monotonic()))
            except asyncio.TimeoutError:
                break
            m = json.loads(raw)
            assert {"type", "ts", "payload"} <= set(m)
            self.msgs.append(m)
            if pred(m):
                return m
        raise AssertionError(f"timeout waiting; got types {[x['type'] for x in self.msgs[-20:]]}")

    def clear(self):
        self.msgs.clear()


def status_is(**kw):
    return lambda m: m["type"] == "agent.status" and all(m["payload"].get(k) == v for k, v in kw.items())


def test_agent_context_endpoint_marks_capture_and_transport_state(server) -> None:
    app, _ctl, port = server
    app.state.hub._network_status = {
        "state": "ready",
        "result": {"capture_path": "/tmp/evidence.pcap", "world": {"agent_context": {"map": {"map_id": 42}}}},
        "error": None,
    }
    response = httpx.get(f"http://127.0.0.1:{port}/api/agent/context")
    assert response.status_code == 200
    body = response.json()
    routing = body.pop("world_routing")
    assert body == {
        "version": 1, "source": "dofus-protocol", "account_id": 0, "scan_state": "ready",
        "capture_path": "/tmp/evidence.pcap", "observed_at_ms": None, "context": {"map": {"map_id": 42}},
        "quest_plan": {"source": "dofus-protocol + installed-assets + on-demand-guide-index", "active_quest_plans": [], "inventory_items_observed": 0},
        "action_status": {"idle": True, "pending": {}, "all": {"dialogue": {"state": "idle"}, "interactive": {"state": "idle"}, "navigation": {"state": "idle"}, "combat": {"state": "idle"}, "inventory": {"state": "idle"}, "attack": {"state": "idle"}, "talk": {"state": "idle"}, "interruption": {"state": "idle"}}, "fight_readiness": {"ready": False, "reason": "character health is not protocol-confirmed; refusing to start a fight"}},
        "transport": {"state": "offline", "session_id": None, "error": None, "uid": 1, "sent": 0},
    }
    assert routing["source"].endswith("world-graph.json")
    assert isinstance(routing["available"], bool)


def test_agent_intent_endpoint_validates_then_uses_managed_transport(server) -> None:
    app, _ctl, port = server
    calls: list[tuple[object, int]] = []

    class Codec:
        def encode(self, intent, uid):
            calls.append((intent, uid))
            return b"wire"

    app.state.hub._network_status = {"state": "ready", "error": None, "result": {
        "map_id": 42, "character": {"hp": 100, "hp_max": 100},
        "groups": [{"entity_id": -8, "cell": 100, "members": []}],
    }}
    app.state.hub.managed_transport.attach("test", Codec(), lambda _frame: None)
    response = httpx.post(f"http://127.0.0.1:{port}/api/agent/intent", json={
        "action": "attack_monster_group", "args": {"entity_id": -8}, "reason": "test",
    })
    assert response.status_code == 200
    assert response.json()["confirmation"] == "waiting for live fight-start state"
    assert calls[0][0].kind == "attack_monster" and calls[0][0].payload == {"monster_group_id": -8}


def test_guide_endpoints_resolve_success_preflight_and_quest_source(server) -> None:
    _app, _ctl, port = server
    success = httpx.get(f"http://127.0.0.1:{port}/api/guides/success/649")
    assert success.status_code == 200
    assert success.json()["preparation"]["items"][0] == ["Bois de Frêne", 10]
    quest = httpx.get(f"http://127.0.0.1:{port}/api/guides/1649")
    assert quest.status_code == 200
    assert quest.json()["group"]["achievement_id"] == 649


def test_agent_quest_tools_are_read_only_and_limited_to_live_active_quests(server) -> None:
    app, _ctl, port = server
    app.state.hub._network_status = {"state": "ready", "error": None, "result": {
        "quests": {"active": [{"quest_id": 1649, "objectives": []}], "completed": []},
        "inventory": {"objects": []},
    }}
    plan = httpx.post(f"http://127.0.0.1:{port}/api/agent/intent", json={"action": "inspect_active_quests"})
    assert plan.status_code == 200
    assert plan.json()["sent"] is False
    guide = httpx.post(f"http://127.0.0.1:{port}/api/agent/intent", json={
        "action": "get_active_quest_guide", "args": {"quest_id": 1649},
    })
    assert guide.status_code == 200
    assert guide.json()["guide"]["group"]["achievement_id"] == 649
    denied = httpx.post(f"http://127.0.0.1:{port}/api/agent/intent", json={
        "action": "get_active_quest_guide", "args": {"quest_id": 999},
    })
    assert denied.status_code == 400


def test_agent_rejects_mutation_while_another_protocol_action_is_pending(server) -> None:
    from dofus_brain.world.navigation_execution import PendingTransition

    app, _ctl, port = server
    app.state.hub._network_status = {"state": "ready", "error": None, "result": {
        "map_id": 42, "groups": [{"entity_id": -8, "cell": 100, "members": []}],
    }}
    app.state.hub._navigation_executor.pending = PendingTransition(99, 42, 43, 1001, 77)
    blocked = httpx.post(f"http://127.0.0.1:{port}/api/agent/intent", json={
        "action": "attack_monster_group", "args": {"entity_id": -8},
    })
    assert blocked.status_code == 400
    assert "awaiting confirmation (navigation)" in blocked.json()["detail"]
    readable = httpx.post(f"http://127.0.0.1:{port}/api/agent/intent", json={"action": "inspect_active_quests"})
    assert readable.status_code == 200


def test_agent_propose_uses_local_model_but_does_not_execute(server) -> None:
    app, _ctl, port = server
    app.state.hub._network_status = {"state": "ready", "error": None, "result": {"map_id": 42}}
    app.state.hub._llm_coordinator.llm.responder = {"action": "observe_world", "args": {}, "reason": "check state"}
    response = httpx.post(f"http://127.0.0.1:{port}/api/agent/propose", json={"goal": "assess map"})
    assert response.status_code == 200
    body = response.json()
    assert body["proposal"] == {"action": "observe_world", "args": {}, "reason": "check state"}
    assert body["accepted"] is True
    assert body["queue"]["completed"] == 1


def test_live_trade_request_is_default_denied_once_through_managed_transport(server) -> None:
    app, _ctl, _port = server
    calls = []

    class Codec:
        def encode(self, intent, uid):
            calls.append((intent, uid))
            return b"wire"

        def request_uid(self, intent, uid):
            return -1

    app.state.hub.managed_transport.attach("test", Codec(), lambda _frame: None)
    payload = {"map_events": [{"kind": "trade_requested", "offset": 91, "source_id": 77}]}
    app.state.hub._process_interruptions(payload)
    app.state.hub._process_interruptions(payload)
    assert [intent.kind for intent, _uid in calls] == ["dialog_leave"]
    assert app.state.hub.action_status_payload()["pending"]["interruption"]["kind"] == "trade_request"
    app.state.hub._process_interruptions({"map_events": [{"kind": "exchange_left", "offset": 92}]})
    assert app.state.hub.action_status_payload()["idle"] is True


def test_route_endpoint_reports_unroutable_unknown_source(server) -> None:
    app, _ctl, port = server
    app.state.hub._network_status = {"state": "ready", "error": None, "result": {"map_id": 42}}
    response = httpx.get(f"http://127.0.0.1:{port}/api/routes/43")
    assert response.status_code == 200
    body = response.json()
    assert body["found"] is False
    assert body["source_map_id"] == 42
    assert body["target_map_id"] == 43
    assert body["reason"]


async def test_e2e(server):
    app, ctl, port = server
    r = httpx.get(f"http://127.0.0.1:{port}/health")
    assert r.json()["ok"] and r.json()["mock"] is True

    async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as raw, \
            websockets.connect(f"ws://127.0.0.1:{port}/ws") as raw2:
        c, c2 = WS(raw), WS(raw2)
        hello = await c.wait_for(lambda m: m["type"] == "hello")
        assert hello["payload"] == {"version": 1, "server": "dofus-brain", "mock": True}
        st = await c.wait_for(lambda m: m["type"] == "agent.status")
        p = st["payload"]
        assert p["state"] == "idle" and p["mode"] == "observe"
        assert set(p) >= {"state", "mode", "goal", "task", "proposed", "loop_hz", "last_decision_ms", "llm",
                          "control", "error", "uptime_s"}
        assert p["llm"]["backend"] == "mock"
        gs = await c.wait_for(lambda m: m["type"] == "game.state" and m["payload"]["map"]["coords"] == [4, -18])
        assert gs["payload"]["screen"] == "world"
        await c.wait_for(lambda m: m["type"] == "log.history")
        ov = await c.wait_for(lambda m: m["type"] == "overlay.status")
        assert ov["payload"]["dofus_window"]["found"] is True
        await c2.wait_for(lambda m: m["type"] == "hello")  # second client works too

        # goal in observe mode: proposes, never acts
        await c.send("steer.goal", {"text": "va à droite"}, "g1")
        ack = await c.wait_for(lambda m: m["type"] == "ack" and m.get("id") == "g1")
        assert ack["payload"]["ok"] is True
        await c.wait_for(status_is(state="running", goal="va à droite"))
        await c.wait_for(lambda m: m["type"] == "agent.status" and m["payload"]["proposed"] is not None)
        await asyncio.sleep(0.5)
        assert ctl.executed == []

        # auto: actions flow, map changes, logged with the action
        await c.send("steer.mode", {"mode": "auto"}, "m1")
        await c.wait_for(status_is(mode="auto"))
        await c.wait_for(lambda m: m["type"] == "log.entry" and "action" in m["payload"])
        await c.wait_for(lambda m: m["type"] == "game.state" and m["payload"]["map"]["coords"] == [5, -18])
        await c2.wait_for(lambda m: m["type"] == "game.state" and m["payload"]["map"]["coords"] == [5, -18])
        assert len(ctl.executed) >= 1 and ctl.executed[0]["kind"] == "click"

        # pause: no more actions
        await c.send("steer.pause", {}, "p1")
        await c.wait_for(status_is(state="paused"))
        await asyncio.sleep(0.3)
        n = len(ctl.executed)
        await asyncio.sleep(0.6)
        assert len(ctl.executed) == n

        # kill: stopped, controller killed, no actions even after...
        await c.send("steer.kill", {}, "k1")
        await c.wait_for(status_is(state="stopped"))
        assert ctl.killed
        await asyncio.sleep(0.5)
        assert len(ctl.executed) == n

        # resume from stopped resets the kill switch and acts again in auto
        await c.send("steer.resume", {}, "r1")
        await c.wait_for(status_is(state="running"))
        assert not ctl.killed
        await c.wait_for(lambda m: m["type"] == "game.state" and m["payload"]["map"]["coords"] == [6, -18], timeout=8)
        assert len(ctl.executed) > n

        # back to observe: stops acting
        await c.send("steer.mode", {"mode": "observe"}, "o1")
        await c.wait_for(status_is(mode="observe"))
        await asyncio.sleep(0.3)
        n2 = len(ctl.executed)
        await asyncio.sleep(0.5)
        assert len(ctl.executed) == n2

        # step outside assist -> ack error; unknown type -> ack error
        await c.send("steer.step", {}, "s1")
        ack = await c.wait_for(lambda m: m["type"] == "ack" and m.get("id") == "s1")
        assert ack["payload"]["ok"] is False and "assist" in ack["payload"]["error"]
        await c.send("nope", {}, "u1")
        ack = await c.wait_for(lambda m: m["type"] == "ack" and m.get("id") == "u1")
        assert ack["payload"]["ok"] is False

        # after the kill switch the window is left alone until Start takes it again
        await c.send("session.start", {}, "st1")
        assert (await c.wait_for(lambda m: m["type"] == "ack" and m.get("id") == "st1"))["payload"]["ok"]

        # slot report -> overlay.status with target + aligned (mock moves the window)
        rep = {"rect_css": {"x": 300, "y": 60, "w": 1280, "h": 800},
               "window": {"screen_x": 0, "screen_y": 25, "outer_w": 1700, "outer_h": 1000, "inner_w": 1700, "inner_h": 920},
               "dpr": 2, "zoom_hint": 1, "estimate_screen": {"x": 300, "y": 165, "w": 1280, "h": 800}}
        await c.send("slot.report", rep, "sl1")
        ov = await c.wait_for(lambda m: m["type"] == "overlay.status" and m["payload"]["slot"] is not None)
        assert ov["payload"]["slot"] == {"x": 300.0, "y": 165.0, "w": 1280.0, "h": 800.0}
        await c.wait_for(lambda m: m["type"] == "overlay.status" and m["payload"]["aligned"])
        await c.send("overlay.calibrate", {}, "cal")
        ack = await c.wait_for(lambda m: m["type"] == "ack" and m.get("id") == "cal")
        assert ack["payload"]["ok"]
        await c.send("overlay.align", {}, "al")
        assert (await c.wait_for(lambda m: m["type"] == "ack" and m.get("id") == "al"))["payload"]["ok"]

        # preview only for the subscribed client
        await c.send("preview.subscribe", {"enabled": True}, "pv")
        pv = await c.wait_for(lambda m: m["type"] == "frame.preview", timeout=4)
        assert pv["payload"]["width"] <= 640 and pv["payload"]["jpeg_b64"]
        assert not any(m["type"] == "frame.preview" for m in c2.msgs)

        # periodic status (status_interval_s=0.5 here; protocol requires <= 2 s)
        c.clear()
        t0 = time.monotonic()
        await c.wait_for(lambda m: m["type"] == "agent.status", timeout=2.5)
        assert time.monotonic() - t0 <= 2.5


async def test_game_state_throttled(server):
    app, ctl, port = server
    hub = app.state.hub
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as raw:
        c = WS(raw)
        await c.wait_for(lambda m: m["type"] == "overlay.status")
        c.clear()
        loop = hub.loop
        t0 = time.monotonic()
        for _ in range(100):
            loop.call_soon_threadsafe(hub._dispatch, "state", None)
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.3)
        while True:
            try:
                c.msgs.append(json.loads(await asyncio.wait_for(raw.recv(), 0.05)))
            except asyncio.TimeoutError:
                break
        n = sum(m["type"] == "game.state" for m in c.msgs)
        dur = time.monotonic() - t0
        assert 1 <= n <= dur * 10 + 2
