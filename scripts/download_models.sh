#!/usr/bin/env bash
# Download the speech models into ${ORPHEUS_MODELS:-~/.local/share/orpheus/models}.
# Each file lands under its final name only when whole: a download cut short (Ctrl-C, the network)
# is picked up on the next run instead of being taken for a model.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"  # before the cd below

DIR="${ORPHEUS_MODELS:-$HOME/.local/share/orpheus/models}"
# a male and a female voice (the server lets the phone choose); also available: dmitri, denis.
# A Vosk voice is written with its speaker ("vosk-model-tts-ru-0.7-multi:3"): only the name is a model
VOICES="${ORPHEUS_VOICE:-vits-piper-ru_RU-ruslan-medium} ${ORPHEUS_VOICE_FEMALE:-vits-piper-ru_RU-irina-medium}"
BASE=https://github.com/k2-fsa/sherpa-onnx/releases/download
ASR=sherpa-onnx-nemo-transducer-punct-giga-am-v3-russian-2025-12-16

mkdir -p "$DIR"
cd "$DIR"

get() {  # get <url> <file>: to <file>.part (resumed), then renamed
  curl -fL --retry 10 --retry-all-errors -C - -o "$2.part" "$1"
  mv "$2.part" "$2"
}

fetch() {  # fetch <release> <archive name without .tar.bz2>: unpacked beside, then moved into place whole
  echo "$2..."
  [ -f "$2.tar.bz2" ] || get "$BASE/$1/$2.tar.bz2" "$2.tar.bz2"
  rm -rf "$2.unpack" && mkdir "$2.unpack"
  # tar needs the bzip2 program, which a minimal server may lack; Python has bz2 built in
  python3 -c "import sys, tarfile; tarfile.open(sys.argv[1]).extractall(sys.argv[2], filter=\"data\")" "$2.tar.bz2" "$2.unpack"
  rm -rf "$2"
  mv "$2.unpack/$2" "$2"
  rm -rf "$2.unpack" "$2.tar.bz2"
}

[ -f "$ASR/encoder.int8.onnx" ] || fetch asr-models "$ASR"
for VOICE in $VOICES; do
  VOICE="${VOICE%%:*}"  # "vosk-model-tts-ru-0.7-multi:3" -> the model's name
  case "$VOICE" in
    vosk-model-tts-*)
      # not a sherpa-onnx release: from alphacephei.com/vosk/models, unpacked here by hand
      [ -d "$VOICE" ] || echo "голос Vosk $VOICE: не скачивается этим скриптом, положи модель в $DIR/$VOICE" ;;
    *) [ -f "$VOICE/tokens.txt" ] || fetch tts-models "$VOICE" ;;
  esac
done

# whose voice it is (orpheus/voiceprint.py): 3D-Speaker CAM++, 28 MB
SPEAKER=3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx
if [ ! -f "$SPEAKER" ]; then
  echo "модель голоса владельца..."
  get "$BASE/speaker-recongition-models/$SPEAKER" "$SPEAKER"
fi

if [ ! -f silero_vad.onnx ]; then
  echo "silero_vad..."
  get "$BASE/asr-models/silero_vad.onnx" silero_vad.onnx
fi

# the model for searching the memory by meaning (orpheus/vectors.py); needs the Python with fastembed —
# without it, or without the internet (the USB stick's first boot), the server gets it itself later:
# not a reason to stop the rest of the install
PY="${ORPHEUS_PYTHON:-$ROOT/.venv/bin/python}"
if [ -x "$PY" ] && "$PY" -c "import fastembed" 2>/dev/null; then
  echo "модель поиска по смыслу..."
  "$PY" -c "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[2]); from orpheus.vectors import Embedder; Embedder(cache_dir=Path(sys.argv[1]) / 'fastembed').embed(['проверка'])" \
    "$DIR" "$ROOT" 2>/dev/null || echo "модель поиска по смыслу не скачалась (нет интернета?): сервер скачает её сам"
fi

echo "готово: $DIR"
