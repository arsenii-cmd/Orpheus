#!/usr/bin/env bash
# Laptop-as-a-server setup for Ubuntu Server; safe to run again.
#   sudo scripts/laptop.sh [Time/Zone]        default Europe/Moscow
# ORPHEUS_ROOT=/some/dir only writes the config files under that dir and skips the commands (for testing).
set -euo pipefail

TZ_NAME="${1:-Europe/Moscow}"
ROOT="${ORPHEUS_ROOT:-}"

run() {
  if [ -n "$ROOT" ]; then echo "skip: $*"; else "$@"; fi
}

put() {  # put <path> <content>: write only when it differs
  local path="$ROOT$1"
  mkdir -p "$(dirname "$path")"
  if [ ! -f "$path" ] || [ "$(cat "$path")" != "$2" ]; then
    printf '%s\n' "$2" > "$path"
    echo "записан $1"
    return 0
  fi
  return 1
}

set_var() {  # set_var <file> <NAME> <value>: NAME="value" in a shell-style config file
  local path="$ROOT$1"
  touch "$path"
  if grep -q "^$2=\"$3\"$" "$path"; then
    return 1
  elif grep -q "^$2=" "$path"; then
    sed -i "s|^$2=.*|$2=\"$3\"|" "$path"
  else
    printf '%s="%s"\n' "$2" "$3" >> "$path"
  fi
  echo "$1: $2=$3"
}

# 1. Never sleep: not on idle, not on the lid, not on the sleep keys. Only the power button
#    still shuts down (a short press), so there is always a way to turn it off by hand.
if put /etc/systemd/logind.conf.d/orpheus.conf "[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
HandleSuspendKey=ignore
HandleHibernateKey=ignore
IdleAction=ignore"; then
  run systemctl restart systemd-logind
fi
run systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target

# 2. The screen stays on while the lid is open (no console blanking) and goes dark when it
#    is closed: orpheus-lid.service watches the lid and switches the backlight.
mkdir -p "$ROOT/etc/default"
touch "$ROOT/etc/default/grub"
if grep -q "consoleblank=[1-9]" "$ROOT/etc/default/grub"; then
  sed -i 's/consoleblank=[0-9]*/consoleblank=0/' "$ROOT/etc/default/grub"
  echo "grub: consoleblank=0"
  run update-grub
elif ! grep -q "consoleblank=" "$ROOT/etc/default/grub"; then
  if grep -q '^GRUB_CMDLINE_LINUX_DEFAULT=' "$ROOT/etc/default/grub"; then
    sed -i 's/^GRUB_CMDLINE_LINUX_DEFAULT="\(.*\)"/GRUB_CMDLINE_LINUX_DEFAULT="\1 consoleblank=0"/' "$ROOT/etc/default/grub"
  else
    echo 'GRUB_CMDLINE_LINUX_DEFAULT="consoleblank=0"' >> "$ROOT/etc/default/grub"
  fi
  echo "grub: consoleblank=0"
  run update-grub
fi
[ -z "$ROOT" ] && { setterm --blank 0 --powerdown 0 > /dev/tty1 < /dev/tty1 2>/dev/null || true; }
LID_SCRIPT="$(cd "$(dirname "$0")" && pwd)/lid_screen.sh"
if put /etc/systemd/system/orpheus-lid.service "[Unit]
Description=Screen off while the lid is closed

[Service]
ExecStart=/bin/bash $LID_SCRIPT
Restart=always

[Install]
WantedBy=multi-user.target"; then
  run systemctl daemon-reload
  run systemctl enable orpheus-lid.service
fi
run systemctl restart orpheus-lid.service

# 2b. Sound: without a sound server ALSA's default card is card 0, which on many laptops is
#     HDMI, and the microphone only takes 44.1/48 kHz. Default to the first card that can
#     record; "default" then converts the rate (Orpheus records at 16 kHz).
CARD="$(awk -F'[ :]+' '/^card [0-9]+:/ {print $2; exit}' <(LC_ALL=C arecord -l 2>/dev/null))"
if [ -n "$CARD" ]; then
  put /etc/asound.conf "# Orpheus: the card with the microphone, not HDMI
defaults.pcm.card $CARD
defaults.ctl.card $CARD" || true
fi

# 2c. Wi-Fi without deep power saving: with it rtw88 (Realtek RTL8822CE here) drops out now and then,
#     and the phone's requests would find the laptop unreachable. Takes effect after a reboot.
put /etc/modprobe.d/orpheus-wifi.conf "# Orpheus: keep the Wi-Fi awake (Realtek rtw88)
options rtw88_core disable_lps_deep=Y
options rtw88_pci disable_aspm=Y" || true

# 3. Compressed swap in RAM: room to breathe when the model and speech share 8 GB.
# only what is missing: apt-get install of an installed package may go online to upgrade it
missing=""
for pkg in zram-tools console-setup; do dpkg -s "$pkg" >/dev/null 2>&1 || missing="$missing $pkg"; done
# shellcheck disable=SC2086
[ -n "$missing" ] && run apt-get -o DPkg::Lock::Timeout=900 install -y $missing
changed=0
set_var /etc/default/zramswap ALGO zstd && changed=1
set_var /etc/default/zramswap PERCENT 50 && changed=1
[ $changed = 1 ] && run systemctl restart zramswap

# 4. Time zone and the clock itself: Orpheus tells the time and dates from it.
run timedatectl set-timezone "$TZ_NAME"
# where NTP is blocked, the clock is set from an HTTP Date header at boot and every hour
TIME_SCRIPT="$(cd "$(dirname "$0")" && pwd)/http_time.sh"
changed=0
put /etc/systemd/system/orpheus-time.service "[Unit]
Description=Set the clock over HTTP when NTP is unreachable
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash $TIME_SCRIPT" && changed=1
put /etc/systemd/system/orpheus-time.timer "[Unit]
Description=Check the clock over HTTP

[Timer]
OnBootSec=30s
OnUnitActiveSec=1h

[Install]
WantedBy=timers.target" && changed=1
if [ $changed = 1 ]; then
  run systemctl daemon-reload
  run systemctl enable --now orpheus-time.timer
fi
run systemctl start orpheus-time.service

# 5. Cyrillic in the text console.
changed=0
set_var /etc/default/console-setup CHARMAP UTF-8 && changed=1
set_var /etc/default/console-setup CODESET CyrSlav && changed=1
set_var /etc/default/console-setup FONTFACE Terminus && changed=1
set_var /etc/default/console-setup FONTSIZE 8x16 && changed=1
[ $changed = 1 ] && { run systemctl restart console-setup.service || echo "шрифт консоли применится с перезагрузки"; }

echo "готово"
