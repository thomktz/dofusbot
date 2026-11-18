import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from dofus_brain.llm import LLMError, MockLLM, extract_json, make_llm
from dofus_brain.llm.jsonparse import JSONParseError
from dofus_brain.llm.mlx import MLXClient
from dofus_brain.llm.ollama import OllamaClient


class FakeServer:
    """Tiny HTTP server: `responses` is a list of (status, body|callable) served in order."""

    def __init__(self, responses, sse=False, delay=0.0):
        self.responses = list(responses)
        self.requests: list[dict] = []
        self.paths: list[str] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self):
                n = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                outer.requests.append(body)
                outer.paths.append(self.path)
                if delay:
                    import time

                    time.sleep(delay)
                status, resp = outer.responses.pop(0) if outer.responses else (200, {})
                if sse:
                    self.send_response(status)
                    self.send_header("content-type", "text/event-stream")
                    self.end_headers()
                    for chunk in resp:
                        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                        self.wfile.flush()
                    self.wfile.write(b"data: [DONE]\n\n")
                    return
                raw = json.dumps(resp).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_POST = _reply
            do_GET = _reply

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


def ollama_reply(content, **extra):
    return {"model": "m", "message": {"role": "assistant", "content": content}, "done": True,
            "load_duration": 1_500_000_000, "prompt_eval_count": 1300, "prompt_eval_duration": 650_000_000,
            "eval_count": 40, "eval_duration": 1_000_000_000, **extra}


@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": [1, 2]}\n```', {"a": [1, 2]}),
    ('Voici la réponse : {"screen": "fight", "confidence": 0.8} voilà', {"screen": "fight", "confidence": 0.8}),
    ('<think>hmm {"x":0}</think>{"ok": true}', {"ok": True}),
    ('{"a": 1, "b": [1,2,],}', {"a": 1, "b": [1, 2]}),
    ('{"s": "accolade } dans une chaîne"}', {"s": "accolade } dans une chaîne"}),
])
def test_extract_json(text, expected):
    assert extract_json(text) == expected


def test_extract_json_fails():
    with pytest.raises(JSONParseError):
        extract_json("pas de json ici")


async def test_ollama_json_retry_images_and_timings():
    srv = FakeServer([(200, ollama_reply("désolé, je ne sais pas")),
                      (200, ollama_reply('{"screen": "dialog", "confidence": 0.9}'))])
    try:
        c = OllamaClient(model="qwen2.5vl:7b", url=srv.url, retries=2, max_image_side=320)
        img = np.zeros((800, 1280, 3), np.uint8)
        r = await c.chat_json("Quel écran ?", images=[img], schema={"type": "object"})
        assert r.data == {"screen": "dialog", "confidence": 0.9}
        assert r.attempts == 2
        req = srv.requests[0]
        assert srv.paths[0] == "/api/chat"
        assert req["model"] == "qwen2.5vl:7b" and req["stream"] is False
        assert req["format"] == {"type": "object"}                 # structured output
        assert req["think"] is False
        assert len(req["messages"][-1]["images"]) == 1             # base64 image in the message
        import base64, io
        from PIL import Image

        im = Image.open(io.BytesIO(base64.b64decode(req["messages"][-1]["images"][0])))
        assert max(im.size) == 320                                 # downscaled
        assert "invalide" in srv.requests[1]["messages"][-1]["content"]   # error fed back
        t = r.timings
        assert t.load_ms == pytest.approx(1500) and t.prompt_tokens == 1300
        assert t.prompt_tok_s == pytest.approx(2000) and t.tok_s == pytest.approx(40)
        assert t.total_ms > 0 and c.last_latency_ms == t.total_ms
        await c.aclose()
    finally:
        srv.close()


async def test_ollama_plain_json_mode_and_gives_up():
    srv = FakeServer([(200, ollama_reply("nope"))] * 3)
    try:
        c = OllamaClient(model="m", url=srv.url, retries=2)
        with pytest.raises(LLMError):
            await c.chat_json("x")
        assert len(srv.requests) == 3 and srv.requests[0]["format"] == "json"
        await c.aclose()
    finally:
        srv.close()


async def test_ollama_validation_retry():
    srv = FakeServer([(200, ollama_reply('{"screen": "banana"}')), (200, ollama_reply('{"screen": "world"}'))])

    def valid(d):
        if d.get("screen") not in ("world", "fight"):
            raise ValueError("bad screen")
        return d

    try:
        c = OllamaClient(model="m", url=srv.url)
        r = await c.chat_json("x", validate=valid)
        assert r.data["screen"] == "world" and r.attempts == 2
        await c.aclose()
    finally:
        srv.close()


async def test_ollama_errors_and_timeout():
    srv = FakeServer([(404, {"error": "model not found"})])
    try:
        c = OllamaClient(model="ghost", url=srv.url)
        with pytest.raises(LLMError, match="ollama pull ghost"):
            await c.chat("x")
        await c.aclose()
    finally:
        srv.close()
    slow = FakeServer([(200, ollama_reply("{}"))], delay=1.0)
    try:
        c = OllamaClient(model="m", url=slow.url, timeout_s=0.2)
        with pytest.raises(LLMError, match="timed out"):
            await c.chat("x")
        await c.aclose()
    finally:
        slow.close()
    c = OllamaClient(model="m", url="http://127.0.0.1:9")
    with pytest.raises(LLMError, match="unreachable"):
        await c.chat("x")
    assert (await c.health())["ok"] is False
    await c.aclose()


async def test_mlx_streaming():
    chunks = [{"choices": [{"delta": {"content": '{"screen":'}}]},
              {"choices": [{"delta": {"content": ' "fight"}'}}]},
              {"choices": [], "usage": {"prompt_tokens": 900, "completion_tokens": 6}}]
    srv = FakeServer([(200, chunks)], sse=True)
    try:
        c = MLXClient(model="mlx-community/x", url=srv.url)
        r = await c.chat_json("x", images=[np.zeros((10, 10, 3), np.uint8)])
        assert r.data == {"screen": "fight"}
        req = srv.requests[0]
        assert srv.paths[0] == "/v1/chat/completions" and req["stream"] is True
        parts = req["messages"][-1]["content"]
        assert parts[0]["type"] == "text" and parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        assert r.timings.ttft_ms is not None and r.timings.prompt_tokens == 900 and r.timings.eval_tokens == 6
        await c.aclose()
    finally:
        srv.close()


async def test_mlx_non_stream():
    srv = FakeServer([(200, {"choices": [{"message": {"content": '{"a": 1}'}}],
                             "usage": {"prompt_tokens": 10, "completion_tokens": 4, "prompt_tps": 100.0, "generation_tps": 20.0}})])
    try:
        c = MLXClient(model="m", url=srv.url, stream=False)
        r = await c.chat_json("x")
        assert r.data == {"a": 1} and r.timings.tok_s == pytest.approx(20.0)
        await c.aclose()
    finally:
        srv.close()


async def test_mock_and_factory():
    m = MockLLM(responder=lambda prompt, n: {"echo": prompt[:3], "images": n})
    r = await m.chat_json("abcdef", images=[np.zeros((2, 2, 3), np.uint8)])
    assert r.data == {"echo": "abc", "images": 1}
    assert make_llm({"backend": "mock"}).backend == "mock"
    o = make_llm({"backend": "ollama", "model": "qwen3-vl:4b"})
    assert o.backend == "ollama" and o.model == "qwen3-vl:4b"
    await o.aclose()
    assert make_llm({"backend": "mlx"}).backend == "mlx"
    with pytest.raises(ValueError):
        make_llm({"backend": "gpt"})
