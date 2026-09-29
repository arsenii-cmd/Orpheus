# Orpheus for Android

[Русский](README.md) · **English**

A smart speaker in your pocket: the app passes a phrase to the Orpheus server and plays back the
answer in its voice. There are two ways to call Orpheus:

- **Touch** (default): touch and hold a Bluetooth earbud. Orpheus is set as the system "digital
  assistant", and the earbud gesture opens a conversation. Between conversations the microphone is
  off: no battery drain, and music in the earbuds keeps its quality.
- **Word**: the app waits for «Орфей» in the background (Vosk, offline, on the phone itself), beeps
  and listens to the phrase. The microphone is always open.

After an answer Orpheus keeps listening for a few seconds for a follow-up without being called again.
Everything except the wake word is done by the server; the protocol is in
[../docs/android-protocol.en.md](../docs/android-protocol.en.md).

## Features

- Works with the app closed and the screen off: a foreground service with a persistent notification
  ("Speak" and "Turn off" buttons).
- **Earbud microphone.** With Bluetooth earbuds connected the app listens through them, so the phone
  can stay in your pocket.
- **Strict mode with earbuds.** With earbuds on, Orpheus answers only the owner's voice; without
  them, everyone. The voice is enrolled in "Settings → My voice": five phrases read aloud.
- **"Personal".** A button opens a section with a separate encrypted memory; its key is kept in the
  Android Keystore and handed to the server only when connecting.
- Speak the command right away, without a pause: «Орфей, какая погода». Everything said after the
  word is taken from a buffer and not lost.
- While Orpheus speaks it doesn't listen for the wake word, so it can't wake itself. Interrupt it with
  the "Speak" button.
- A male or female answer voice. A chat with the conversation history, which can be cleared.

## Interface

| Orb | What is happening |
| --- | --- |
| a dim ember | Off |
| blue, breathing inside a shimmering ring | Waiting to be called |
| light blue, spectrum rays growing with your voice | Listening to a phrase ("started listening" cue: two rising tones) |
| violet, comets on tilted orbits | Phrase sent ("got it" cue: two falling tones), waiting for the answer |
| golden, a lyre appears around it, its strings trembling | Speaking the answer |
| turquoise, rays and a ring | Listening for a follow-up without a call (duration configurable) |

Every change of state flows rather than switches: shape, light, sky colour and caption move on
springs, a shock wave spreads from the orb, the phone vibrates lightly. The answer appears in the
chat word by word as it arrives. With system animations turned off, motion stops while colours and
states remain.

## Building

You need the Android SDK (platform 36) and JDK 17. From this folder:

```sh
scripts/fetch_wake_model.sh     # once: the Vosk model (~45 MB) into assets
./gradlew assembleRelease       # app/build/outputs/apk/release/app-release.apk (~60 MB)
adb install -r app/build/outputs/apk/release/app-release.apk
./gradlew testDebugUnitTest     # logic tests
```

The server address and token can be built in through `local.properties` (it stays out of git):

```properties
orpheus.server=wss://your.domain/ws
orpheus.token=<the server's token>
```

Without them the app asks for the address and token in its settings. Only `arm64-v8a` is built.
The release is signed with the debug key.

## First run

1. "Turn Orpheus on" and allow the microphone and notifications.
2. "Allow unrestricted background use": without it some phones (Xiaomi, Samsung, Huawei) kill the
   service after a while.
3. For touch: "Set as assistant" → Orpheus; in the earbuds' app set "Touch and hold" to "Digital
   assistant" (Galaxy Wearable for Galaxy Buds). If the phone asks what to use on the gesture,
   choose "Orpheus", "Always".
4. For the word: if Orpheus reacts to unrelated words, raise "Word strictness"; if it doesn't
   respond, lower it. On first start the wake-word model takes a few seconds to unpack.

After the phone reboots, Android doesn't let microphone services start on their own, so Orpheus has
to be turned on again.

## Testing without a real server

```sh
pip install websockets numpy sherpa-onnx
python tools/mock_server.py --save /tmp/phrases
```

In the app: "Settings" → address `ws://<this computer's IP>:8765/ws`, empty token.
The mock answers in a Piper voice with how many seconds the phrase lasted. With `--save` it stores
every received phrase as a WAV, so you can hear exactly what the phone sent.

## Layout

| File | What it does |
| --- | --- |
| `AssistantMachine.kt` | States and transitions (waiting → listening → thinking → speaking → follow-up), timeouts. Plain Kotlin, unit-tested |
| `OrpheusService.kt` | The service: microphone, wake word, audio streaming, notification |
| `Assistant.kt` | Digital assistant (VoiceInteractionService) and the earbud call |
| `Headset.kt` | Bluetooth earbud microphone |
| `WakeWord.kt` | Vosk grammar: «орфей» and similar decoy words, picking a word by confidence |
| `Audio.kt` | Level-based VAD, rumble filter, 10 s ring buffer, PCM |
| `ServerLink.kt` / `Protocol.kt` | WebSocket with reconnects, protocol messages |
| `KeyVault.kt` | The "Personal" key in the Android Keystore |
| `Sound.kt` | Answer playback, cues (synthesized, no files) |
| `ui/Orb.kt` | The orb: liquid body, rays, comets, lyre, shock wave. One frame clock, drawing without recomposition |
| `ui/Backdrop.kt` | The sky: an aurora in the state's colour, stars at three depths |
| `ui/Screens.kt` | Main screen, chat, buttons, settings, voice enrollment |
| `ui/Motion.kt` | Shared springs, kinetic text, text shimmer, springy press, staggered entrances |
| `ui/Theme.kt` | State colours, fonts (Unbounded and Manrope, OFL, `res/font`, licenses in `licenses/`) |

### Previewing the UI without a phone

`tools/ui-preview` builds the same `ui/` code with Compose Desktop and plays a scenario
(off → on → listening → thinking → answering → settings) frame by frame. It needs only a JDK and
access to Maven Central, no Android SDK:

```sh
cd tools/ui-preview
gradle render -Pout=/tmp/frames            # 720 PNG frames, 400 × 860 dp
ffmpeg -framerate 30 -i /tmp/frames/f%05d.png -pix_fmt yuv420p orpheus-ui.mp4
```

That is why `ui/` uses only common Compose (foundation, animation, material3) and no Android API:
everything platform-specific (fonts from `R`, animation settings) is passed in from `MainActivity`.
The app types the screen needs are replaced with stubs in the preview
(`tools/ui-preview/src/main/kotlin/dev/arco/orpheus/Stubs.kt`): when `Settings`, `Phase` or `Line`
change, update them there too.
