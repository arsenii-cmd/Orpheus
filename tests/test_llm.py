import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from orpheus.config import Config
from orpheus.llm import LLMError, Ollama


@pytest.fixture
def server():
    bodies = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            bodies.append(body)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            for chunk in [{"message": {"role": "assistant", "content": "При"}, "done": False},
                          {"message": {"role": "assistant", "content": "вет"}, "done": False},
                          {"message": {"role": "assistant", "content": ""}, "done": True,
                           "prompt_eval_count": 7, "eval_count": 2}]:
                self.wfile.write((json.dumps(chunk) + "\n").encode())
                self.wfile.flush()

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % httpd.server_port, bodies
    httpd.shutdown()


def test_stream_and_request_body(server):
    url, bodies = server
    llm = Ollama(Config(ollama=url, model="m", num_ctx=4096))
    chunks = list(llm.chat([{"role": "user", "content": "hi"}], tools=[{"x": 1}]))
    assert "".join(c["message"]["content"] for c in chunks) == "Привет"
    assert chunks[-1]["prompt_eval_count"] == 7
    body = bodies[0]
    assert body["keep_alive"] == -1 and body["stream"] is True and body["tools"] == [{"x": 1}]
    assert body["options"] == {"num_ctx": 4096, "temperature": 0.6, "num_batch": 128}
    assert "think" not in body


def test_warmup_keeps_num_ctx_so_the_model_is_not_reloaded(server):
    url, bodies = server
    llm = Ollama(Config(ollama=url, model="m", num_ctx=4096, think="0"))
    assert llm.warmup([{"role": "system", "content": "s"}])["done"]
    list(llm.chat([{"role": "system", "content": "s"}]))
    assert bodies[0]["options"]["num_ctx"] == bodies[1]["options"]["num_ctx"] == 4096
    assert bodies[0]["options"]["num_predict"] == 1 and "num_predict" not in bodies[1]["options"]
    assert bodies[0]["think"] is False


def test_unreachable_ollama_gives_a_clear_error():
    llm = Ollama(Config(ollama="http://127.0.0.1:9", model="m"))
    with pytest.raises(LLMError, match="недоступна"):
        list(llm.chat([]))


def test_a_dead_runner_is_unloaded_and_the_request_made_again():
    """Ollama after a GPU reset: "model runner has unexpectedly stopped" to every request until unloaded."""
    bodies, state = [], {"dead": True}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            bodies.append((self.path, body))
            if self.path == "/api/generate" and body.get("keep_alive") == 0:
                state["dead"] = False
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"done": true, "done_reason": "unload"}')
                return
            if state["dead"]:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'{"error": "model runner has unexpectedly stopped: vk::Queue::submit: ErrorDeviceLost"}')
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write((json.dumps({"message": {"content": "Да."}, "done": True}) + "\n").encode())

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        llm = Ollama(Config(ollama="http://127.0.0.1:%d" % httpd.server_port, model="m"))
        assert [c["message"]["content"] for c in llm.chat([{"role": "user", "content": "?"}])] == ["Да."]
        assert [p for p, _ in bodies] == ["/api/chat", "/api/generate", "/api/chat"]
    finally:
        httpd.shutdown()
