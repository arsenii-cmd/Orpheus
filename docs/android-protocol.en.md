# Phone ↔ Orpheus server protocol

[Русский](android-protocol.md) · **English**

The phone app is only a transport. By itself it recognises just the wake word «Орфей» and sends the
phrase audio to the server. The server recognises speech (GigaAM), thinks (Ollama) and answers with a
voice (Piper). This page describes everything a server needs to serve the app.
A reference implementation for testing is `android/tools/mock_server.py`.

## Connection

- A single WebSocket connection: `ws://` or `wss://<server>/ws`. The app takes the full URL, so the
  path can be anything.
- The token comes in the `Authorization: Bearer <token>` header. If it's wrong, the server sends
  `error` and closes the connection.
- The connection stays open while Orpheus is on: a ping every 20 s. After a drop the app reconnects
  by itself, with pauses from 1 to 30 s.
- **Text frames** are JSON messages, each with a `type` field.
- **Binary frames** are audio: PCM, 16-bit, little-endian, mono.

## Phone → server

| Message | When |
| --- | --- |
| `{"type":"hello","device":"Pixel 8","version":1,"personal_key":"…"}` | Right after connecting. `personal_key` is the "Personal" key (base64, 32 bytes) if set in the app: the server opens the encrypted personal memory with it |
| `{"type":"mode","personal":true}` / `false` | The "Personal" button in the app: enter or leave the section |
| `{"type":"start","sample_rate":16000,"follow_up":false,"wake_word":"орфей"}` | A phrase has started. Binary 16 kHz frames follow |
| … `"voice":"male"` or `"voice":"female"` in `start` | Optional: which voice to answer with (male — Piper ruslan, female — Piper irina). Without it, the server's default voice (`ORPHEUS_SERVER_VOICE`, male) |
| … `"enroll":true` in `start` | The phrase is not a question but an owner-voice enrollment: the server stores the voice and sends `enroll` instead of an answer, then `audio_end` |
| … `"headset":true` / `"headset":false` in `start` | Optional: the phrase was heard by the earbuds' / the phone's own microphone. The voiceprint adapts only from phrases on the microphone it was enrolled with; without the field, as before |
| … `"id":7` in `start` | Optional: the phrase number. The server echoes it in that phrase's `transcript`, `reply`, `audio`, `audio_end`, `error` and `enroll`; the phone drops messages with another number (an answer interrupted by the button may still be arriving when the next phrase has gone). Without the field, as before |
| … `"strict":true` in `start` | Optional: answer this phrase only for the owner's voice (the app asks for this with earbuds on, "Strict mode with earbuds" toggle — except in a conversation opened by the earbud touch or the button: the owner opened it). Another voice gets `audio_end` with `"reason":"not_owner"`, as with `ORPHEUS_SPEAKER=strict`. Ignored with `ORPHEUS_SPEAKER=off` |
| `{"type":"enroll_reset"}` | Forget the enrolled owner's voice. The server answers `enroll` with `count: 0` |
| binary frames | The phrase audio, as it is recorded. The first frame may be longer: it is what was said right after the wake word |
| `{"type":"stop"}` | The phone considers the phrase finished: 1 s of silence, 15 s at most, or an `end_of_speech` from the server |
| `{"type":"cancel"}` | The phrase is cancelled (nothing was said after «Орфей», or Orpheus was turned off). Don't answer |

`follow_up: true` means the phrase came in the follow-up window, without «Орфей». It may be an
unrelated conversation: the server may answer with an empty `audio_end`.

The audio starts at the end of «Орфей», but the edge can be fuzzy: the phrase sometimes starts with
«фей» or the whole «Орфей». Cut such a start off before handing the recognised text to the model.

## Server → phone

| Message | Meaning |
| --- | --- |
| `{"type":"end_of_speech"}` | Optional: the server's VAD decided the phrase is over. The phone answers `stop` and stops sending audio. The server's VAD is more accurate than the phone's |
| `{"type":"transcript","text":"какая погода"}` | What was recognised. The phone shows it in the history |
| `{"type":"reply","text":"…"}` | The answer text. May come in several pieces that append to each other |
| `{"type":"audio","sample_rate":22050}` | The answer's voice follows: binary frames at this rate |
| binary frames | The answer's voice, may be sent as it is synthesized (sentence by sentence) |
| `{"type":"audio_end","expect_reply":false}` | The answer is over. `"reason":"not_owner"` — not the owner's voice (`strict` mode): there was and will be no answer, the phone just keeps waiting. `expect_reply: true` — Orpheus asked a question, and the phone listens for the answer without the wake word even if follow-up is off in the settings |
| `{"type":"error","message":"…"}` | An error: the phone beeps and shows the text |
| `{"type":"mode","personal":true}` / `false` | The current section: right after `hello` and on every change — by the button, by voice («давай поговорим о личном» / «хватит о личном») or after 10 minutes of silence. The phone shows the "Personal" badge from this message, not from its own button |
| `{"type":"enroll","count":3,"needed":5,"mode":"strict"}` | Owner's voice: how many phrases of those needed are enrolled, the server's mode (`off` — no check, `log` — checks and answers everyone, `strict` — answers only the owner). Right after `hello` and after every enrollment phrase; `"error":"…"` — why a phrase was rejected (e.g. too short) |

An answer without a voice is fine too: `reply`, then `audio_end` right away, without `audio`.
The server must answer within 30 s after `stop`, otherwise the phone assumes there will be no answer.

## One turn

```
phone                                     server
  «Орфей» heard, beep
  start ─────────────────────────────────▶
  PCM, PCM, PCM … ───────────────────────▶ (recognises as it goes)
                   ◀───────────────────── end_of_speech   (optional)
  stop, "got it" beep ───────────────────▶
                   ◀───────────────────── transcript
                   ◀───────────────────── reply …
                   ◀───────────────────── audio {sample_rate}
                   ◀───────────────────── PCM, PCM …
                   ◀───────────────────── audio_end {expect_reply}
  answer played → listens for a follow-up for 6 s (if enabled) → waits again
```

## The "Personal" section

A separate memory for private conversations: facts, notes and conversations are kept in their own
database file on the server, `personal.db.enc`, **encrypted** (AES-256-GCM, `orpheus/secure.py`).
The key is never on the server's disk: it is kept in the app (wrapped with an Android Keystore key)
and arrives in `hello`; the server holds it only in memory. Without a key "Personal" is closed: a voice
command gets «Личное закрыто…», a `mode` message gets `error` and `mode:false`. A wrong key gets the
`error` «Ключ «Личного» не подходит», and the file is left untouched. A new key:
`python -c "from orpheus.secure import new_key; print(new_key())"` — keep a copy somewhere safe:
without the key the personal memory can't be opened.

Entering and leaving happen by voice or by a `mode` message; the server switches the section itself
and reports every change. The text of personal phrases and answers is never written to the server
log — only the time.

## Owner's voice

The server compares the voice of every phrase with the owner's (`orpheus/voiceprint.py`, the CAM++
model). The voice is enrolled once from the app: "Settings → My voice", five phrases read aloud, each
sent as a regular phrase with `"enroll": true`. Until a voice is enrolled, or with the server in `off`
mode, everyone is answered. The mode and threshold are set on the server (`ORPHEUS_SPEAKER`,
`ORPHEUS_SPEAKER_THRESHOLD`); the text of someone else's phrase goes neither to the log nor to memory.
