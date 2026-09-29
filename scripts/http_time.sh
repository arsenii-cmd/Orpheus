#!/usr/bin/env bash
# Set the clock from an HTTP Date header when NTP cannot get through (some networks block UDP 123).
# Orpheus tells the date and time from this clock, and HTTPS fails when it is far off.
# Run by orpheus-time.timer (installed by scripts/laptop.sh) at boot and every hour.
set -u
if [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = yes ]; then
  exit 0
fi
for url in http://www.google.com http://ya.ru http://www.cloudflare.com; do
  stamp="$(curl -sI -m 10 "$url" | tr -d '\r' | sed -n 's/^[Dd]ate: //p' | head -1)"
  if [ -n "$stamp" ] && date -s "$stamp" >/dev/null; then
    hwclock --systohc 2>/dev/null || true  # keep it across reboots
    echo "время по $url: $(date)"
    exit 0
  fi
done
echo "не удалось узнать время ни по NTP, ни по HTTP"
exit 1
