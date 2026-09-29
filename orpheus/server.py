"""The phone's way in: a WebSocket server speaking docs/android-protocol.md.

    python -m orpheus server            # 0.0.0.0:8765, GET /healthz, WebSocket on any other path

The phone sends a phrase as 16 kHz PCM; here it is recognised (GigaAM), answered by the brain
(Ollama) and spoken (Piper), the reply going back sentence by sentence as it is generated.

From outside the phone comes through a reverse proxy elsewhere (it terminates TLS and checks the
token) and a VPN tunnel to this laptop, so every such connection arrives from the tunnel's address.
Only that address and the home LAN are let in (ORPHEUS_ALLOW); the token is checked here too when
it is configured (ORPHEUS_TOKEN_FILE), in case the service is ever reached some other way.
"""

import asyncio
import dataclasses
import hmac
import ipaddress
import json
import os
import re
import threading
import time
from datetime import date, datetime, timedelta
from http import HTTPStatus
from pathlib import Path

import numpy as np
from websockets.asyncio.server import serve

from .brain import Brain
from .config import SAMPLE_RATE, Config
from .memory import Memory, SealedMemory
from .numbers import plural
from .planner import Unavailable, from_config as planner_tools
from .secure import WrongKey, parse_key, shred
from .speech import Sentences, Voice
from .vectors import load_embedder
from .voiceprint import NEEDED as VOICEPRINT_NEEDED, OwnerCheck
from .web import online

ALLOWED = [ipaddress.ip_network(n) for n in os.environ.get(
    "ORPHEUS_ALLOW", "10.8.0.1/32,192.168.2.0/24,127.0.0.0/8").split(",")]
MIN_PHRASE_SEC = 0.3
AUDIO_CHUNK_SEC = 0.2


def allowed(address):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in net for net in ALLOWED)


def strip_wake_word(text, wake="орфей"):
    """The phone sends the audio from the end of «Орфей», but the edge is fuzzy: «фей, какая погода»
    or the whole «Орфей, какая погода» may come through. Drop such a start, keep everything else."""
    from .__main__ import addressed

    rest = addressed(text, wake, within=2)
    if rest is not None:
        return rest
    # «Арфий, …», «Архей, …», «Рофей, …» (a voice from a metre away): the phone heard
    # «Орфей» already, so a first word two letters off it, with a comma after, is that word too
    first = re.match(r"\W*(\w{4,7}),\s*", text)
    if first and _off_by(_as_heard(first.group(1)), _as_heard(wake)) <= 1:
        return text[first.end():].strip()
    # «фей, …», «рфей, …», «А фей, …»: the wake word's tail, maybe after a one-letter shard
    return re.sub(r"^\W*(?:\w\W+)?\w{0,2}фе[йи]\b\W*", "", text, flags=re.I).strip()


def _as_heard(word):
    """A word as it sounds: an unstressed «о» is said «а», a final «ей» all but «и» (Арфий = Орфей)."""
    return word.lower().replace("ё", "е").replace("а", "о").replace("и", "е").rstrip("й")


def _off_by(a, b):
    """Letters to change, add, drop or swap to get b from a."""
    d = [[i + j if not i or not j else 0 for j in range(len(b) + 1)] for i in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


# what a phrase's turn says; with the phrase's "id" when its "start" had one
PHRASE_MESSAGES = {"transcript", "reply", "audio", "audio_end", "error", "enroll"}


def tag_phrase(send, pid):
    """send() that marks what a phrase's turn says with the phrase's "id" from its "start": a reply
    interrupted by the talk button may still be arriving when the next phrase is asked."""
    if pid is None:
        return send

    async def tagged(message):
        if isinstance(message, dict) and message.get("type") in PHRASE_MESSAGES:
            message = dict(message, id=pid)
        await send(message)
    return tagged


def _mic(headset):
    """The "headset" flag of a phrase as a microphone's name, None when the app did not say."""
    return None if headset is None else "headset" if headset else "phone"


def to_pcm16(samples):
    return (np.clip(np.asarray(samples, dtype=np.float32), -1, 1) * 32767).astype("<i2").tobytes()


class Orpheus:
    """The heavy parts, loaded once and shared by all connections (there is one owner anyway)."""

    def __init__(self, config: Config):
        from .stt import Recognizer

        self.config = config
        t = time.monotonic()
        self.recognizer = Recognizer(config)
        # both voices stay loaded (~80 MB each); the phone picks one per phrase ("voice" in "start")
        self.voices = {"male": Voice(config)}
        try:
            self.voices["female"] = Voice(dataclasses.replace(config, voice=config.voice_female))
        except SystemExit:
            log("женского голоса %s нет, будет только мужской" % config.voice_female)
        self.default_voice = os.environ.get("ORPHEUS_SERVER_VOICE", "male")
        embedder = load_embedder(config.models, log)
        memory = Memory(config.db, embedder)
        indexed = memory.reindex()
        if indexed:
            log("проиндексировано для поиска по смыслу: %d" % indexed)
        self.embedder = embedder
        # "Личное" stays closed until the phone hands over its key (secure.py)
        weather, search = online(config)
        if weather is not None:
            weather.start()  # the home forecast kept fresh in the background
        self.brain = Brain(config, memory, locked=True, planner=planner_tools(config, embedder),
                           weather=weather, search=search)
        plain = config.personal_db.with_name("personal.db")
        if plain.exists():  # the unencrypted file of the first version: never again on disk
            shred(plain)
            log("старый незашифрованный personal.db удалён")
        self.owner = OwnerCheck(config.models, config.voiceprint, config.speaker, config.speaker_threshold)
        if self.owner.problem:
            log(self.owner.problem)
        log("голос владельца: режим %s, %s" % (self.owner.mode, "записан (%d фраз), порог %.2f" % (
            self.owner.voiceprint.count, self.owner.threshold) if self.owner.voiceprint.ready else "не записан"))
        try:
            self.brain.warmup()
        except Exception as exc:  # Ollama not up yet at boot: the server starts all the same, the model comes later
            log("модель не прогрета: %s" % exc)
        self.turn_lock = asyncio.Lock()
        self.phones = set()  # the connections' send(), for what the server says by itself (reminders)
        self.last_voice = ""
        self.reminded = set()
        log("модели загружены за %.1f с" % (time.monotonic() - t))

    async def reminders(self):
        """The planner's reminders said aloud on the phone ("Напоминаю: через 15 минут, в 19:00, — тренировка"):
        an event's "remind" minutes before its start, once, while a phone is connected (the Planner's app only
        shows a notification, which in a pocket nobody sees)."""
        tools = self.brain.planner
        if tools is None:
            return
        while True:
            await asyncio.sleep(20)
            if not self.phones:
                continue
            now = datetime.now()
            try:
                items = await asyncio.to_thread(tools.planner.items, now.date(), now.date() + timedelta(days=1))
            except (Unavailable, OSError, ValueError):
                continue
            for key, item in due_reminders(items, now, self.reminded):
                text = reminder_text(item)
                async with self.turn_lock:  # never in the middle of a reply
                    try:
                        samples, rate = await asyncio.to_thread(self.voice(self.last_voice).synth, text)
                    except Exception as exc:
                        log("напоминание не озвучено (%s)" % type(exc).__name__)
                        continue
                    pcm = to_pcm16(samples)
                    step = int(rate * AUDIO_CHUNK_SEC) * 2
                    for send in list(self.phones):
                        try:
                            await send({"type": "announce", "text": text, "sample_rate": rate})
                            for i in range(0, len(pcm), step):
                                await send(pcm[i:i + step])
                            await send({"type": "announce_end"})
                        except Exception:
                            pass  # that connection is going: the others still get it
                self.reminded.add(key)
                log("напоминание: %s" % text)
            if len(self.reminded) > 500:
                today = now.date().isoformat()
                self.reminded = {k for k in self.reminded if k[1] >= today}

    def voice(self, name):
        return self.voices.get(name) or self.voices.get(self.default_voice) or self.voices["male"]

    async def set_personal(self, send, on):
        async with self.turn_lock:
            if self.brain.set_personal(on):
                log("режим: %s (кнопкой)" % ("личное" if on else "обычный"))
        if on and self.brain.locked:
            await send({"type": "error", "message": "Личное закрыто: на телефоне нет ключа"})
        await send({"type": "mode", "personal": self.brain.personal})

    async def leave_personal(self, why):
        async with self.turn_lock:
            if self.brain.set_personal(False):
                log("режим: обычный (%s)" % why)

    async def unlock(self, send, key_text):
        """Open "Личное" with the key from the phone; the key itself is never logged or stored."""
        if not key_text or not self.brain.locked:
            return
        try:
            key = parse_key(key_text)
            private = await asyncio.to_thread(SealedMemory, self.config.personal_db, key, self.embedder)
            await asyncio.to_thread(private.reindex)
        except (ValueError, WrongKey) as exc:
            log("личное не открыто: %s" % exc)
            await send({"type": "error", "message": "Ключ «Личного» не подходит"})
            return
        async with self.turn_lock:
            self.brain.unlock(private)
        log("личное открыто ключом с телефона")

    async def turn(self, send, pcm: bytes, follow_up: bool, voice: str = "", headset=None, strict=False):
        """One phrase in, one spoken reply out. headset: heard through the earbuds (True), the phone's own
        microphone (False) or not said (an older app); strict: only the owner's voice gets an answer."""
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        seconds = len(samples) / SAMPLE_RATE
        if seconds < MIN_PHRASE_SEC:
            await send({"type": "audio_end", "expect_reply": False, "listen": False})
            return
        async with self.turn_lock:
            t0 = time.monotonic()
            was_personal = self.brain.personal
            # what was said and who said it, side by side: the voice check adds no delay
            heard, (score, owner) = await asyncio.gather(
                asyncio.to_thread(self.recognizer.transcribe, samples),
                asyncio.to_thread(self.owner.judge, samples, self.owner.learns(_mic(headset))))
            text = heard if follow_up else strip_wake_word(heard)
            t_stt = time.monotonic() - t0
            voice_note = "" if score is None else " | голос %.2f%s" % (score, "" if owner else " — не владелец")
            if headset is not None:
                voice_note += " | %s%s" % ("наушники" if headset else "телефон", ", строго" if strict else "")
            # strict asked for the buds only: the print is theirs, the phone's microphone scores the owner low
            if self.owner.refuses(owner, strict and headset is not False) or (not owner and was_personal):
                # another voice (the TV, a guest): no answer, and what it said is not written anywhere
                log("фраза %.1f с%s — без ответа" % (seconds, voice_note))
                await send({"type": "audio_end", "expect_reply": False, "listen": False, "reason": "not_owner"})
                return
            await send({"type": "transcript", "text": heard})
            if not text or (follow_up and len(text) < 3):
                # a follow-up window may catch the room talking: stay silent
                log("фраза %.1f с, распознано %s — без ответа" % (seconds, "(личное)" if was_personal else repr(heard)))
                # nothing said (the room's noise): back to waiting for «Орфей», no new follow-up window
                await send({"type": "audio_end", "expect_reply": False, "listen": False})
                return
            self.last_voice = voice or getattr(self, "last_voice", "")
            reply, first_audio = await self._answer(send, text, self.voice(voice))
            timing = "%.1f с | STT %.2f с | первый звук через %s%s" % (
                seconds, t_stt, "%.2f с" % (first_audio - t0) if first_audio else "—", voice_note)
            timing += " | %s" % (self.brain.handled or "—")  # which scenario answered, or the model
            if was_personal or self.brain.personal:
                log("личная фраза " + timing)  # what is said in "Личное" never reaches the journal
            else:
                log("фраза %s | %r -> %r" % (timing, text, reply[:80]))
            if self.brain.personal != was_personal:
                log("режим: %s (голосом или по тишине)" % ("личное" if self.brain.personal else "обычный"))
                await send({"type": "mode", "personal": self.brain.personal})
            # «Стоп», «Хватит»: no more follow-up; «Орфей, не слушать»: the phone pauses until a button
            handled = self.brain.handled
            await send({"type": "audio_end", "expect_reply": reply.rstrip().endswith("?"),
                        "listen": handled not in ("stop", "pause"), "pause": handled == "pause"})

    def enroll_status(self):
        return {"type": "enroll", "count": self.owner.voiceprint.count, "needed": VOICEPRINT_NEEDED,
                "mode": self.owner.mode}

    async def enroll(self, send, pcm: bytes, headset=None):
        """One phrase the owner read to record his voice: no answer, just the progress."""
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        async with self.turn_lock:
            why = await asyncio.to_thread(self.owner.enroll, samples, _mic(headset))
        status = self.enroll_status()
        if why:
            status["error"] = why
        log("голос владельца: %s" % (why or "фраза %d из %d" % (status["count"], VOICEPRINT_NEEDED)))
        await send(status)
        await send({"type": "audio_end", "expect_reply": False, "listen": False})

    async def enroll_reset(self, send):
        async with self.turn_lock:
            self.owner.voiceprint.reset()
        log("голос владельца сброшен")
        await send(self.enroll_status())

    async def _answer(self, send, text, voice):
        """Stream the brain's reply: text pieces as they come, audio sentence by sentence."""
        loop = asyncio.get_running_loop()
        pieces: asyncio.Queue = asyncio.Queue()

        def think():
            try:
                for piece in self.brain.ask(text):
                    loop.call_soon_threadsafe(pieces.put_nowait, piece)
            except Exception as exc:  # the LLM being down must not kill the connection
                loop.call_soon_threadsafe(pieces.put_nowait, exc)
            loop.call_soon_threadsafe(pieces.put_nowait, None)

        threading.Thread(target=think, daemon=True).start()
        splitter = Sentences(first_clause=20)
        reply = ""
        started = False
        first_audio = None

        async def speak(sentence):
            nonlocal started, first_audio
            try:
                samples, rate = await asyncio.to_thread(voice.synth, sentence)
            except Exception as exc:  # one sentence not said must not leave the phone waiting for the rest
                log("синтез не удался (%s): %r" % (type(exc).__name__, sentence[:60]))
                return
            if not started:
                await send({"type": "audio", "sample_rate": rate})
                started = True
                first_audio = time.monotonic()
            pcm = to_pcm16(samples)
            step = int(rate * AUDIO_CHUNK_SEC) * 2
            for i in range(0, len(pcm), step):
                await send(pcm[i:i + step])

        while True:
            piece = await pieces.get()
            if piece is None:
                break
            if isinstance(piece, Exception):
                await send({"type": "error", "message": "мозг недоступен: %s" % piece})
                break
            reply += piece
            await send({"type": "reply", "text": piece})
            for sentence in splitter.feed(piece):
                await speak(sentence)
        for sentence in splitter.flush():
            await speak(sentence)
        return reply, first_audio


def reminder_text(item):
    """"Напоминаю: через 15 минут — тренировка." for an item whose reminder is due."""
    title = item.get("title") or "событие"
    if not title[:2].isupper():  # "ЕГЭ" stays as it is
        title = title[:1].lower() + title[1:]
    minutes = int(item.get("remind") or 0)
    if minutes <= 0:
        return "Напоминаю: сейчас — %s." % title
    if minutes % 60 == 0:
        hours = minutes // 60
        when = "через час" if hours == 1 else "через %d %s" % (hours, plural(hours, "час", "часа", "часов"))
    else:
        when = "через %d %s" % (minutes, plural(minutes, "минуту", "минуты", "минут"))
    return "Напоминаю: %s, в %s, — %s." % (when, item["start_time"], title)


def due_reminders(items, now, done):
    """The events whose reminder time has come (their "remind" minutes before the start) and not yet said: in
    [done] by (id, date, time, remind), so a moved one is said again at its new time."""
    due = []
    for i in items:
        if i.get("kind") != "event" or i.get("done") or not i.get("start_time") or not isinstance(i.get("remind"), int):
            continue
        start = datetime.combine(date.fromisoformat(i["date"]), datetime.strptime(i["start_time"], "%H:%M").time())
        key = (i["id"], i["date"], i["start_time"], i["remind"])
        if key not in done and start - timedelta(minutes=i["remind"]) <= now < start + timedelta(minutes=1):
            due.append((key, i))
    return due


def log(text):
    print(time.strftime("%H:%M:%S"), text, flush=True)


def load_token():
    path = os.environ.get("ORPHEUS_TOKEN_FILE", str(Path.home() / ".config/orpheus/voice-token"))
    try:
        return Path(path).read_text().strip() or None
    except OSError:
        return None


async def run(config: Config, host: str, port: int):
    orpheus = Orpheus(config)
    token = load_token()
    log("токен %s" % ("проверяется" if token else "не задан: доступ только по адресам"))

    def check(connection, request):
        peer = connection.remote_address[0]
        if not allowed(peer):
            log("отказ: %s" % peer)
            return connection.respond(HTTPStatus.FORBIDDEN, "forbidden\n")
        if request.path == "/healthz":
            return connection.respond(HTTPStatus.OK, "ok\n")
        if token:
            given = request.headers.get("Authorization", "")
            if not hmac.compare_digest(given.encode(), ("Bearer " + token).encode()):
                log("неверный токен от %s" % peer)
                return connection.respond(HTTPStatus.UNAUTHORIZED, "unauthorized\n")
        return None

    async def session(ws):
        peer = ws.remote_address[0]
        pcm = bytearray()
        listening = False
        follow_up = False
        enrolling = False
        voice = ""
        headset, strict = None, False
        phrase_id = None
        task = None

        async def send(message):
            await ws.send(message if isinstance(message, bytes) else json.dumps(message, ensure_ascii=False))

        def for_phrase(pid):
            return tag_phrase(send, pid)

        log("подключился %s" % peer)
        try:
            async for message in ws:
                if isinstance(message, bytes):
                    if listening:
                        pcm += message
                    continue
                msg = json.loads(message)
                kind = msg.get("type")
                if kind == "hello":
                    log("устройство: %s" % msg.get("device"))
                    orpheus.phones.add(send)
                    await orpheus.unlock(send, msg.get("personal_key"))
                    # a new connection starts in the ordinary section: «Личное» only by its command or the button,
                    # never carried over from before (the app restarted and was in «Личное» at once)
                    await orpheus.leave_personal("новое подключение")
                    await send({"type": "mode", "personal": orpheus.brain.personal})
                    await send(orpheus.enroll_status())
                elif kind == "enroll_reset":
                    await orpheus.enroll_reset(send)
                elif kind == "mode":
                    await orpheus.set_personal(send, bool(msg.get("personal")))
                elif kind == "start":
                    pcm = bytearray()
                    listening = True
                    follow_up = bool(msg.get("follow_up"))
                    enrolling = bool(msg.get("enroll"))
                    voice = str(msg.get("voice") or "")
                    headset = msg["headset"] if isinstance(msg.get("headset"), bool) else None
                    phrase_id = msg["id"] if isinstance(msg.get("id"), int) and not isinstance(msg.get("id"), bool) else None
                    strict = bool(msg.get("strict"))
                elif kind == "cancel":
                    listening = False
                    pcm = bytearray()
                elif kind == "stop" and listening:
                    listening = False
                    if task and not task.done():
                        task.cancel()
                    if enrolling:
                        task = asyncio.create_task(orpheus.enroll(for_phrase(phrase_id), bytes(pcm), headset))
                    else:
                        task = asyncio.create_task(orpheus.turn(for_phrase(phrase_id), bytes(pcm), follow_up, voice, headset, strict))
        except Exception as exc:
            log("соединение %s: %s" % (peer, exc))
        finally:
            if task and not task.done():
                task.cancel()
            orpheus.phones.discard(send)
            log("отключился %s" % peer)

    reminding = asyncio.create_task(orpheus.reminders())  # kept referenced: a bare task may be collected
    async with serve(session, host, port, process_request=check, max_size=None,
                     ping_interval=20, ping_timeout=20):
        log("слушаю %s:%d (разрешено: %s)" % (host, port, ", ".join(map(str, ALLOWED))))
        await asyncio.Future()


def main(config: Config):
    host = os.environ.get("ORPHEUS_HOST", "0.0.0.0")
    port = int(os.environ.get("ORPHEUS_PORT", "8765"))
    try:
        asyncio.run(run(config, host, port))
    except KeyboardInterrupt:
        pass
