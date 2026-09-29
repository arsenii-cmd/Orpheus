#!/usr/bin/env bash
# Put the Vosk Russian model (for the wake word "Орфей") into the app's assets. Run once before building.
set -euo pipefail
cd "$(dirname "$0")/.."
NAME=vosk-model-small-ru-0.22
DEST=app/src/main/assets/vosk-model
[ -f "$DEST/am/final.mdl" ] && { echo "модель уже на месте: $DEST"; exit 0; }
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fL --retry 5 -o "$TMP/model.zip" "https://alphacephei.com/vosk/models/$NAME.zip"
unzip -q "$TMP/model.zip" -d "$TMP"
rm -rf "$DEST"
mkdir -p "$(dirname "$DEST")"
mv "$TMP/$NAME" "$DEST"
# the recognizer never reads these, and they are big
rm -rf "$DEST/rescore" "$DEST/rnnlm"
echo "модель: $DEST ($(du -sh "$DEST" | cut -f1))"
