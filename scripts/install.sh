#!/usr/bin/env bash
# Everything Orpheus needs on a fresh Debian / Ubuntu Server, from the repository root:
#   scripts/install.sh
set -euo pipefail
cd "$(dirname "$0")/.."

sudo apt-get update
sudo apt-get install -y python3-venv python3-dev libportaudio2 alsa-utils curl bzip2 libvulkan1 mesa-vulkan-drivers

[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e .

scripts/download_models.sh
scripts/setup_ollama.sh --vulkan

# the units as in this checkout; settings live in drop-ins (systemctl --user edit ...), which this keeps
mkdir -p ~/.config/systemd/user
for unit in orpheus orpheus-server plannerd; do
  sed "s#@ROOT@#$PWD#g" "systemd/$unit.service" > ~/.config/systemd/user/$unit.service
done
systemctl --user daemon-reload
# Lingering starts user services at boot without logging in; without a login session
# the sound devices are reachable only through the audio group.
sudo loginctl enable-linger "$USER"
sudo usermod -aG audio "$USER"

echo
echo "Готово. Проверка:   .venv/bin/python -m orpheus --stats chat"
echo "Голос:              .venv/bin/python -m orpheus --stats voice"
echo "Автозапуск, голос ноута:      systemctl --user enable --now orpheus"
echo "или сервер для телефона:      systemctl --user enable --now plannerd orpheus-server"
echo "                              (plannerd — из репозитория Planner; для доступа снаружи — scripts/firewall.sh)"
echo "Настройки:          systemctl --user edit orpheus-server   (или orpheus)"
