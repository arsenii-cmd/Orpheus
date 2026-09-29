#!/usr/bin/env bash
# First boot of a system installed from the Orpheus USB stick: packages, Python, models, Ollama.
# When the stick carried them (/opt/orpheus-payload/offline) nothing is downloaded and no internet
# is needed; otherwise they come from the network. Runs as root from orpheus-firstboot.service;
# if it fails, it runs again on the next boot.
set -euo pipefail
. /etc/orpheus-firstboot.conf
export HOME=/root  # a system service has no $HOME, and the ollama CLI panics without it

ROOT="/home/$USER_NAME/Orpheus"
OFF=/opt/orpheus-payload/offline
cd "$ROOT"
as_user() { runuser -u "$USER_NAME" -- env HOME="/home/$USER_NAME" "$@"; }
export DEBIAN_FRONTEND=noninteractive

if [ -d "$OFF" ]; then
  echo "== системные пакеты (с флешки)"
  # dpkg, not apt: with the archive's package lists on board, apt would rather download
  # the same versions than use these files; the set is complete, dependencies included
  dpkg -i "$OFF"/debs/*.deb
else
  echo "== жду интернет"
  for i in $(seq 120); do
    curl -fsI -m 10 https://github.com >/dev/null && break
    [ "$i" = 120 ] && { echo "нет интернета, попробую при следующей загрузке"; exit 1; }
    sleep 5
  done

  echo "== системные пакеты"
  for i in $(seq 30); do  # the first boot's unattended upgrades may be holding apt
    apt-get -o DPkg::Lock::Timeout=900 update && break
    [ "$i" = 30 ] && exit 1
    sleep 20
  done
  apt-get -o DPkg::Lock::Timeout=900 install -y python3-venv python3-dev libportaudio2 alsa-utils curl bzip2 git libvulkan1 mesa-vulkan-drivers
fi

echo "== ноут как сервер"
bash scripts/laptop.sh "$TZ_NAME"

echo "== python и модели речи"
as_user python3 -m venv .venv
if [ -d "$OFF" ]; then
  as_user .venv/bin/pip install -q --no-index --find-links "$OFF/wheels" -e .
else
  as_user .venv/bin/pip install -q --upgrade pip
  as_user .venv/bin/pip install -q -e .
fi
as_user scripts/download_models.sh  # models brought on the stick are already in place

echo "== ollama и языковая модель"
if [ -d "$OFF" ]; then
  export ORPHEUS_OLLAMA_TGZ="$OFF/ollama.tgz"
fi
if [ "${MOVED:-0}" = 1 ]; then  # the model the moved server runs (its settings came along)
  ORPHEUS_MODEL="$(sed -n 's/^Environment=ORPHEUS_MODEL=//p' /home/"$USER_NAME"/.config/systemd/user/orpheus-server.service.d/*.conf | tail -1)"
  export ORPHEUS_MODEL
elif [ -n "${MODEL_NAME:-}" ]; then  # the one the stick was built with (make_usb.sh ORPHEUS_MODEL)
  export ORPHEUS_MODEL="$MODEL_NAME"
fi
ORPHEUS_OLLAMA_SEED=/opt/orpheus-payload/ollama bash scripts/setup_ollama.sh --vulkan

echo "== автозапуск орфея"
UNIT_DIR="/home/$USER_NAME/.config/systemd/user"
if [ "${MOVED:-0}" = 1 ]; then
  # the moved laptop's services came enabled as they were (the phone's server, plannerd, sing-box);
  # nothing comes in but SSH and the phone's server from home and through the tunnel
  bash scripts/firewall.sh
else
  mkdir -p "$UNIT_DIR/default.target.wants"
  sed "s#@ROOT@#$ROOT#g" systemd/orpheus.service > "$UNIT_DIR/orpheus.service"
  if [ -n "${ORPHEUS_MODEL:-}" ]; then  # Orpheus asks Ollama for the model set up above (a drop-in, as settings go)
    mkdir -p "$UNIT_DIR/orpheus.service.d"
    printf '[Service]\nEnvironment=ORPHEUS_MODEL=%s\n' "$ORPHEUS_MODEL" > "$UNIT_DIR/orpheus.service.d/model.conf"
  fi
  ln -sf ../orpheus.service "$UNIT_DIR/default.target.wants/orpheus.service"
fi
chown -R "$USER_NAME:$USER_NAME" "/home/$USER_NAME/.config"
usermod -aG audio "$USER_NAME"
loginctl enable-linger "$USER_NAME"

echo "== готово, перезагрузка"
systemctl disable orpheus-firstboot.service
rm -rf /opt/orpheus-payload
rm -f /etc/motd
cat > /etc/motd <<'MOTD'

  Орфей установлен.  Лог: journalctl --user -u orpheus -f     Чат: cd ~/Orpheus && .venv/bin/python -m orpheus chat

MOTD
systemctl reboot
