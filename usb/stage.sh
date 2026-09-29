#!/usr/bin/env bash
# Runs inside the freshly installed system (curtin in-target) at the end of the autoinstall.
# Unpacks Orpheus into the user's home and arms the first-boot setup.
#   stage.sh <time zone> [language model]
set -euo pipefail

TZ_NAME="$1"
MODEL_NAME="${2:-}"  # the one the stick carries (make_usb.sh ORPHEUS_MODEL); empty: the default
# The user is created on the installer's identity screen; the first one always gets uid 1000.
USER_NAME="$(getent passwd 1000 | cut -d: -f1)"
[ -n "$USER_NAME" ] || { echo "orpheus: no user with uid 1000"; exit 1; }
PAYLOAD=/opt/orpheus-payload
HOME_DIR="/home/$USER_NAME"

mkdir -p "$HOME_DIR/Orpheus"
tar -xf "$PAYLOAD/orpheus.tar" -C "$HOME_DIR/Orpheus"
chown -R "$USER_NAME:$USER_NAME" "$HOME_DIR/Orpheus"

# Speech models from the stick go where Orpheus looks for them (config.py: ~/.local/share/orpheus/models).
if [ -d "$PAYLOAD/models" ]; then
  mkdir -p "$HOME_DIR/.local/share/orpheus"
  mv "$PAYLOAD/models" "$HOME_DIR/.local/share/orpheus/models"
  chown -R "$USER_NAME:$USER_NAME" "$HOME_DIR/.local"
fi

# Moved from a working laptop (make_usb.sh with ORPHEUS_FROM): its programs, services, settings, secrets
# and data go into this home as they were there, its home's path in them replaced with this one's.
MOVED=0
if [ -f "$PAYLOAD/home.tar" ]; then
  tar -xpf "$PAYLOAD/home.tar" -C "$HOME_DIR"
  shred -u "$PAYLOAD/home.tar" 2>/dev/null || rm -f "$PAYLOAD/home.tar"
  OLD_HOME="$(cat "$HOME_DIR/.orpheus-from-home")"
  rm -f "$HOME_DIR/.orpheus-from-home"
  if [ "$OLD_HOME" != "$HOME_DIR" ]; then
    grep -rlF "$OLD_HOME" "$HOME_DIR/.config/systemd/user" | xargs -r sed -i "s#$OLD_HOME#$HOME_DIR#g"
    # the enabled services are links to where the units were
    find "$HOME_DIR/.config/systemd/user" -type l | while read -r link; do
      ln -sfn "$(readlink "$link" | sed "s#^$OLD_HOME#$HOME_DIR#")" "$link"
    done
  fi
  chown -R "$USER_NAME:$USER_NAME" "$HOME_DIR/.config" "$HOME_DIR/.local"
  MOVED=1
fi

printf 'USER_NAME=%s\nTZ_NAME=%s\nMOVED=%s\nMODEL_NAME=%s\n' "$USER_NAME" "$TZ_NAME" "$MOVED" "$MODEL_NAME" > /etc/orpheus-firstboot.conf
sed "s|@ROOT@|$HOME_DIR/Orpheus|g" "$HOME_DIR/Orpheus/usb/orpheus-firstboot.service" > /etc/systemd/system/orpheus-firstboot.service
systemctl enable orpheus-firstboot.service

cat > /etc/motd <<'MOTD'

  Орфей настраивается при первом запуске: пакеты, модели, Ollama (5–30 минут).
  Ход установки:   journalctl -u orpheus-firstboot -f
  Когда закончит, ноут сам перезагрузится.

MOTD
