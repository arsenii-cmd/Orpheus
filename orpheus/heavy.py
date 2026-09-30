"""The heavy parts - speech recognition, the voices, the owner's voice check and the language model - and where
they run.

LocalHeavy: in this very process (the laptop alone, as it was: `python -m orpheus server`).
RemoteHeavy: on the laptop, reached from the server ("hub") through a connection the laptop itself opens and
holds (`python -m orpheus worker`): no port on the home router, no VPN. The hub keeps the data and the
scripts; the laptop only hears, speaks and thinks. The voiceprint stays on the laptop, at home.

The worker's protocol (JSON text frames; audio as binary frames that start with the job's id, 4 bytes LE):
  hub -> worker  {"job": "hear", "id", "headset", "strict"} + audio   -> {"id", "text", "score", "owner", "refused"}
                 {"job": "synth", "id", "text", "voice"}              -> {"id", "rate"} + audio
                 {"job": "chat", "id", "messages", "num_predict"}     -> {"id", "chunk"}… {"id", "end": true}
                 {"job": "enroll", "id", "headset"} + audio           -> {"id", "why", "status"}
                 {"job": "enroll_reset", "id"}                        -> {"id", "status"}
  worker -> hub  {"hello": {"status": {"count", "needed", "mode"}}} once connected
Any reply may be {"id", "error": "…"} instead.
"""

import asyncio
import dataclasses
import json
import os
import ssl
import struct
import threading
import time
from pathlib import Path

import numpy as np

from .config import Config

HEAD = struct.Struct("<I")


def _mic(headset):
    """The "headset" flag of a phrase as a microphone's name, None when the app did not say."""
    return None if headset is None else "headset" if headset else "phone"


def to_pcm16(samples):
    return (np.clip(np.asarray(samples, dtype=np.float32), -1, 1) * 32767).astype("<i2").tobytes()


def from_pcm16(pcm):
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768


class Unreachable(RuntimeError):
    """The laptop (the worker) is not connected: nothing can be heard or said."""


class LocalHeavy:
    """Everything heavy, loaded once in this process."""

    def __init__(self, config: Config, log=print):
        from .speech import Voice
        from .stt import Recognizer
        from .voiceprint import NEEDED, OwnerCheck

        self.needed = NEEDED
        self.recognizer = Recognizer(config)
        # both voices stay loaded (~80 MB each); the phone picks one per phrase ("voice" in "start")
        self.voices = {"male": Voice(config)}
        try:
            self.voices["female"] = Voice(dataclasses.replace(config, voice=config.voice_female))
        except SystemExit:
            log("женского голоса %s нет, будет только мужской" % config.voice_female)
        self.default_voice = os.environ.get("ORPHEUS_SERVER_VOICE", "male")
        self.owner = OwnerCheck(config.models, config.voiceprint, config.speaker, config.speaker_threshold)
        if self.owner.problem:
            log(self.owner.problem)
        log("голос владельца: режим %s, %s" % (self.owner.mode, "записан (%d фраз), порог %.2f" % (
            self.owner.voiceprint.count, self.owner.threshold) if self.owner.voiceprint.ready else "не записан"))
        self.llm = None  # the brain's own Ollama client

    def voice(self, name):
        return self.voices.get(name) or self.voices.get(self.default_voice) or self.voices["male"]

    def hear_now(self, samples, headset=None, strict=False):
        """-> (text, score, owner, refused): what was said and whether its voice is let through. strict: only the
        owner's voice gets an answer (asked for the buds only: the phone's own microphone scores the owner low)."""
        found = {}

        def judge():
            found["judged"] = self.owner.judge(samples, self.owner.learns(_mic(headset)))

        checking = threading.Thread(target=judge)  # side by side: the voice check adds no delay
        checking.start()
        text = self.recognizer.transcribe(samples)
        checking.join()
        score, owner = found["judged"]
        return text, score, owner, self.owner.refuses(owner, strict and headset is not False)

    async def hear(self, samples, headset=None, strict=False):
        return await asyncio.to_thread(self.hear_now, samples, headset, strict)

    async def synth(self, text, voice=""):
        return await asyncio.to_thread(self.voice(voice).synth, text)

    def status(self):
        return {"count": self.owner.voiceprint.count, "needed": self.needed, "mode": self.owner.mode}

    async def enroll(self, samples, headset=None):
        return await asyncio.to_thread(self.owner.enroll, samples, _mic(headset))

    async def enroll_reset(self):
        await asyncio.to_thread(self.owner.voiceprint.reset)


class RemoteHeavy:
    """The laptop's heavy parts, over its connection to this server."""

    def __init__(self, log=print):
        self.log = log
        self.ws = None
        self.loop = None
        self.next_id = 0
        self.waiting = {}  # id -> Future (one reply) or Queue (a stream)
        self.audio = {}  # id -> the audio that came with or for a reply
        self.last_status = {"count": 0, "needed": 0, "mode": "off"}
        self.connected = asyncio.Event()
        self.llm = RemoteLLM(self)
        self.on_connect = None  # called when the laptop comes (the model is warmed up then)

    # ---------------------------------------------------------------- the laptop's side of the connection

    async def serve(self, ws):
        """One connection of the worker: until it goes. A new one replaces the old."""
        if self.ws is not None:
            await self.ws.close()
        self.ws, self.loop = ws, asyncio.get_running_loop()
        self.connected.set()
        self.log("голова подключилась: %s" % (ws.remote_address[0],))
        try:
            async for message in ws:
                if isinstance(message, bytes):
                    (job,) = HEAD.unpack_from(message)
                    self.audio.setdefault(job, bytearray()).extend(message[HEAD.size:])
                    continue
                msg = json.loads(message)
                if "hello" in msg:
                    self.last_status = msg["hello"].get("status") or self.last_status
                    if self.on_connect:
                        self.on_connect()
                    continue
                waiter = self.waiting.get(msg.get("id"))
                if "status" in msg:
                    self.last_status = msg["status"]
                if isinstance(waiter, asyncio.Queue):
                    waiter.put_nowait(msg)
                elif waiter is not None and not waiter.done():
                    waiter.set_result(msg)
        finally:
            if self.ws is ws:
                self.ws = None
                self.connected.clear()
                for waiter in self.waiting.values():  # nobody will answer them now
                    if isinstance(waiter, asyncio.Queue):
                        waiter.put_nowait({"error": "голова отключилась"})
                    elif not waiter.done():
                        waiter.set_exception(Unreachable("голова отключилась"))
                self.log("голова отключилась")

    # ---------------------------------------------------------------- jobs

    async def _call(self, job, audio=None, timeout=120):
        future = asyncio.get_running_loop().create_future()
        job_id = None
        try:
            ws = self.ws
            if ws is None:
                raise Unreachable("голова (ноут) не в сети")
            self.next_id += 1
            job_id = job["id"] = self.next_id
            self.waiting[job_id] = future
            if audio:  # the audio first: when the job itself comes, all of it is there
                step = 1 << 16
                for i in range(0, len(audio), step):
                    await ws.send(HEAD.pack(job_id) + audio[i:i + step])
            await ws.send(json.dumps(job, ensure_ascii=False))
            msg = await asyncio.wait_for(future, timeout)
            if "error" in msg:
                raise Unreachable(msg["error"])
            return msg, bytes(self.audio.pop(job_id, b""))
        finally:
            self.waiting.pop(job_id, None)
            self.audio.pop(job_id, None)

    async def hear(self, samples, headset=None, strict=False):
        msg, _ = await self._call({"job": "hear", "headset": headset, "strict": strict}, to_pcm16(samples))
        return msg["text"], msg.get("score"), msg.get("owner", True), msg.get("refused", False)

    async def synth(self, text, voice=""):
        msg, audio = await self._call({"job": "synth", "text": text, "voice": voice})
        return from_pcm16(audio), msg["rate"]

    def status(self):
        return dict(self.last_status)

    async def enroll(self, samples, headset=None):
        msg, _ = await self._call({"job": "enroll", "headset": headset}, to_pcm16(samples))
        return msg.get("why")

    async def enroll_reset(self):
        await self._call({"job": "enroll_reset"})

    async def stream(self, job):
        """A chat job -> the queue its chunks come to ({"chunk"}…, then {"end"} or {"error"})."""
        queue = asyncio.Queue()
        ws = self.ws
        if ws is None:
            raise Unreachable("голова (ноут) не в сети")
        self.next_id += 1
        job["id"] = self.next_id
        self.waiting[job["id"]] = queue
        await ws.send(json.dumps(job, ensure_ascii=False))
        return job["id"], queue

    def done(self, job_id):
        self.waiting.pop(job_id, None)


class RemoteLLM:
    """The brain's model, on the laptop: the same chat() as llm.Ollama, called from the brain's own thread."""

    def __init__(self, heavy: RemoteHeavy):
        self.heavy = heavy

    def chat(self, messages, tools=None, num_predict=None):
        heavy = self.heavy
        if heavy.loop is None or heavy.ws is None:
            raise Unreachable("голова (ноут) не в сети")
        job = {"job": "chat", "messages": messages, "num_predict": num_predict}
        job_id, queue = asyncio.run_coroutine_threadsafe(heavy.stream(job), heavy.loop).result(10)
        try:
            while True:
                msg = asyncio.run_coroutine_threadsafe(asyncio.wait_for(queue.get(), 300), heavy.loop).result()
                if "error" in msg:
                    raise Unreachable(msg["error"])
                if msg.get("end"):
                    return
                yield msg["chunk"]
        finally:
            heavy.loop.call_soon_threadsafe(heavy.done, job_id)

    def warmup(self, messages, tools=None):
        last = {}
        for last in self.chat(messages, tools, num_predict=1):
            pass
        return last


# ------------------------------------------------------------------------------------ the laptop: the worker


def client_ssl(cert_path):
    """TLS to the hub with its own certificate pinned (a self-signed one, made there): nothing else is trusted."""
    ctx = ssl.create_default_context(cafile=str(cert_path))
    ctx.check_hostname = False  # the certificate is the pin itself; its name is the hub's IP
    return ctx


async def work(config: Config, url: str, token: str, cert: Path, log=print, heavy=None, llm=None):
    """Hold a connection to the hub and do what it asks, forever (reconnecting when it drops)."""
    from websockets.asyncio.client import connect

    if heavy is None:
        heavy = LocalHeavy(config, log)
    if llm is None:
        from .llm import Ollama
        llm = Ollama(config)
    busy = asyncio.Semaphore(1)  # one phrase at a time through the recogniser and the voices
    delay = 2
    while True:
        try:
            async with connect(url, ssl=client_ssl(cert), additional_headers={"Authorization": "Bearer " + token}, proxy=None,
                               max_size=None, ping_interval=20, ping_timeout=40) as ws:
                log("подключился к %s" % url)
                delay = 2
                await ws.send(json.dumps({"hello": {"status": heavy.status()}}))
                audio = {}
                tasks = set()
                async for message in ws:
                    if isinstance(message, bytes):
                        (job,) = HEAD.unpack_from(message)
                        audio.setdefault(job, bytearray()).extend(message[HEAD.size:])
                        continue
                    job = json.loads(message)
                    task = asyncio.create_task(_do(ws, job, audio, heavy, llm, busy, log))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
        except Exception as exc:  # the hub away, the network down: again later
            log("нет связи с хабом (%s): через %d с снова" % (type(exc).__name__, delay))
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


async def _do(ws, job, audio, heavy: LocalHeavy, llm, busy, log):
    job_id = job.get("id")
    kind = job.get("job")

    async def reply(msg):
        await ws.send(json.dumps(dict(msg, id=job_id), ensure_ascii=False))

    try:
        if kind == "chat":
            await _chat(ws, job_id, job, llm)
            return
        samples = from_pcm16(bytes(audio.pop(job_id, b"")))
        async with busy:
            if kind == "hear":
                text, score, owner, refused = await heavy.hear(samples, job.get("headset"), bool(job.get("strict")))
                await reply({"text": text, "score": score, "owner": bool(owner), "refused": bool(refused)})
            elif kind == "synth":
                voiced, rate = await heavy.synth(job.get("text", ""), job.get("voice", ""))
                pcm = to_pcm16(voiced)
                step = 1 << 16
                for i in range(0, len(pcm), step):  # the audio first: the reply after it says it is all there
                    await ws.send(HEAD.pack(job_id) + pcm[i:i + step])
                await reply({"rate": rate})
            elif kind == "enroll":
                why = await heavy.enroll(samples, job.get("headset"))
                await reply({"why": why, "status": heavy.status()})
            elif kind == "enroll_reset":
                await heavy.enroll_reset()
                await reply({"status": heavy.status()})
            else:
                await reply({"error": "неизвестная задача %r" % kind})
    except Exception as exc:
        log("задача %s не выполнена: %s" % (kind, exc))
        await reply({"error": "%s: %s" % (type(exc).__name__, exc)})


async def _chat(ws, job_id, job, llm):
    """The model's answer streamed back chunk by chunk, generated in a thread (Ollama's client is blocking)."""
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()

    def generate():
        try:
            for chunk in llm.chat(job.get("messages") or [], None, num_predict=job.get("num_predict")):
                loop.call_soon_threadsafe(queue.put_nowait, {"chunk": chunk})
            loop.call_soon_threadsafe(queue.put_nowait, {"end": True})
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, {"error": "модель: %s" % exc})

    threading.Thread(target=generate, daemon=True).start()
    while True:
        msg = await queue.get()
        await ws.send(json.dumps(dict(msg, id=job_id), ensure_ascii=False))
        if "chunk" not in msg:
            return


def main(config: Config):
    """`python -m orpheus worker`: the laptop's side (ORPHEUS_HUB, ORPHEUS_HUB_TOKEN_FILE, ORPHEUS_HUB_CERT)."""
    url = os.environ.get("ORPHEUS_HUB", "")
    token_file = Path(os.environ.get("ORPHEUS_HUB_TOKEN_FILE", str(Path.home() / ".config/orpheus/hub-token")))
    cert = Path(os.environ.get("ORPHEUS_HUB_CERT", str(Path.home() / ".config/orpheus/hub-cert.pem")))
    if not url or not token_file.exists() or not cert.exists():
        raise SystemExit("нужны ORPHEUS_HUB (wss://…), токен (%s) и сертификат хаба (%s)" % (token_file, cert))

    def log(text):
        print(time.strftime("%H:%M:%S"), text, flush=True)

    try:
        asyncio.run(work(config, url, token_file.read_text().strip(), cert, log))
    except KeyboardInterrupt:
        pass
