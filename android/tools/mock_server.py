#!/usr/bin/env python3
"""A stand-in for the Orpheus server, to try the Android app before the real one exists.

    pip install websockets numpy sherpa-onnx      (sherpa-onnx is optional: without it the reply is a tone)
    python tools/mock_server.py [--port 8765] [--token SECRET] [--save DIR]

In the app: server address ws://<this computer's IP>:8765/ws. Speak after «Орфей»: the server
answers how long the phrase was, in the Piper voice if the Orpheus speech models are around
(~/.local/share/orpheus/models or ~/.cache/orpheus-usb/models). --save keeps every received
phrase as a WAV, to hear exactly what the phone sent (with the words right after the wake word).
Follows docs/android-protocol.md.
"""

import argparse
import asyncio
import json
import math
import struct
import wave
from datetime import datetime
from pathlib import Path

import websockets

RATE_IN = 16000


def load_voice():
    try:
        import sherpa_onnx
    except ImportError:
        return None
    for base in (Path.home() / ".local/share/orpheus/models", Path.home() / ".cache/orpheus-usb/models"):
        for d in sorted(base.glob("vits-piper-ru*")) if base.exists() else []:
            model = next(d.glob("*.onnx"), None)
            if model:
                return sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
                    model=sherpa_onnx.OfflineTtsModelConfig(vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                        model=str(model), tokens=str(d / "tokens.txt"), data_dir=str(d / "espeak-ng-data")))))
    return None


VOICE = None


def speak(text):
    """-> (sample_rate, 16-bit PCM bytes)"""
    if VOICE is not None:
        audio = VOICE.generate(text, sid=0, speed=1.0)
        pcm = b"".join(struct.pack("<h", max(-32768, min(32767, int(s * 32767)))) for s in audio.samples)
        return audio.sample_rate, pcm
    rate = 22050
    tone = [int(8000 * math.sin(2 * math.pi * 440 * i / rate) * min(1, (rate - i) / 800)) for i in range(rate)]
    return rate, struct.pack("<%dh" % len(tone), *tone)


def words_for_seconds(sec):
    whole = round(sec, 1)
    return f"{whole}".replace(".", ",") + " секунды"


async def session(ws, args):
    if args.token:
        auth = ws.request.headers.get("Authorization", "")
        if auth != f"Bearer {args.token}":
            await ws.send(json.dumps({"type": "error", "message": "неверный токен"}, ensure_ascii=False))
            await ws.close()
            return
    peer = ws.remote_address[0]
    print(f"[{peer}] подключился")
    audio = bytearray()
    listening = False
    try:
        async for message in ws:
            if isinstance(message, bytes):
                if listening:
                    audio += message
                continue
            msg = json.loads(message)
            kind = msg.get("type")
            if kind == "hello":
                print(f"[{peer}] устройство: {msg.get('device')}")
            elif kind == "start":
                audio.clear()
                listening = True
                print(f"[{peer}] слушаю (follow_up={msg.get('follow_up')})")
            elif kind == "cancel":
                listening = False
                print(f"[{peer}] отменено")
            elif kind == "stop":
                listening = False
                sec = len(audio) / 2 / RATE_IN
                print(f"[{peer}] фраза {sec:.1f} с")
                if args.save:
                    args.save.mkdir(parents=True, exist_ok=True)
                    path = args.save / datetime.now().strftime("phrase-%H%M%S.wav")
                    with wave.open(str(path), "wb") as w:
                        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE_IN); w.writeframes(bytes(audio))
                    print(f"[{peer}] сохранено: {path}")
                await ws.send(json.dumps({"type": "transcript", "text": f"(мок) фраза на {sec:.1f} с"}, ensure_ascii=False))
                reply = f"Я тебя слышу. Твоя фраза длилась {words_for_seconds(sec)}."
                await asyncio.sleep(0.3)  # "thinking"
                await ws.send(json.dumps({"type": "reply", "text": reply}, ensure_ascii=False))
                rate, pcm = await asyncio.to_thread(speak, reply)
                await ws.send(json.dumps({"type": "audio", "sample_rate": rate}))
                chunk = rate // 5 * 2  # 200 ms
                for i in range(0, len(pcm), chunk):
                    await ws.send(pcm[i:i + chunk])
                await ws.send(json.dumps({"type": "audio_end", "expect_reply": False}))
    except websockets.ConnectionClosed:
        pass
    print(f"[{peer}] отключился")


async def main():
    global VOICE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--token", default="")
    ap.add_argument("--save", type=Path)
    args = ap.parse_args()
    import sys
    sys.stdout.reconfigure(line_buffering=True)
    VOICE = load_voice()
    print("голос:", "Piper" if VOICE else "нет (sherpa-onnx или модели не найдены), будет тон")
    async with websockets.serve(lambda ws: session(ws, args), "0.0.0.0", args.port, max_size=None):
        print(f"мок-сервер: ws://0.0.0.0:{args.port}/ws")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
