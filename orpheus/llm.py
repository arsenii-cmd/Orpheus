"""Minimal streaming client for the Ollama chat API (no dependencies).

Latency rules this client follows:
- keep_alive=-1: the model stays in RAM, no reload between phrases;
- the same options on every request: a different num_ctx makes Ollama reload the model;
- streaming: text is spoken sentence by sentence while the rest is still being generated;
- a runner that died (the GPU was reset under it: "vk::Queue::submit: ErrorDeviceLost", 26.09) is
  unloaded and the request made once more: Ollama itself kept answering "model runner has
  unexpectedly stopped" to every request until the model was unloaded by hand.
Prompt caching itself happens in Ollama: it reuses the KV cache for the longest prefix
shared with the previous request, so the caller keeps the start of the prompt stable (see brain.py).
"""

import json
import os
import urllib.error
import urllib.request

from .config import Config


class LLMError(RuntimeError):
    pass


def _runner_died(exc):
    return any(s in str(exc) for s in ("unexpectedly stopped", "DeviceLost", "device lost", "runner process has terminated"))


class Ollama:
    def __init__(self, config: Config):
        self.url = config.ollama.rstrip("/")
        self.model = config.model
        temperature = config.temperature
        if "gemma" in config.model.lower() and not os.environ.get("ORPHEUS_TEMPERATURE"):
            # Gemma 4 E4B called the right tool 16 times of 20 at 0.6, 19 of 20 at 0.2 (27.09)
            temperature = 0.2
        self.options = {"num_ctx": config.num_ctx, "temperature": temperature}
        if config.batch:
            self.options["num_batch"] = config.batch
        if config.llm_threads:
            self.options["num_thread"] = config.llm_threads
        self.think = {"0": False, "1": True}.get(config.think)

    def _post(self, path, body, timeout=600):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise LLMError("Ollama %s: %s" % (exc.code, detail)) from None
        except urllib.error.URLError as exc:
            raise LLMError("Ollama недоступна по %s (%s). Запущена ли она?" % (self.url, exc.reason)) from None

    def chat(self, messages, tools=None, num_predict=None):
        """Yield the raw streamed chunks: {"message": {"content", "tool_calls"}, "done", stats...}."""
        body = {"model": self.model, "messages": messages, "stream": True, "keep_alive": -1,
                "options": dict(self.options)}
        if tools:
            body["tools"] = tools
        if self.think is not None:
            body["think"] = self.think
        if num_predict is not None:
            body["options"]["num_predict"] = num_predict
        try:
            resp = self._post("/api/chat", body)
        except LLMError as exc:
            if not _runner_died(exc):
                raise
            self.unload()  # the next request starts a new runner (and reads the prompt anew, ~35 s)
            resp = self._post("/api/chat", body)
        with resp:
            for line in resp:
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise LLMError(chunk["error"])
                yield chunk

    def unload(self):
        try:
            self._post("/api/generate", {"model": self.model, "keep_alive": 0}, timeout=60).close()
        except LLMError:
            pass

    def warmup(self, messages, tools=None):
        """Load the model and fill the KV cache with the prompt prefix; returns the final stats chunk."""
        last = {}
        for last in self.chat(messages, tools, num_predict=1):
            pass
        return last
