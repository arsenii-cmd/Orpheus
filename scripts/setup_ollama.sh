#!/usr/bin/env bash
# Install Ollama and tune it for the lowest latency on a small CPU-only machine.
#   scripts/setup_ollama.sh            CPU only (recommended to start with)
#   scripts/setup_ollama.sh --vulkan   the Vega iGPU through Vulkan: on the Ryzen 5 3500U prompt reading
#                                      goes from ~15 to ~70 tokens/s (needs libvulkan1 mesa-vulkan-drivers)
# From the USB stick, with no internet: ORPHEUS_OLLAMA_TGZ installs Ollama from that archive,
# ORPHEUS_OLLAMA_SEED provides the models (see below).
set -euo pipefail

MODEL="${ORPHEUS_MODEL:-huihui_ai/qwen3-abliterated:4b-instruct-2507-q4_K_M}"
CTX="${ORPHEUS_CTX:-4096}"
VULKAN=""
if [ "${1:-}" = "--vulkan" ]; then
  VULKAN='Environment="OLLAMA_VULKAN=1"
Environment="OLLAMA_IGPU_ENABLE=1"'
fi

if [ -n "${ORPHEUS_OLLAMA_TGZ:-}" ] && ! command -v ollama >/dev/null; then
  # Offline, from the archive the USB stick carries: the same layout and service as install.sh
  sudo tar -xzf "$ORPHEUS_OLLAMA_TGZ" -C /usr/local
  id ollama >/dev/null 2>&1 || sudo useradd -r -s /bin/false -U -m -d /usr/share/ollama ollama
  for group in render video; do getent group $group >/dev/null && sudo usermod -aG $group ollama; done
  sudo tee /etc/systemd/system/ollama.service >/dev/null <<UNIT
[Unit]
Description=Ollama Service
After=network-online.target

[Service]
ExecStart=/usr/local/bin/ollama serve
User=ollama
Group=ollama
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
UNIT
elif ! command -v ollama >/dev/null; then
  curl -fsSL https://ollama.com/install.sh | sh
fi

# KEEP_ALIVE=-1        the model never leaves RAM: no multi-second reload after a pause
# NUM_PARALLEL=1       one slot: the whole context and its KV cache belong to Orpheus,
#                      so every request reuses the prefix computed by the previous one
# MAX_LOADED_MODELS=1  never two models in 8 GB
# FLASH_ATTENTION + KV_CACHE_TYPE=q8_0: half the memory for the KV cache
# CONTEXT_LENGTH       the same num_ctx Orpheus sends, so nothing triggers a reload
# HOST=127.0.0.1       reachable only from this machine
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/orpheus.conf >/dev/null <<CONF
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_KEEP_ALIVE=-1"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
Environment="OLLAMA_CONTEXT_LENGTH=$CTX"
$VULKAN
CONF
# the GPU device nodes belong to these groups
[ -n "$VULKAN" ] && sudo usermod -aG render,video ollama
sudo systemctl daemon-reload
sudo systemctl enable ollama
sudo systemctl restart ollama

for _ in $(seq 30); do
  curl -fs http://127.0.0.1:11434/api/version >/dev/null && break
  sleep 1
done

# A model brought along (the USB stick puts one in ORPHEUS_OLLAMA_SEED, in Ollama's own layout)
# is moved into the service's models directory instead of being downloaded.
SEED="${ORPHEUS_OLLAMA_SEED:-}"
if [ -n "$SEED" ] && [ -d "$SEED/manifests" ] && getent passwd ollama >/dev/null; then
  DEST="$(getent passwd ollama | cut -d: -f6)/.ollama/models"
  sudo mkdir -p "$DEST/blobs" "$DEST/manifests"
  sudo find "$SEED/blobs" -type f -name 'sha256-*' -exec mv -t "$DEST/blobs/" {} +
  sudo cp -r "$SEED/manifests/." "$DEST/manifests/"
  sudo chown -R ollama:ollama "$DEST"
fi
# GGUF models brought along (SEED/gguf/<name>/Modelfile) are built by Ollama itself.
for modelfile in "${SEED:-/nonexistent}"/gguf/*/Modelfile; do
  [ -f "$modelfile" ] || continue
  name="$(basename "$(dirname "$modelfile")")"
  name="${name//__//}"  # make_usb.sh: "huihui_ai__qwen3…" is "huihui_ai/qwen3…"
  ollama show "$name" >/dev/null 2>&1 || (cd "$(dirname "$modelfile")" && ollama create "$name" -f Modelfile)
done
ollama show "$MODEL" >/dev/null 2>&1 || ollama pull "$MODEL"
echo "Ollama готова: $MODEL, контекст $CTX"
