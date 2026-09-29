# Orpheus

[Русский](README.md) · **English**

Orpheus is a Russian-speaking voice assistant that runs entirely at home: on an old laptop with no
discrete GPU, no cloud and no subscriptions. Speech recognition, the language model, memory and speech
synthesis are all local, on the CPU. An Android phone serves as its ears and voice: call it with the
word «Орфей» or a touch on your earbuds.

```
microphone → Silero VAD → GigaAM v3 → Ollama (Qwen3 4B) → Piper → speaker
              phrases       text       answer + memory     voice
```

> Orpheus speaks and understands **Russian**; the phrase examples below are given in Russian with a
> translation.

## Features

- **Fully offline.** The internet is needed only for weather, search and exchange rates — and only to
  five known services, through a proxy of your choice.
- **Fast on weak hardware.** Everyday commands are handled by the program itself, without the model:
  0.6–1 s from the end of a phrase to the first sound of the answer on a Ryzen 5 3500U. Everything
  else is answered by the language model.
- **Planner, notes, memory.** Works with [Planner](https://github.com/arsenii-cmd/Planner): events,
  tasks and notes added by voice show up on the phone and the desktop.
- **Only your voice.** An owner voiceprint: the TV, guests and someone else's «Орфей» get no answer.
- **"Personal".** A separate encrypted memory for private conversations; the key lives only on the phone.
- **Android app.** A background service, an on-device wake word (Vosk), earbud touch-and-hold via the
  system "digital assistant", Bluetooth earbud microphone.
- **USB installer.** One script builds an Ubuntu Server image that installs the system together with
  Orpheus and its models, no internet required.

## Components

- **Speech recognition:** [GigaAM v3](https://github.com/salute-developers/GigaAM) RNNT with punctuation
  (int8) via [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx).
- **Brain:** [Ollama](https://ollama.com), by default `huihui_ai/qwen3-abliterated:4b-instruct-2507-q4_K_M`
  (Qwen3 4B, ~2.5 GB). Gemma 4 E4B is tested too.
- **Memory:** SQLite — facts, notes and conversations, keyword and semantic search.
- **Voice:** Piper (`ru_RU-ruslan`, also `dmitri`, `denis`, `irina`) via sherpa-onnx, or Vosk TTS
  (`vosk-model-tts-ru-0.7-multi`).
- **Owner's voice:** CAM++ (3D-Speaker) via sherpa-onnx.

## Installation

The easiest way is a USB stick that installs Ubuntu Server together with Orpheus:
`scripts/make_usb.sh /dev/sdX`, details in [docs/usb.en.md](docs/usb.en.md).

Or by hand on a clean Ubuntu Server 24.04 or Debian 13 without a desktop
(installing the system: [docs/install-ubuntu-server.en.md](docs/install-ubuntu-server.en.md)).
From the repository root:

```sh
sudo scripts/laptop.sh Europe/Moscow   # lid, zram, time zone, Cyrillic in the console
scripts/install.sh                     # Ollama on the integrated GPU (Vulkan) right away
```

The script installs system packages, creates `.venv`, downloads the speech models (~300 MB),
installs and tunes Ollama, downloads the language model (~2.5 GB) and installs the systemd
services. Log in again afterwards (the `audio` group was added).

## Running

```sh
. .venv/bin/activate
python -m orpheus --stats chat     # text — check the brain and memory
python -m orpheus --stats voice    # voice, with the laptop's microphone
python -m orpheus server           # server for the phone (WebSocket, port 8765)
python -m orpheus listen           # speech recognition only
python -m orpheus memory           # what Orpheus remembers  (--forget ID to forget)
python -m orpheus notes [words]    # notes / search in them

systemctl --user enable --now orpheus                   # voice mode at boot
systemctl --user enable --now plannerd orpheus-server   # or the server for the phone
journalctl --user -u orpheus-server -f                  # log
```

`--stats` prints how long recognition took, when the first word of the answer arrived and how many
prompt tokens the model had to read.

The chat understands `/mem`, `/notes`, `/reset` (new conversation), `/quit`.

## What it can do

Everyday commands are handled by the program itself, without the language model: instantly and
without making things up. From the end of a phrase to the first sound of the answer takes 0.6–1 s,
of which ~0.3 s is recognition and ~0.4 s synthesis. The model (2.5–6 s) answers everything else:
questions, conversation, whatever matches no scenario.

| Say | What happens |
| --- | --- |
| «Который час?», «Какое сегодня число?» (What time is it? What's the date?) | time and date |
| «Сколько дней до Нового года?» (How many days until New Year?) | counting days, hours, minutes |
| «Сколько будет 15 умножить на 37?», «20% от 3 000» | calculator |
| «Сделай заметку: купить молоко» (Make a note: buy milk) | a note in Planner, visible on the phone and desktop |
| «Прочитай мои заметки», «Найди заметку про вайфай» (Read my notes; find the Wi-Fi note) | notes |
| «Запомни, что я люблю кофе без сахара» (Remember that I take coffee without sugar) | facts about you |
| «Что у меня завтра?», «Что у меня дальше?» (What's on tomorrow? What's next?) | planner |
| «Когда следующая тренировка?» (When is the next workout?) | an answer from the planner |
| «Если я свободен завтра в 19, запиши кино» (If I'm free tomorrow at 7 pm, add a movie) | added only if the slot is free; otherwise what's there and "Add anyway?" |
| «Напомни через 2 часа выпить таблетку», «Тренировка по понедельникам, средам и пятницам в 6 утра» | a new event, task or recurring event |
| «Перенеси физику на завтра в 15», «Отмени завтра в 10 русский» → «Да» | editing plans |
| «Перенеси его на 14», «Нет, лучше на 15» (Move it to 2 pm; no, better 3 pm) | whatever was just discussed |
| «Удали все дела на завтра» → «Да» | clears a day after a confirmation with the list |
| «Верни» (Undo) | undoes the last deletion, addition or check-off |
| «Какая погода на выходных?», «Будет ли дождь?», «Как одеться?» | weather (Open-Meteo), by default for `ORPHEUS_CITY`; a 7-day forecast refreshed every 6 hours |
| «Кто такой Никола Тесла?» (Who was Nikola Tesla?) | the opening of the Wikipedia article |
| «Найди в интернете …», «Какие новости?» (Search the web…; what's the news?) | "Let me look", then a short model answer from DuckDuckGo (or your own SearXNG) results |
| «Какой курс доллара?», «Сколько будет 100 долларов в рублях?» | the official Bank of Russia rate |
| «А завтра?», «А в Сочи?» after weather or plans (And tomorrow? And in Sochi?) | the same question for another day or city |
| «Что у меня завтра и какая будет погода?» (What's on tomorrow and what's the weather?) | two requests in one phrase — both |
| «Включи музыку», «Поставь таймер» (Play music; set a timer) | an honest "I can't do that yet" |

Orpheus goes online only to these services (Open-Meteo and its city search, Wikipedia, DuckDuckGo,
the Bank of Russia), through the `ORPHEUS_PROXY` proxy if one is set; it never searches in
"Personal". Deleting always asks for confirmation, and "no" means no.

Scenarios are phrase templates in [`orpheus/intents_ru.txt`](orpheus/intents_ru.txt), with Home
Assistant–style syntax: `(a|b)` — one of, `[a]` — optional, `{slot}` — any words. Teaching Orpheus a
new phrase means adding a template; a new scenario is a section in that file plus a `do_<name>` method
in [`orpheus/skills.py`](orpheus/skills.py).

## How it stays fast

On such a CPU the model **generates** 5–10 tokens/s (enough to speak aloud) but **reads** the prompt
at only ~30–60 tokens/s. Re-reading the system prompt, memory and history (~1500 tokens) on every
phrase would delay the answer by 30+ seconds. So:

0. **No model where possible.** Time, date, arithmetic, notes, planner, memory are program scenarios:
   a fraction of a millisecond to parse a phrase. Their replies don't pile up in the model's history:
   it gets only the last exchange, otherwise its next reply would re-read them all (20–30 s).
1. **The model stays loaded.** `OLLAMA_KEEP_ALIVE=-1`, warm-up when Orpheus starts.
2. **Prefix cache.** Ollama keeps the KV cache of the previous request and recomputes only what
   differs. Orpheus lays out the prompt so that only its end changes from turn to turn:
   - the system prompt doesn't change during a conversation — the time and date go at the start of
     the user's turn instead;
   - a new fact isn't inserted into the system prompt right away (that would drop the whole cache):
     it is already in the history as a `remember` call; memory is re-read when a new conversation starts;
   - history is trimmed rarely and in one large chunk, not a turn at a time.
3. **One slot.** `OLLAMA_NUM_PARALLEL=1` — the whole cache belongs to Orpheus.
4. **Fixed `num_ctx`.** A different context size makes Ollama reload the model.
5. **Streaming.** The first sentence is spoken while the second is being written.
6. **A smaller cache.** `OLLAMA_FLASH_ATTENTION=1` + `OLLAMA_KV_CACHE_TYPE=q8_0`.

Measured on a Ryzen 5 3500U, 8 GB (Qwen3 4B, Q4_K_M), `python scripts/bench_llm.py`:

| | CPU | Vega 8 (Vulkan) |
| --- | --- | --- |
| Prompt reading | ~15 tok/s | ~70 tok/s |
| Generation | ~7 tok/s | ~9 tok/s |
| First word of a typical reply | 4–5 s | 1.5–2.5 s |
| Cold start (after a reboot) | ~60 s | ~10 s |

That is why the installer enables the integrated GPU right away (`scripts/setup_ollama.sh --vulkan`,
the `mesa-vulkan-drivers` driver). `ollama ps` shows where the model runs (the PROCESSOR column should
say `100% GPU`).

## Only the owner's voice

Orpheus can answer you alone: it stays silent for the TV, guests and someone else's «Орфей».
The server compares the voice of every phrase with the enrolled owner's voice (`orpheus/voiceprint.py`:
the CAM++ model, 28 MB, ~35 ms per phrase, in parallel with recognition, so the answer isn't delayed).

1. In the app: "Settings → My voice", read five phrases aloud.
   Or on the server from recordings: `python -m orpheus voiceprint enroll a.wav b.wav …`.
2. Live a few days in `log` mode (the default): it answers everyone and logs a score for every phrase.
3. Set the threshold (`ORPHEUS_SPEAKER_THRESHOLD`) between your scores and others'. On synthetic
   voices the owner scored 0.56–0.82 (noise included), others no more than 0.47.
4. `ORPHEUS_SPEAKER=strict` — and Orpheus answers only you. The app can turn strict mode on only
   while you wear earbuds.

Short phrases ("yes", "stop") aren't checked: a second of speech is not enough to judge. The voiceprint
adapts a little by itself (morning voice, a cold, another room) but never drifts far from the
enrollment. This keeps other voices out; it is not a lock: a recording of your voice will pass.

`python -m orpheus voiceprint` — what is enrolled; `… score a.wav` — score a recording; `… reset` — forget.

## Testing

- `python -m pytest` — unit tests (`pip install pytest`).
- `scripts/bench_speech.py` — 270 phrases in 68 dialogs spoken "as in real life" (slips,
  self-corrections, colloquialisms), checking what actually ended up in the planner.
- `scripts/bench_tools.py`, `scripts/bench_dialogs.py` — every scenario and whole conversations with
  the real model.
- `scripts/bench_voice.py` — end to end: synthesized voice → recognition → answer.
- `scripts/bench_stt.py samples/` — recognition accuracy on your own voice (WER, RTF).

## Settings

Environment variables; for a service, as `Environment=` lines in its drop-in:
`systemctl --user edit orpheus-server` (or `orpheus` for voice mode). Drop-ins survive reinstalling the
units with `scripts/install.sh`.

| Variable | Default | What it does |
| --- | --- | --- |
| `ORPHEUS_MODEL` | `huihui_ai/qwen3-abliterated:4b-instruct-2507-q4_K_M` | Ollama model |
| `ORPHEUS_CTX` | `4096` | Context size (same as in `setup_ollama.sh`) |
| `ORPHEUS_TEMPERATURE` | `0.6`, Gemma `0.2` | Creativity of answers |
| `ORPHEUS_LLM_THREADS` | auto | Model threads |
| `ORPHEUS_THINK` | — | `0`/`1` only for hybrid models such as `qwen3:4b` and Gemma 4 (`0` is better) |
| `ORPHEUS_BATCH` | `128` | Batch size for prompt reading |
| `ORPHEUS_IDLE_RESET` | `600` | Seconds of silence before a new conversation starts |
| `ORPHEUS_WAKE` | — | Wake word for voice mode, e.g. `орфей`; without it every phrase is answered |
| `ORPHEUS_SPEAKER` | `log` | Owner's voice: `off` — don't check; `log` — check and log the score; `strict` — answer the owner only |
| `ORPHEUS_SPEAKER_THRESHOLD` | `0.5` | Similarity threshold for the owner's voice (0–1) |
| `ORPHEUS_FOLLOW_UP` | `8` | Seconds after an answer when you can go on without the wake word |
| `ORPHEUS_VOICE` | `vits-piper-ru_RU-ruslan-medium` | Voice: a Piper folder or a Vosk TTS model with a speaker number, e.g. `vosk-model-tts-ru-0.7-multi:3` |
| `ORPHEUS_SPEED` | `1.0` | Speech rate (higher is faster) |
| `ORPHEUS_TTS_THREADS` | `1` | Synthesis threads (the other cores go to the model) |
| `ORPHEUS_MIC` | system default | Microphone number or name (`python -m sounddevice` lists them) |
| `ORPHEUS_SILENCE` | `0.6` | Pause that ends a phrase |
| `ORPHEUS_VAD_THRESHOLD` | `0.5` | VAD sensitivity; higher reacts less to noise |
| `ORPHEUS_MIN_SPEECH` / `ORPHEUS_MAX_SPEECH` | `0.25` / `20` | Phrase length limits, s |
| `ORPHEUS_THREADS` | number of cores | Recognition threads |
| `ORPHEUS_DB` | `~/.local/share/orpheus/orpheus.db` | Memory and notes database |
| `ORPHEUS_MODELS` | `~/.local/share/orpheus/models` | Speech models |
| `ORPHEUS_OLLAMA` | `http://127.0.0.1:11434` | Ollama address |
| `ORPHEUS_PROXY` | — | Proxy for weather and search; empty — direct, `-` — no internet |
| `ORPHEUS_CITY` | `Москва` | Weather city when none is named |
| `ORPHEUS_SEARXNG` | — | Your own SearXNG (`http://127.0.0.1:8888`): searched first, DuckDuckGo as a fallback |
| `ORPHEUS_ALLOW` | local network | Addresses the server accepts the phone from |
| `ORPHEUS_TOKEN_FILE` | `~/.config/orpheus/voice-token` | Token the phone must send (if the file exists) |

If phrases get cut off in the middle, raise `ORPHEUS_SILENCE` to 0.8–1.0.
If it reacts to a fan, raise `ORPHEUS_VAD_THRESHOLD`.

## Code layout

| File | What it does |
| --- | --- |
| `orpheus/__main__.py` | Commands `voice`, `server`, `chat`, `listen`, `memory`, `notes`, `voiceprint`, `warmup` |
| `orpheus/server.py` | Server for the phone (WebSocket), [protocol](docs/android-protocol.en.md) |
| `orpheus/brain.py` | System prompt, model tools, history, cache-friendly layout |
| `orpheus/intents.py`, `orpheus/intents_ru.txt` | Phrase templates the program handles itself |
| `orpheus/skills.py` | Program scenarios: what to do with each phrase |
| `orpheus/planner.py` | Planner (events, tasks, notes) through its local API |
| `orpheus/calc.py`, `orpheus/numbers.py` | Calculator; numbers, times and dates in words for speech |
| `orpheus/memory.py`, `orpheus/vectors.py` | SQLite: facts, notes, conversations; keyword and semantic search |
| `orpheus/secure.py` | Encryption of "Personal" (the key is only on the phone) |
| `orpheus/voiceprint.py` | Owner's voice |
| `orpheus/weather.py`, `orpheus/web.py`, `orpheus/search.py` | Weather, Wikipedia, search, exchange rates |
| `orpheus/llm.py` | Dependency-free streaming Ollama client |
| `orpheus/stt.py`, `orpheus/audio.py` | Speech recognition (GigaAM v3), microphone and Silero VAD |
| `orpheus/speech.py` | Speech synthesis and splitting the answer into sentences |
| `orpheus/config.py` | All settings |
| `scripts/` | Installation, model downloads, benchmarks |
| `android/` | The phone app ([android/README.en.md](android/README.en.md)) |

## Next

- Timers and reminders right on the phone (today they go through Planner events).
- A morning briefing.
- A text bot — the same brain and memory, in a messenger.

## License

[MIT](LICENSE). The app's fonts are under the OFL, see [android/licenses](android/licenses).
